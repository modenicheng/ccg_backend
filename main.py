"""FastAPI application entrypoint for CCG backend."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    FastAPI,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import update

from cache.connection import redis_client
from client_manager import ClientManager, Client
from config import app_config
from db.crud import fetch_room_object, simple_authentication
from db.models import User
from db.session import session_scope
from handlers.registe_manager import handle
from handlers.connection_lifespan import on_connect, on_disconnect
from router import (
    room_router,
    tag_router,
    song_router,
    songlist_router,
    room_songs_router,
    audio_stream_router,
)
from schemas.song import HttpErrorResponse
from schemas.ws_messages.error_schemas import WebSocketErrorEvent
from utils import get_event_type, get_logger, init_logging
from utils.enumerations import EventType, GameEventType, ErrorEventType
from utils.logger import log_level_map
from utils.memory_monitor import MemoryMonitor

log_level = log_level_map.get(app_config.log_level, logging.INFO)

init_logging(level=log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用生命周期管理：启动和关闭事件处理"""
    # 启动逻辑
    # 连接 Redis
    try:
        redis_connected = await redis_client.connect()
        if redis_connected:
            logger.info("Redis connected successfully")
        else:
            logger.warning(
                "Failed to connect to Redis, some features may be unavailable")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Error connecting to Redis: %s", exc)

    try:
        update_stmt = update(User).values(online=False)
        async with session_scope() as session:
            await session.execute(update_stmt)
        logger.info("Database user online states reset to offline on startup")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Error resetting user online states in database: %s", exc)

    # 启动内存监控
    try:
        memory_monitor = MemoryMonitor(
            interval=30.0,  # 每30秒报告一次
            report_threshold_mb=20.0,  # 内存变化超过20MB时报告
            detailed_report=True,  # 输出详细报告
        )
        await memory_monitor.start()
        _app.state.memory_monitor = memory_monitor
        logger.info("Memory monitor started successfully")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Failed to start memory monitor: %s", exc)

    # 应用运行
    yield

    # 关闭逻辑
    try:
        await _app.state.clients.clear()
        logger.info("All websocket clients closed and online states flushed")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Failed to flush websocket online state on shutdown: %s", exc)

    # 断开 Redis 连接
    try:
        await redis_client.disconnect()
        logger.info("Redis disconnected successfully")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Failed to disconnect Redis: %s", exc)

    try:
        update_stmt = update(User).values(online=False)
        async with session_scope() as session:
            await session.execute(update_stmt)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Error resetting user online states in database on shutdown: %s",
                     exc)

    # 停止内存监控
    memory_monitor = getattr(_app.state, "memory_monitor", None)
    if memory_monitor:
        try:
            await memory_monitor.stop()
            logger.info("Memory monitor stopped successfully")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Failed to stop memory monitor: %s", exc)


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
)

##############################
# Regist the API routers
##############################

app.include_router(room_router)
app.include_router(tag_router)
app.include_router(song_router)
app.include_router(songlist_router)
app.include_router(room_songs_router)
app.include_router(audio_stream_router)

##############################

app.state.clients = ClientManager()
app.state.memory_monitor = None  # 内存监控器实例

# 前端静态文件目录配置
STATIC_DIR = Path(__file__).parent.parent / "ccg_frontend" / "dist"
logger.info("Static directory path: %s", STATIC_DIR)

if STATIC_DIR.exists():
    logger.info("Frontend dist directory found: %s", STATIC_DIR)
else:
    logger.warning("Frontend dist directory not found: %s", STATIC_DIR)


@app.get("/")
async def root():
    """返回前端应用主页"""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    # 如果前端文件不存在，返回API信息
    return {
        "message": "CCG Backend Server",
        "status": "running",
        "note": "Frontend not found. Please build frontend first.",
        "static_dir": str(STATIC_DIR),
        "endpoints": {
            "websocket": "/ws/{roomid}",
            "create_room": "/api/room/",
            "room_info": "/api/room/{roomid}",
            "room_setting": "/api/room/{roomid}",
            "memory_status": "/memory",
            "memory_report": "/memory/report",
            "health": "/health",
        },
    }


@app.websocket("/ws/{roomid}")
async def websocket_endpoint(  # pylint: disable=too-many-branches,too-many-statements
    websocket: WebSocket,
    roomid: str,
):
    """Handle websocket lifecycle, authentication, and game events for a room."""
    async with session_scope() as session:
        clients_manager: ClientManager = app.state.clients
        user = await simple_authentication(session, websocket.cookies, roomid)
        room = await fetch_room_object(session, roomid)
        if not room:
            await websocket.close(code=1008, reason="Room not found")
            return

        # 如果没有身份信息，创建一个观战者客户端
        if not user:
            logger.info(f"WebSocket connection for room {roomid} as spectator")
            # 创建一个临时的观战者用户对象
            from db.models import User
            spectator_user = User(id=0,
                                  username="Spectator",
                                  token="",
                                  room_id=roomid,
                                  is_owner=False)
            client = Client(websocket, spectator_user, room)
        else:
            client = Client(websocket, user, room)

        await client.ws.accept()

        logger.info("WebSocket connected: %s", websocket.client)
        clients_manager.push(roomid, client)

        await on_connect(session, client, clients_manager, roomid)

    try:
        while True:
            data = await client.ws.receive()
            if data.get("type") == "websocket.disconnect":
                logger.info(
                    "WebSocket disconnect event received from %s: code=%s reason=%s",
                    client.ws.client,
                    data.get("code"),
                    data.get("reason", ""),
                )
                break

            if "text" in data:
                payload_text = data["text"]
                try:
                    parsed_data = json.loads(payload_text)
                except json.JSONDecodeError as exc:
                    await client.ws.send_json(
                        WebSocketErrorEvent(
                            error_event=ErrorEventType.INVALID_JSON,
                            message=f"Invalid JSON payload: {exc}",
                        ).model_dump())
                    continue

                event_value = parsed_data.get("event")
                if not isinstance(event_value, int):
                    await client.ws.send_json(
                        WebSocketErrorEvent(
                            error_event=ErrorEventType.MISSING_EVENT_FIELD,
                            message="Missing event field",
                        ).model_dump())
                    continue

                try:
                    event = GameEventType(event_value)
                except ValueError:
                    await client.ws.send_json(
                        WebSocketErrorEvent(
                            error_event=ErrorEventType.UNSUPPORTED_EVENT,
                            message=f"Unsupported game event: {event_value}",
                        ).model_dump())
                    continue

            elif "bytes" in data:
                parsed_data = data["bytes"]
                event: EventType | GameEventType = get_event_type(parsed_data)

            else:
                logger.warning("Received unsupported data type from %s: %s",
                               client.ws.client, data)
                continue

            try:
                await handle(event,
                             parsed_data,
                             clients=clients_manager,
                             client=client,
                             room_id=roomid)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error("Failed to handle event: %s, error: %s",
                             event.name,
                             exc,
                             exc_info=True)
                await client.ws.send_json(
                    WebSocketErrorEvent(
                        error_event=ErrorEventType.HANDLER_EXCEPTION,
                        message=f"Failed to handle event {event.name}: {exc}",
                    ).model_dump())

    except (WebSocketDisconnect, RuntimeError):
        logger.info("WebSocket disconnected: %s", client.ws.client)
    finally:
        try:
            await client.ws.close()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        clients_manager.pop(roomid, client)
        logger.info("WebSocket removed from clients: %s", client.ws.client)
        async with session_scope() as session:
            await on_disconnect(session, client, clients_manager, roomid)


@app.websocket("/ws/{roomid}/watch")
async def websocket_watch_endpoint(  # pylint: disable=too-many-branches,too-many-statements
    websocket: WebSocket,
    roomid: str,
):
    """Handle websocket lifecycle for spectator mode."""
    async with session_scope() as session:
        clients_manager: ClientManager = app.state.clients
        room = await fetch_room_object(session, roomid)
        if not room:
            await websocket.close(code=1008, reason="Room not found")
            return

        # 直接创建一个观战者客户端
        logger.info(
            f"WebSocket connection for room {roomid} as spectator (watch endpoint)")
        from db.models import User
        spectator_user = User(id=0,
                              username="Spectator",
                              token="",
                              room_id=roomid,
                              is_owner=False)
        client = Client(websocket, spectator_user, room)

        await client.ws.accept()

        logger.info("WebSocket connected: %s", websocket.client)
        clients_manager.push(roomid, client)

        await on_connect(session, client, clients_manager, roomid)

    try:
        while True:
            data = await client.ws.receive()
            if data.get("type") == "websocket.disconnect":
                logger.info(
                    "WebSocket disconnect event received from %s: code=%s reason=%s",
                    client.ws.client,
                    data.get("code"),
                    data.get("reason", ""),
                )
                break

            # 观战者不需要处理任何事件，只接收消息
            logger.debug("Spectator received message, ignoring: %s", data)

    except (WebSocketDisconnect, RuntimeError):
        logger.info("WebSocket disconnected: %s", client.ws.client)
    finally:
        try:
            await client.ws.close()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        clients_manager.pop(roomid, client)
        logger.info("WebSocket removed from clients: %s", client.ws.client)
        async with session_scope() as session:
            await on_disconnect(session, client, clients_manager, roomid)


# Catch-all 路由：处理前端的客户端路由（必须放在所有路由的最后）
@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    """处理所有其他路由，返回静态文件或前端应用（用于 SPA 客户端路由）"""
    # 尝试返回请求的静态文件
    file_path = STATIC_DIR / full_path
    logger.debug("Requested path: %s, Resolved to: %s", full_path, file_path)

    # 防目录越界检查：确保解析后的路径在 STATIC_DIR 内
    try:
        # 获取规范化路径（解析 .. 等相对路径）
        resolved_path = file_path.resolve()
        static_dir_resolved = STATIC_DIR.resolve()

        # 确保 resolved_path 在 static_dir_resolved 目录内
        if not str(resolved_path).startswith(str(static_dir_resolved)):
            logger.warning("Path traversal attempt detected: %s -> %s", full_path,
                           resolved_path)
            # 返回 index.html（作为安全降级）
            raise HTTPException(status_code=404, detail="Not found")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Path resolution error: %s", exc)
        return HttpErrorResponse(error="Invalid path", detail=None, path=full_path)

    if file_path.exists() and file_path.is_file():
        logger.debug("Serving static file: %s", file_path)
        return FileResponse(file_path)

    # 检查是否是目录（目录访问重定向到 index.html）
    if file_path.exists() and file_path.is_dir():
        index_file = file_path / "index.html"
        if index_file.exists():
            logger.debug("Serving index.html from directory: %s", file_path)
            return FileResponse(index_file)

    # 如果文件不存在，返回 index.html（用于前端客户端路由）
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        logger.debug("File not found, serving index.html for client-side routing: %s",
                     full_path)
        return FileResponse(index_file)

    # 前端文件不存在
    logger.warning("Not found: %s, index.html also not found", full_path)
    return HttpErrorResponse(error="Not found", detail=None, path=full_path)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
