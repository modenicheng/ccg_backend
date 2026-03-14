#!/usr/bin/env python3
"""Memory monitoring example - demonstrates how to use memory monitoring in FastAPI."""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI
import uvicorn

from utils import init_logging, get_logger
from utils.memory_monitor import MemoryMonitor

init_logging(level=logging.INFO)
logger = get_logger(__name__)

app = FastAPI()

memory_monitor: MemoryMonitor | None = None


@app.on_event("startup")
async def startup_event():
    """Start memory monitoring on application startup."""
    global memory_monitor
    memory_monitor = MemoryMonitor(
        interval=30.0,
        report_threshold_mb=50.0,
        detailed_report=True,
    )
    await memory_monitor.start()
    logger.info("Memory monitor started")


@app.on_event("shutdown")
async def shutdown_event():
    """Stop memory monitoring on application shutdown."""
    global memory_monitor
    if memory_monitor is not None:
        await memory_monitor.stop()
        logger.info("Memory monitor stopped")


@app.get("/")
async def root():
    """Root route."""
    return {"message": "Memory monitoring example", "status": "running"}


@app.get("/memory")
async def get_memory_info():
    """Get current memory information."""
    monitor = MemoryMonitor()
    memory_info = monitor._get_memory_info()  # pylint: disable=protected-access

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
    """Get formatted memory report."""
    monitor = MemoryMonitor(detailed_report=True)
    memory_info = monitor._get_memory_info()  # pylint: disable=protected-access
    report = monitor._format_memory_report(memory_info)  # pylint: disable=protected-access

    return {"report": report}


async def demo_manual_usage():
    """Demonstrate manual usage of memory monitoring."""
    logger.info("Demonstrating manual memory monitoring...")

    monitor = MemoryMonitor(interval=5.0, report_threshold_mb=10.0)

    async with monitor.monitor_context():
        logger.info("Executing operations within monitoring context...")
        data = [bytearray(1024 * 1024) for _ in range(10)]
        await asyncio.sleep(10)
        del data
        await asyncio.sleep(5)

    monitor2 = MemoryMonitor(interval=2.0, report_threshold_mb=5.0)
    await monitor2.start()

    logger.info("Manual monitoring in progress...")
    large_list = []
    for _ in range(5):
        large_list.append(bytearray(5 * 1024 * 1024))
        await asyncio.sleep(3)

    await monitor2.stop()
    logger.info("Manual usage demo complete")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        asyncio.run(demo_manual_usage())
    else:
        logger.info("Starting memory monitoring example app...")
        logger.info("Visit http://localhost:8000/ to view app")
        logger.info("Visit http://localhost:8000/memory for memory info")
        logger.info("Visit http://localhost:8000/memory/report for detailed report")
        logger.info("Run 'python memory_monitor_example.py demo' for manual demo")

        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8000,
            log_level="info",
        )
