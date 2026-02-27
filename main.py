import logging
import json
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from db.models import User, Room, TagGroup
from db.session import get_db
from uuid import uuid4
from client_manager import ClientManager
from cache.connection import redis_client
from cache.utils import RedisKeys, room_manager, session_manager
from utils import get_event_type, get_logger, init_logging
from utils.enumerations import EventType, GameEventType
from utils.memory_monitor import MemoryMonitor
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from handlers import handle, handle_json
from pathlib import Path
from schemas.room import RoomStateInitMessage, RoomStateInitData, RoomStatePlayerItem, RoomStateTagGroupItem, RoomStateTagItem
from schemas.song import HttpErrorResponse

from router import *

init_logging(level=logging.DEBUG)
logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动和关闭事件处理"""
    global memory_monitor

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


def build_roomstate_init_payload(room: Room) -> RoomStateInitMessage:
    host_user = next((u for u in room.users if u.is_owner), None)

    tag_groups: list[RoomStateTagGroupItem] = []
    unique_tags: dict[int, RoomStateTagItem] = {}
    for group in room.tag_groups:
        group_tags: list[RoomStateTagItem] = []
        for tag in group.tags:
            tag_item = RoomStateTagItem(
                id=tag.id,
                name=tag.name,
            )
            group_tags.append(tag_item)
            unique_tags[tag.id] = tag_item

        tag_groups.append(
            RoomStateTagGroupItem(
                id=group.id,
                name=group.name,
                description=group.description,
                tags=group_tags,
            ))

    message = RoomStateInitMessage(data=RoomStateInitData(
        room_id=room.id,
        title=room.title,
        status=int(room.status),
        host=host_user.username if host_user else None,
        owner=host_user.username if host_user else None,
        host_player_id=str(host_user.id) if host_user else "",
        players=[
            RoomStatePlayerItem(
                id=u.id,
                username=u.username,
                is_owner=u.is_owner,
            ) for u in room.users
        ],
        tag_groups=tag_groups,
        tags=list(unique_tags.values()),
    ))

    return message




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
    logger.debug(websocket.cookies)

    user_token = websocket.cookies.get(f"ccg-room-token:{roomid}")
    user_id = websocket.cookies.get(f"ccg-room-user-id:{roomid}")
    username = websocket.cookies.get(f"ccg-room-username:{roomid}")

    if not user_token or not user_id or not username:
        logger.warning(
            f"WebSocket connection missing authentication cookies for room {roomid}. user_token: {user_token}, user_id: {user_id}, username: {username}"
        )
        await websocket.close(code=1008, reason="Authentication required")
        return

    stmt = select(User).where(User.id == int(user_id))
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if not user or user.token != user_token or user.room_id != roomid:
        logger.warning(
            f"WebSocket authentication failed for room {roomid}. user_id: {user_id}, token valid: {user.token == user_token if user else 'N/A'}, room_id valid: {user.room_id == roomid if user else 'N/A'}"
        )
        await websocket.close(code=1008, reason="Invalid authentication")
        return

    logger.info(
        f"WebSocket connection attempt for room {roomid} with token: {user_token}, user_id: {user_id}, username: {username}"
    )

    await websocket.accept()
    websocket.state.user = user

    logger.info("WebSocket connected: %s", websocket.client)
    clients.push(roomid, websocket)

    room_stmt = (select(Room).where(Room.id == roomid).options(
        selectinload(Room.users),
        selectinload(Room.tag_groups).selectinload(TagGroup.tags),
    ))
    room_result = await session.execute(room_stmt)
    room = room_result.scalar_one_or_none()
    if not room:
        await websocket.close(code=1008, reason="Room not found")
        return

    init_payload = build_roomstate_init_payload(room)
    await websocket.send_text(init_payload.model_dump_json())

    # asyncio.create_task(heartbeat_init(websocket))
    try:
        while True:
            data = await websocket.receive()
            if "text" in data:
                payload_text = data["text"]
                try:
                    payload_json = json.loads(payload_text)
                except json.JSONDecodeError as e:
                    await clients.send(websocket, {
                        "type": "error",
                        "reason": f"Invalid JSON payload: {e}"
                    })
                    continue

                event_value = payload_json.get("event")
                if not isinstance(event_value, int):
                    await clients.send(websocket, {
                        "type": "error",
                        "reason": "Missing event field"
                    })
                    continue

                try:
                    game_event = GameEventType(event_value)
                except ValueError:
                    await clients.send(
                        websocket, {
                            "type": "error",
                            "reason": f"Unsupported game event: {event_value}"
                        })
                    continue

                try:
                    await handle_json(
                        game_event,
                        payload_json,
                        clients=clients,
                        websocket=websocket,
                        room_id=roomid,
                        user=user,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to handle JSON event: %s, error: %s",
                        game_event.name,
                        e,
                    )
                    await clients.send(
                        websocket, {
                            "type": "error",
                            "event": game_event.value,
                            "reason": f"Failed to handle event: {e}"
                        })
            elif "bytes" in data:
                data = data["bytes"]
                event: EventType = get_event_type(data)
                try:
                    await handle(event,
                                 data,
                                 clients=clients,
                                 websocket=websocket,
                                 room_id=roomid)
                except Exception as e:
                    logger.warning(
                        f"Failed to handle event: {event.name}, error: {e}")
                    await clients.send(
                        websocket,
                        f"Failed to handle event: {event.name}, error: {e}")
            else:
                logger.warning(
                    f"Received unsupported data type from {websocket.client}: {data}"
                )
                continue

    except (WebSocketDisconnect, RuntimeError) as e:
        logger.info("WebSocket disconnected: %s", websocket.client)
    finally:
        clients.pop(roomid, websocket)
        logger.info("WebSocket removed from clients: %s", websocket.client)


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
