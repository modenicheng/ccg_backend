#!/usr/bin/env python3
"""
测试内存监控功能
"""
import asyncio
import logging
import sys
import os

# 添加当前目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import init_logging, get_logger
from utils.memory_monitor import (
    MemoryMonitor,
    start_memory_monitoring,
    periodic_memory_report,
)

# 初始化日志
init_logging(level=logging.DEBUG)
logger = get_logger(__name__)


async def test_memory_monitor_class():
    """测试MemoryMonitor类"""
    logger.info("测试MemoryMonitor类...")
    
    # 创建内存监控器（每5秒报告一次，阈值50MB）
    monitor = MemoryMonitor(
        interval=5.0,
        report_threshold_mb=50.0,
        detailed_report=True,
    )
    
    # 启动监控
    await monitor.start()
    
    # 运行一段时间
    logger.info("监控运行中，等待15秒...")
    await asyncio.sleep(15)
    
    # 停止监控
    await monitor.stop()
    logger.info("MemoryMonitor测试完成")


async def test_start_memory_monitoring():
    """测试便捷函数start_memory_monitoring"""
    logger.info("测试start_memory_monitoring函数...")
    
    # 启动内存监控
    monitor = await start_memory_monitoring(
        interval=3.0,
        report_threshold_mb=10.0,
        detailed_report=False,
    )
    
    # 运行一段时间
    logger.info("监控运行中，等待10秒...")
    await asyncio.sleep(10)
    
    # 停止监控
    await monitor.stop()
    logger.info("start_memory_monitoring测试完成")


async def test_periodic_memory_report():
    """测试简单的定时内存报告函数"""
    logger.info("测试periodic_memory_report函数...")
    
    # 定义回调函数
    def memory_callback(info: dict):
        logger.debug(f"回调收到内存信息: RSS={info['rss_mb']:.2f}MB")
    
    # 启动定时报告任务
    task = asyncio.create_task(
        periodic_memory_report(
            interval=2.0,
            detailed=True,
            callback=memory_callback,
        )
    )
    
    # 运行一段时间
    logger.info("定时报告运行中，等待8秒...")
    await asyncio.sleep(8)
    
    # 取消任务
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        logger.info("定时报告任务已取消")
    
    logger.info("periodic_memory_report测试完成")


async def test_context_manager():
    """测试上下文管理器"""
    logger.info("测试上下文管理器...")
    
    monitor = MemoryMonitor(interval=2.0, report_threshold_mb=5.0)
    
    async with monitor.monitor_context():
        logger.info("在监控上下文中，等待6秒...")
        await asyncio.sleep(6)
    
    logger.info("上下文管理器测试完成")


async def main():
    """主测试函数"""
    logger.info("开始测试内存监控功能")
    
    try:
        # 测试1: MemoryMonitor类
        await test_memory_monitor_class()
        
        # 测试2: 便捷函数
        await test_start_memory_monitoring()
        
        # 测试3: 简单定时报告
        await test_periodic_memory_report()
        
        # 测试4: 上下文管理器
        await test_context_manager()
        
        logger.info("所有测试完成！")
        
    except Exception as e:
        logger.error(f"测试过程中出错: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return 1
    
    return 0


if __name__ == "__main__":
    # 检查psutil是否安装
    try:
        import psutil
        logger.info(f"psutil版本: {psutil.__version__}")
    except ImportError:
        logger.error("psutil未安装，请运行: pip install psutil")
        sys.exit(1)
    
    # 运行测试
    exit_code = asyncio.run(main())
    sys.exit(exit_code)