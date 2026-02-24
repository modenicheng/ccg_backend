import logging
import json
import time
import secrets
import string
from uuid import uuid4
from client_manager import ClientManager
from cache.connection import redis_client
from cache.utils import RedisKeys, room_manager, session_manager
from db.session import get_db_session
from db import crud
from db.cache_sync import CacheSyncManager
from utils import get_event_type, get_logger, init_logging
from utils.enumerations import EventType
from utils.memory_monitor import MemoryMonitor
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from handlers import handle, handle_json
from handlers.game_sync import send_room_state
from schemas import CreateRoomRequest, CreateRoomResponse, PatchRoomRequest, RoomInfoResponse
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession

init_logging(level=logging.DEBUG)
logger = get_logger(__name__)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
)

clients = ClientManager()
memory_monitor = None  # 内存监控器实例
room_router = APIRouter(prefix="/api/room", tags=["room"])

# 前端静态文件目录配置
STATIC_DIR = Path(__file__).parent.parent / "ccg_frontend" / "dist"
logger.info(f"Static directory path: {STATIC_DIR}")

if STATIC_DIR.exists():
    logger.info(f"Frontend dist directory found: {STATIC_DIR}")
else:
    logger.warning(f"Frontend dist directory not found: {STATIC_DIR}")


def generate_room_id(length: int = 6) -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


async def _periodic_cleanup_task():
    """
    定期清理任务：检查并同步即将过期的房间
    每 30 分钟运行一次
    """
    import asyncio
    
    while True:
        try:
            await asyncio.sleep(30 * 60)  # 每 30 分钟运行一次
            cleaned = await CacheSyncManager.cleanup_expired_rooms()
            if cleaned > 0:
                logger.info(f"Periodic cleanup: synced {cleaned} rooms")
        except asyncio.CancelledError:
            logger.info("Periodic cleanup task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in periodic cleanup task: {e}")
            # 继续运行，不要因为错误就停止定时任务


async def get_room_info_payload(roomid: str) -> RoomInfoResponse:
    redis = await redis_client.get_client()
    if not redis:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    room_key = RedisKeys.room(roomid)
    room_data = await redis.hgetall(room_key)
    
    # 如果 Redis 中没有房间数据，尝试从数据库恢复
    if not room_data:
        logger.info(f"Room {roomid} not found in Redis, attempting to restore from database")
        restored = await CacheSyncManager.restore_room_from_db(roomid)
        if not restored:
            raise HTTPException(status_code=404, detail="Room not found")
        
        # 尝试再次从 Redis 读取
        room_data = await redis.hgetall(room_key)
        if not room_data:
            raise HTTPException(status_code=404, detail="Room not found after restore")

    players = sorted(list(await room_manager.get_room_players(roomid)))
    song_queue = await room_manager.get_song_queue(roomid)

    tag_groups_raw = room_data.get("tag_groups", "{}")
    try:
        tag_groups = json.loads(tag_groups_raw) if tag_groups_raw else {}
    except json.JSONDecodeError:
        tag_groups = {}

    play_progress = int(room_data.get("play_progress", 0) or 0)

    return RoomInfoResponse(
        roomId=roomid,
        hostPlayerId=room_data.get("host_player_id", ""),
        status=room_data.get("status", "waiting"),
        title=room_data.get("title"),
        description=room_data.get("description"),
        players=players,
        songQueue=song_queue,
        tagGroups=tag_groups,
        playProgress=play_progress,
    )


@room_router.post("/", response_model=CreateRoomResponse)
async def create_room(payload: CreateRoomRequest, session: AsyncSession = Depends(get_db_session)) -> CreateRoomResponse:
    redis = await redis_client.get_client()
    if not redis:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    for _ in range(10):
        room_id = generate_room_id()
        room_key = RedisKeys.room(room_id)
        exists = await redis.exists(room_key)
        if exists:
            continue

        player_id = uuid4().hex[:12]
        username = payload.username.strip()
        if not username:
            raise HTTPException(status_code=422, detail="username cannot be empty")
        token = secrets.token_urlsafe(24)

        try:
            # 1. 先在数据库中创建房间
            db_room = await crud.create_room_in_db(session, room_id)
            if not db_room:
                logger.warning(f"Failed to create room in database: {room_id}")
                continue

            # 2. 添加房主用户到数据库
            db_user = await crud.add_user_to_room(
                session,
                room_id,
                player_id=player_id,
                username=username,
                is_owner=True,
            )
            if not db_user:
                logger.warning(f"Failed to add user to database: {room_id}, {player_id}")
                continue

            # 3. 在 Redis 中创建房间
            created = await room_manager.create_room(room_id, player_id)
            if not created:
                logger.warning(f"Failed to create room in Redis: {room_id}")
                continue

            # 4. 创建会话
            await session_manager.create_session(token, room_id, player_id)
            
            logger.info(f"Room created successfully: {room_id}, player: {player_id}")
            return CreateRoomResponse(roomId=room_id, playerId=player_id, token=token)
        
        except Exception as e:
            logger.error(f"Error creating room: {e}")
            continue

    raise HTTPException(status_code=500, detail="Failed to create room after retries")


@room_router.get("/{roomid}", response_model=RoomInfoResponse)
async def room_info(roomid: str) -> RoomInfoResponse:
    return await get_room_info_payload(roomid)


@room_router.patch("/{roomid}", response_model=RoomInfoResponse)
async def room_setting(roomid: str, payload: PatchRoomRequest) -> RoomInfoResponse:
    redis = await redis_client.get_client()
    if not redis:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    room_key = RedisKeys.room(roomid)
    room_data = await redis.hgetall(room_key)
    if not room_data:
        raise HTTPException(status_code=404, detail="Room not found")

    updates: dict[str, str | int] = {}

    if payload.songQueue is not None:
        await room_manager.set_song_queue(roomid, payload.songQueue)

    if payload.title is not None:
        updates["title"] = payload.title

    if payload.description is not None:
        updates["description"] = payload.description

    if payload.tagGroups is not None:
        updates["tag_groups"] = json.dumps(payload.tagGroups, ensure_ascii=False)

    if updates:
        await redis.hset(room_key, mapping=updates)

    return await get_room_info_payload(roomid)


app.include_router(room_router)


@app.on_event("startup")
async def startup_event():
    """应用启动时的事件处理：启动测试音频播放任务和内存监控"""
    global memory_monitor

    # 连接 Redis
    try:
        redis_connected = await redis_client.connect()
        if redis_connected:
            logger.info("Redis connected successfully")
        else:
            logger.warning("Failed to connect to Redis, some features may be unavailable")
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
    
    # 启动定期清理任务（检查即将过期的房间并同步到 DB）
    try:
        import asyncio
        asyncio.create_task(_periodic_cleanup_task())
        logger.info("Periodic cleanup task started")
    except Exception as e:
        logger.error(f"Failed to start periodic cleanup task: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时的事件处理：停止内存监控并同步所有活跃房间"""
    global memory_monitor
    
    # 同步所有活跃房间到数据库
    try:
        synced_count = await CacheSyncManager.sync_all_active_rooms()
        logger.info(f"Synced {synced_count} active rooms on shutdown")
    except Exception as e:
        logger.error(f"Error syncing rooms on shutdown: {e}")
    
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
async def websocket_endpoint(
    websocket: WebSocket,
    roomid: str,
    token: str = Query(...),
):

    global clients
    await websocket.accept()

    redis = await redis_client.get_client()
    if not redis:
        await websocket.close(code=1013, reason="Service unavailable")
        return

    room_exists = await redis.exists(RedisKeys.room(roomid))
    if not room_exists:
        await websocket.close(code=1008, reason="Room not found")
        return

    session = await session_manager.get_session(token)
    if not session or session.get("room_id") != roomid:
        await websocket.close(code=1008, reason="Invalid room session")
        return

    player_id = session.get("player_id")
    if not player_id:
        await websocket.close(code=1008, reason="Invalid player session")
        return

    logger.info("WebSocket connected: %s", websocket.client)
    clients.push(roomid, websocket)
    await send_room_state(roomid, clients, websocket)
    # asyncio.create_task(heartbeat_init(websocket))
    try:
        while True:
            data = await websocket.receive()
            if "text" in data:
                data = data["text"]
                try:
                    payload = json.loads(data)
                    event_value = int(payload.get("event"))
                    event = EventType(event_value)
                    payload_data = payload.get("data") or {}
                    if not isinstance(payload_data, dict):
                        raise ValueError("payload.data must be object")
                    if payload.get("request_id"):
                        payload_data["request_id"] = payload.get("request_id")
                    await handle_json(event,
                                      payload_data,
                                      clients=clients,
                                      websocket=websocket,
                                      room_id=roomid,
                                      player_id=player_id)
                except Exception as e:
                    logger.warning("Failed to handle JSON message: %s", e)
                    await clients.send(websocket, {
                        "v": 1,
                        "event": EventType.MESSAGE.value,
                        "ts": int(time.time() * 1000),
                        "data": {
                            "ok": False,
                            "error": {
                                "code": "INVALID_MESSAGE",
                                "message": str(e),
                            },
                        },
                    })
            elif "bytes" in data:
                data = data["bytes"]
                event: EventType = get_event_type(data)
                logger.debug("Received event: %s", event.name)
                try:
                    await handle(event,
                                 data,
                                 clients=clients,
                                 websocket=websocket,
                                 room_id=roomid,
                                 player_id=player_id)
                except Exception as e:
                    logger.warning(
                        f"Failed to handle event: {event.name}, error: {e}")
                    await clients.send(websocket, {
                        "v": 1,
                        "event": EventType.MESSAGE.value,
                        "ts": int(time.time() * 1000),
                        "data": {
                            "ok": False,
                            "error": {
                                "code": "EVENT_HANDLE_FAILED",
                                "message": f"Failed to handle event: {event.name}, error: {e}",
                            },
                        },
                    })
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
        return {"error": "Invalid path"}

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
    return {"error": "Not found", "path": full_path}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
