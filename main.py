import logging
import orjson
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from db.crud import fetch_room_object, simple_authentication
from db.models import User, Room, TagGroup
from db.session import get_db
from uuid import uuid4
from client_manager import ClientManager, Client
from cache.connection import redis_client
from handlers.game_events import on_connect, on_disconnect
from utils import get_event_type, get_logger, init_logging
from utils.enumerations import EventType, GameEventType, ErrorEventType
from schemas.ws_messages.error_schemas import WebSocketErrorEvent
from utils.memory_monitor import MemoryMonitor
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from handlers import handle
from pathlib import Path
from schemas.song import HttpErrorResponse
from router import *
from utils.logger import log_level_map
import os

log_level = log_level_map.get(
    os.getenv("CCG_LOG_LEVEL", "INFO").upper(), logging.INFO)

init_logging(level=log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI, session: AsyncSession = Depends(get_db)):
    """应用生命周期管理：启动和关闭事件处理"""
    global memory_monitor
    global clients

    # 启动逻辑
    # 连接 Redis
    try:
        redis_connected = await redis_client.connect()
        if redis_connected:
            logger.info("Redis connected successfully")
        else:
            logger.warning(
                "Failed to connect to Redis, some features may be unavailable")
    except Exception as e:
        logger.error(f"Error connecting to Redis: {e}")

    try:
        update_stmt = update(User).values(online=False)
        await session.execute(update_stmt)
        await session.commit()
        logger.info("Database user online states reset to offline on startup")
    except Exception as e:
        logger.error(f"Error resetting user online states in database: {e}")

    # 启动内存监控
    try:
        memory_monitor = MemoryMonitor(
            interval=30.0,  # 每30秒报告一次
            report_threshold_mb=20.0,  # 内存变化超过20MB时报告
            detailed_report=True,  # 输出详细报告
        )
        await memory_monitor.start()
        logger.info("Memory monitor started successfully")
    except Exception as e:
        logger.error(f"Failed to start memory monitor: {e}")

    # 应用运行
    yield

    # 关闭逻辑
    try:
        await clients.clear()
        logger.info("All websocket clients closed and online states flushed")
    except Exception as e:
        logger.error(
            f"Failed to flush websocket online state on shutdown: {e}")

    # 断开 Redis 连接
    try:
        await redis_client.disconnect()
        logger.info("Redis disconnected successfully")
    except Exception as e:
        logger.error(f"Failed to disconnect Redis: {e}")

    # 停止内存监控
    if memory_monitor:
        try:
            await memory_monitor.stop()
            logger.info("Memory monitor stopped successfully")
        except Exception as e:
            logger.error(f"Failed to stop memory monitor: {e}")


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

##############################

clients = ClientManager()
memory_monitor = None  # 内存监控器实例

# 前端静态文件目录配置
STATIC_DIR = Path(__file__).parent.parent / "ccg_frontend" / "dist"
logger.info(f"Static directory path: {STATIC_DIR}")

if STATIC_DIR.exists():
    logger.info(f"Frontend dist directory found: {STATIC_DIR}")
else:
    logger.warning(f"Frontend dist directory not found: {STATIC_DIR}")


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
            "health": "/health"
        }
    }


@app.websocket("/ws/{roomid}")
async def websocket_endpoint(websocket: WebSocket,
                             roomid: str,
                             session: AsyncSession = Depends(get_db)):

    global clients
    user = await simple_authentication(session, websocket.cookies, roomid)
    if not user:
        await websocket.close(code=1008, reason="Authentication failed")
        return
    room = await fetch_room_object(session, roomid)
    if not room:
        await websocket.close(code=1008, reason="Room not found")
        return

    client = Client(websocket, user, room)

    await client.ws.accept()

    logger.info("WebSocket connected: %s", websocket.client)
    clients.push(roomid, client)

    await on_connect(session, client, clients, roomid)

    try:
        while True:
            data = await client.ws.receive()
            if "text" in data:
                payload_text = data["text"]
                try:
                    parsed_data = orjson.loads(payload_text)
                except orjson.JSONDecodeError as e:
                    await client.ws.send_json(
                        WebSocketErrorEvent(
                            error_event=ErrorEventType.INVALID_JSON,
                            message=f"Invalid JSON payload: {e}").model_dump())
                    continue

                event_value = parsed_data.get("event")
                if not isinstance(event_value, int):
                    await client.ws.send_json(
                        WebSocketErrorEvent(
                            error_event=ErrorEventType.MISSING_EVENT_FIELD,
                            message="Missing event field").model_dump())
                    continue

                try:
                    event = GameEventType(event_value)
                except ValueError:
                    await client.ws.send_json(
                        WebSocketErrorEvent(
                            error_event=ErrorEventType.UNSUPPORTED_EVENT,
                            message=f"Unsupported game event: {event_value}").
                        model_dump())
                    continue

            elif "bytes" in data:
                parsed_data = data["bytes"]
                event: EventType | GameEventType = get_event_type(parsed_data)

            else:
                logger.warning(
                    f"Received unsupported data type from {client.ws.client}: {data}"
                )
                continue

            try:
                await handle(event,
                             parsed_data,
                             clients=clients,
                             client=client,
                             room_id=roomid)
            except Exception as e:
                logger.error(
                    f"Failed to handle event: {event.name}, error: {e}",
                    exc_info=True)
                await client.ws.send_json(
                    WebSocketErrorEvent(
                        error_event=ErrorEventType.HANDLER_EXCEPTION,
                        message=f"Failed to handle event {event.name}: {e}").
                    model_dump())

    except (WebSocketDisconnect, RuntimeError) as e:
        logger.info("WebSocket disconnected: %s", client.ws.client)
    finally:
        try:
            await client.ws.close()
        except Exception:
            pass
        clients.pop(roomid, client)
        logger.info("WebSocket removed from clients: %s", client.ws.client)
        await on_disconnect(session, client, clients, roomid)


# Catch-all 路由：处理前端的客户端路由（必须放在所有路由的最后）
@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    """处理所有其他路由，返回静态文件或前端应用（用于 SPA 客户端路由）"""
    # 尝试返回请求的静态文件
    file_path = STATIC_DIR / full_path
    logger.debug(f"Requested path: {full_path}, Resolved to: {file_path}")

    # 防目录越界检查：确保解析后的路径在 STATIC_DIR 内
    try:
        # 获取规范化路径（解析 .. 等相对路径）
        resolved_path = file_path.resolve()
        static_dir_resolved = STATIC_DIR.resolve()

        # 确保 resolved_path 在 static_dir_resolved 目录内
        if not str(resolved_path).startswith(str(static_dir_resolved)):
            logger.warning(
                f"Path traversal attempt detected: {full_path} -> {resolved_path}"
            )
            # 返回 index.html（作为安全降级）
            raise HTTPException(status_code=404, detail="Not found")
    except Exception as e:
        logger.warning(f"Path resolution error: {e}")
        return HttpErrorResponse(error="Invalid path",
                                 detail=None,
                                 path=full_path)

    if file_path.exists() and file_path.is_file():
        logger.debug(f"Serving static file: {file_path}")
        return FileResponse(file_path)

    # 检查是否是目录（目录访问重定向到 index.html）
    if file_path.exists() and file_path.is_dir():
        index_file = file_path / "index.html"
        if index_file.exists():
            logger.debug(f"Serving index.html from directory: {file_path}")
            return FileResponse(index_file)

    # 如果文件不存在，返回 index.html（用于前端客户端路由）
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        logger.debug(
            f"File not found, serving index.html for client-side routing: {full_path}"
        )
        return FileResponse(index_file)

    # 前端文件不存在
    logger.warning(f"Not found: {full_path}, index.html also not found")
    return HttpErrorResponse(error="Not found", detail=None, path=full_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
