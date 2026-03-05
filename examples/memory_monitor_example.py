#!/usr/bin/env python3
from __future__ import annotations
"""
内存监控使用示例
展示如何在FastAPI应用中使用内存监控功能
"""
import asyncio
import logging
from fastapi import FastAPI
import uvicorn

from utils import init_logging, get_logger
from utils.memory_monitor import (
    MemoryMonitor,
    start_memory_monitoring,
    periodic_memory_report,
)

# 初始化日志
init_logging(level=logging.INFO)
logger = get_logger(__name__)

app = FastAPI()


@app.on_event("startup")
async def startup_event():
    """应用启动时启动内存监控"""
    # 方法1: 使用MemoryMonitor类
    global memory_monitor
    memory_monitor = MemoryMonitor(
        interval=30.0,  # 每30秒报告一次
        report_threshold_mb=50.0,  # 内存变化超过50MB时报告
        detailed_report=True,
    )
    await memory_monitor.start()
    logger.info("内存监控器已启动")

    # 方法2: 也可以使用便捷函数
    # global memory_monitor2
    # memory_monitor2 = await start_memory_monitoring(
    #     interval=60.0,
    #     report_threshold_mb=100.0,
    #     detailed_report=False,
    # )

    # 方法3: 或者启动简单的定时报告
    # asyncio.create_task(periodic_memory_report(
    #     interval=60.0,
    #     detailed=False,
    #     callback=lambda info: logger.info(f"内存回调: {info['rss_mb']:.1f}MB")
    # ))


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时停止内存监控"""
    if "memory_monitor" in globals():
        await memory_monitor.stop()
        logger.info("内存监控器已停止")

    # if 'memory_monitor2' in globals():
    #     await memory_monitor2.stop()


@app.get("/")
async def root():
    """根路由"""
    return {"message": "内存监控示例应用", "status": "running"}


@app.get("/memory")
async def get_memory_info():
    """获取当前内存信息"""
    monitor = MemoryMonitor()
    memory_info = monitor._get_memory_info()

    return {
        "process_memory_mb": {
            "rss": memory_info["rss_mb"],
            "vms": memory_info["vms_mb"],
        },
        "system_memory_mb": {
            "total": memory_info["system_total_mb"],
            "used": memory_info["system_used_mb"],
            "available": memory_info["system_available_mb"],
            "percent": memory_info["system_percent"],
        },
        "timestamp": memory_info["timestamp"],
    }


@app.get("/memory/report")
async def get_memory_report():
    """获取格式化的内存报告"""

    monitor = MemoryMonitor(detailed_report=True)
    memory_info = monitor._get_memory_info()
    report = monitor._format_memory_report(memory_info)

    return {"report": report}


async def demo_manual_usage():
    """演示手动使用内存监控"""
    logger.info("演示手动使用内存监控...")

    # 1. 使用上下文管理器（推荐）
    monitor = MemoryMonitor(interval=5.0, report_threshold_mb=10.0)

    async with monitor.monitor_context():
        logger.info("在监控上下文中执行一些操作...")
        # 模拟一些内存操作
        data = [bytearray(1024 * 1024) for _ in range(10)]  # 分配10MB
        await asyncio.sleep(10)
        del data  # 释放内存
        await asyncio.sleep(5)

    # 2. 手动控制
    monitor2 = MemoryMonitor(interval=2.0, report_threshold_mb=5.0)
    await monitor2.start()

    # 执行一些操作
    logger.info("手动监控中，执行一些内存操作...")
    large_list = []
    for i in range(5):
        large_list.append(bytearray(5 * 1024 * 1024))  # 每次分配5MB
        await asyncio.sleep(3)

    await monitor2.stop()

    logger.info("手动使用演示完成")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        # 运行演示
        asyncio.run(demo_manual_usage())
    else:
        # 启动FastAPI应用
        logger.info("启动内存监控示例应用...")
        logger.info("访问 http://localhost:8000/ 查看应用")
        logger.info("访问 http://localhost:8000/memory 查看内存信息")
        logger.info("访问 http://localhost:8000/memory/report 查看详细报告")
        logger.info("使用 'python memory_monitor_example.py demo' 运行手动演示")

        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8000,
            log_level="info",
        )
