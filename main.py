import logging
from client_manager import ClientManager
from utils import get_event_type, get_logger, init_logging
from utils.enumerations import EventType
from utils.memory_monitor import MemoryMonitor
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from handlers import handle
from pathlib import Path

init_logging(level=logging.DEBUG)
logger = get_logger(__name__)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
)

clients = ClientManager()
memory_monitor = None  # 内存监控器实例

# 前端静态文件目录配置
STATIC_DIR = Path(__file__).parent.parent / "ccg_frontend" / "dist"
logger.info(f"Static directory path: {STATIC_DIR}")

if STATIC_DIR.exists():
    logger.info(f"Frontend dist directory found: {STATIC_DIR}")
else:
    logger.warning(f"Frontend dist directory not found: {STATIC_DIR}")


@app.on_event("startup")
async def startup_event():
    """应用启动时的事件处理：启动测试音频播放任务和内存监控"""
    global memory_monitor

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


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时的事件处理：停止内存监控"""
    global memory_monitor
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
            "websocket": "/ws/",
            "memory_status": "/memory",
            "memory_report": "/memory/report",
            "health": "/health"
        }
    }


@app.websocket("/ws/")
async def websocket_endpoint(websocket: WebSocket):

    global clients
    await websocket.accept()
    logger.info("WebSocket connected: %s", websocket.client)
    clients.push(websocket)
    # asyncio.create_task(heartbeat_init(websocket))
    try:
        while True:
            data = await websocket.receive()
            if "text" in data:
                data = data["text"]
                # // use pydantic to validate and parse the incoming JSON data directly into a python object.
            elif "bytes" in data:
                data = data["bytes"]
                event: EventType = get_event_type(data)
                logger.debug("Received event: %s", event.name)
                try:
                    await handle(event, data, clients, websocket)
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
        clients.pop(websocket)
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
