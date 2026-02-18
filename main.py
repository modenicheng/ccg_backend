from email.mime import audio
import logging
import time
from client_manager import ClientManager
from utils import get_event_type, get_logger, init_logging
from utils.dataframe import AudioFrame, AudioEncoding
from utils.enumerations import EventType
from utils.memory_monitor import MemoryMonitor
from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from handlers import handle
import asyncio
import datetime
from pydub import AudioSegment

init_logging(level=logging.DEBUG)
logger = get_logger(__name__)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
)

clients = ClientManager()
memory_monitor = None  # 内存监控器实例

async def test_pcm_audio():
    """测试函数：解码FLAC并循环播放，发送PCM音频数据到所有客户端。"""
    global clients
    audio_file = "D:\\QQMusicDownloads\\HOYO-MiX\\崩坏星穹铁道-星空剧场2 Astral Theater Vol_2\\初花学剑动星芒 My Sword Stirs Starlight - HOYO-MiX.flac"
    
    # 加载并解码FLAC文件
    logger.info(f"Loading audio file: {audio_file}")
    audio: AudioSegment = AudioSegment.from_file(audio_file, format="flac")
    
    # 获取音频参数
    sample_rate: int = audio.frame_rate
    channels: int = audio.channels
    sample_width: int = audio.sample_width  # 字节数
    
    if not audio.raw_data:
        logger.warning("Audio file loaded but contains no data.")
        return
    # 转换为PCM原始数据
    pcm_data: bytes = audio.raw_data
    
    logger.info(f"Audio loaded - Sample rate: {sample_rate}Hz, Channels: {channels}, Sample width: {sample_width} bytes, Duration: {len(audio)}ms")
    
    # 计算每次发送的数据块大小（例如：每50ms发送一次）
    chunk_duration_ms = 50
    bytes_per_ms = (sample_rate * channels * sample_width) // 1000
    chunk_size = bytes_per_ms * chunk_duration_ms
    
    position = 0
    
    # 循环播放
    while True:
        t1 = time.perf_counter_ns()
        # 提取当前块
        chunk_data = pcm_data[position:position + chunk_size]
        
        # 如果到达末尾，从头开始（循环播放）
        if len(chunk_data) < chunk_size:
            position = 0
            chunk_data = pcm_data[position:position + chunk_size]
        
        # 计算实际采样数
        sample_num = len(chunk_data) // (channels * sample_width)
        
        # 创建音频帧并广播
        audio_frame = AudioFrame(
            sample_rate=sample_rate,
            sample_num=sample_num,
            channels=channels,
            encoding=AudioEncoding.PCM,
            data=chunk_data
        )
        await clients.broadcast(audio_frame.bin)
        
        # 移动播放位置
        position += chunk_size
        t2 = time.perf_counter_ns()
        elapsed_ms = (t2 - t1) / 1_000_000
        # 按实际播放速度等待
        await asyncio.sleep(((chunk_duration_ms - (elapsed_ms * 4)) / 1000))

@app.on_event("startup")
async def startup_event():
    """应用启动时的事件处理：启动测试音频播放任务和内存监控"""
    global memory_monitor
    
    # 启动测试音频播放任务
    asyncio.create_task(test_pcm_audio())
    logger.info("Test PCM audio playback task started")
    
    # 启动内存监控
    try:
        memory_monitor = MemoryMonitor(
            interval=30.0,           # 每30秒报告一次
            report_threshold_mb=20.0,  # 内存变化超过20MB时报告
            detailed_report=True,    # 输出详细报告
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
    """根路由，显示应用状态"""
    return {
        "message": "CCG Backend Server",
        "status": "running",
        "features": ["WebSocket audio streaming", "Memory monitoring"],
        "endpoints": {
            "websocket": "/ws/",
            "memory_status": "/memory",
            "memory_report": "/memory/report",
            "health": "/health"
        }
    }


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "healthy", "timestamp": datetime.datetime.now().isoformat()}


@app.get("/memory")
async def get_memory_status():
    """获取当前内存状态"""
    global memory_monitor
    
    if not memory_monitor:
        return {"error": "Memory monitor not initialized", "status": "not_available"}
    
    try:
        memory_info = memory_monitor._get_memory_info()
        return {
            "status": "available",
            "process_memory_mb": {
                "rss": round(memory_info["rss_mb"], 2),
                "vms": round(memory_info["vms_mb"], 2),
            },
            "system_memory_mb": {
                "total": round(memory_info["system_total_mb"], 2),
                "used": round(memory_info["system_used_mb"], 2),
                "available": round(memory_info["system_available_mb"], 2),
                "percent": round(memory_info["system_percent"], 1),
            },
            "monitor_config": {
                "interval": memory_monitor.interval,
                "report_threshold_mb": memory_monitor.report_threshold_mb,
                "detailed_report": memory_monitor.detailed_report,
                "is_running": memory_monitor._running if hasattr(memory_monitor, '_running') else False
            },
            "timestamp": memory_info["timestamp"],
            "formatted_time": datetime.datetime.fromtimestamp(memory_info["timestamp"]).isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to get memory status: {e}")
        return {"error": str(e), "status": "error"}


@app.get("/memory/report")
async def get_memory_report():
    """获取格式化的内存报告"""
    global memory_monitor
    
    if not memory_monitor:
        return {"error": "Memory monitor not initialized", "status": "not_available"}
    
    try:
        memory_info = memory_monitor._get_memory_info()
        report = memory_monitor._format_memory_report(memory_info)
        return {
            "status": "available",
            "report": report,
            "timestamp": memory_info["timestamp"],
            "formatted_time": datetime.datetime.fromtimestamp(memory_info["timestamp"]).isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to get memory report: {e}")
        return {"error": str(e), "status": "error"}


@app.websocket("/ws/")
async def websocket_endpoint(websocket: WebSocket):

    global clients
    await websocket.accept()
    logger.info("WebSocket connected: %s", websocket.client)
    clients.push(websocket)
    # asyncio.create_task(heartbeat_init(websocket))
    try:
        while True:
            try:
                data = await websocket.receive_bytes()
            except KeyError:
                logger.warning(f"WebSocket disconnected: {websocket.client}. The data is not in bytes format.")
                break
            event: EventType = get_event_type(data)
            logger.debug("Received event: %s", event.name)
            try:
                await handle(event, data, clients, websocket)
            except Exception as e:
                logger.warning(f"Failed to handle event: {event.name}, error: {e}")
                await clients.send(websocket, f"Failed to handle event: {event.name}, error: {e}")
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: %s", websocket.client)
    finally:
        clients.pop(websocket)
        logger.info("WebSocket removed from clients: %s", websocket.client)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
