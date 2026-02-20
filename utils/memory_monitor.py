"""
内存监控模块，提供定时内存使用报告功能。
"""
from __future__ import annotations

import asyncio
import logging
import psutil
import time
from typing import Optional, Callable, Any
from contextlib import asynccontextmanager

from .logger import get_logger

logger = get_logger(__name__)


class MemoryMonitor:
    """内存监控器，用于定时报告内存使用情况"""

    def __init__(
        self,
        interval: float = 60.0,
        report_threshold_mb: float = 100.0,
        detailed_report: bool = False,
    ):
        """
        初始化内存监控器
        
        Args:
            interval: 报告间隔时间（秒），默认60秒
            report_threshold_mb: 内存变化报告阈值（MB），默认100MB
            detailed_report: 是否输出详细报告，默认False
        """
        self.interval = interval
        self.report_threshold_mb = report_threshold_mb
        self.detailed_report = detailed_report
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_memory_usage: Optional[float] = None

    def _get_memory_info(self) -> dict[str, Any]:
        """获取当前内存使用信息"""
        process = psutil.Process()
        memory_info = process.memory_info()

        # 获取系统内存信息
        system_memory = psutil.virtual_memory()

        # 构建内存信息字典（使用通用属性）
        memory_data = {
            "rss_mb": memory_info.rss / 1024 / 1024,  # RSS内存（MB）
            "vms_mb": memory_info.vms / 1024 / 1024,  # VMS内存（MB）
            "system_total_mb": system_memory.total / 1024 / 1024,
            "system_available_mb": system_memory.available / 1024 / 1024,
            "system_used_mb": system_memory.used / 1024 / 1024,
            "system_percent": system_memory.percent,
            "timestamp": time.time(),
        }

        # 设置默认值（这些属性在Windows上不可用）
        memory_data["shared_mb"] = 0
        memory_data["text_mb"] = 0
        memory_data["data_mb"] = 0

        return memory_data

    def _format_memory_report(self, memory_info: dict[str, Any]) -> str:
        """格式化内存报告"""
        lines = [
            "=" * 50,
            f"内存使用报告 - {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(memory_info['timestamp']))}",
            "-" * 50,
        ]

        # 进程内存信息
        lines.append("进程内存:")
        lines.append(f"  RSS内存: {memory_info['rss_mb']:.2f} MB")
        lines.append(f"  VMS内存: {memory_info['vms_mb']:.2f} MB")

        if self.detailed_report:
            if memory_info.get('shared_mb', 0) > 0:
                lines.append(f"  共享内存: {memory_info['shared_mb']:.2f} MB")
            if memory_info.get('text_mb', 0) > 0:
                lines.append(f"  代码段: {memory_info['text_mb']:.2f} MB")
            if memory_info.get('data_mb', 0) > 0:
                lines.append(f"  数据段: {memory_info['data_mb']:.2f} MB")

        # 系统内存信息
        lines.append("系统内存:")
        lines.append(f"  总内存: {memory_info['system_total_mb']:.2f} MB")
        lines.append(
            f"  已使用: {memory_info['system_used_mb']:.2f} MB ({memory_info['system_percent']:.1f}%)"
        )
        lines.append(f"  可用内存: {memory_info['system_available_mb']:.2f} MB")

        lines.append("=" * 50)
        return "\n".join(lines)

    async def _monitor_loop(self):
        """监控循环"""
        logger.info(f"内存监控器已启动，报告间隔: {self.interval}秒")

        while self._running:
            try:
                memory_info = self._get_memory_info()
                current_rss = memory_info['rss_mb']

                # 检查是否需要报告（首次报告或变化超过阈值）
                should_report = False
                if self._last_memory_usage is None:
                    should_report = True
                    logger.info("首次内存使用报告")
                else:
                    memory_change = abs(current_rss - self._last_memory_usage)
                    if memory_change >= self.report_threshold_mb:
                        should_report = True
                        logger.info(f"内存变化超过阈值: {memory_change:.2f} MB")

                if should_report:
                    report = self._format_memory_report(memory_info)
                    logger.info(f"\n{report}")
                    self._last_memory_usage = current_rss
                else:
                    logger.debug(f"当前RSS内存: {current_rss:.2f} MB (变化未达阈值)")

                await asyncio.sleep(self.interval)

            except asyncio.CancelledError:
                logger.info("内存监控器被取消")
                break
            except Exception as e:
                logger.error(f"内存监控出错: {e}")
                await asyncio.sleep(self.interval)  # 出错后继续等待

    async def start(self):
        """启动内存监控"""
        if self._running:
            logger.warning("内存监控器已经在运行")
            return

        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("内存监控器启动成功")

    async def stop(self):
        """停止内存监控"""
        if not self._running:
            logger.warning("内存监控器未在运行")
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info("内存监控器已停止")

    @asynccontextmanager
    async def monitor_context(self):
        """上下文管理器，用于在代码块中临时监控内存"""
        await self.start()
        try:
            yield
        finally:
            await self.stop()


async def start_memory_monitoring(
    interval: float = 60.0,
    report_threshold_mb: float = 100.0,
    detailed_report: bool = False,
) -> MemoryMonitor:
    """
    启动内存监控的便捷函数
    
    Args:
        interval: 报告间隔时间（秒）
        report_threshold_mb: 内存变化报告阈值（MB）
        detailed_report: 是否输出详细报告
    
    Returns:
        MemoryMonitor: 内存监控器实例
    """
    monitor = MemoryMonitor(
        interval=interval,
        report_threshold_mb=report_threshold_mb,
        detailed_report=detailed_report,
    )
    await monitor.start()
    return monitor


async def periodic_memory_report(
    interval: float = 60.0,
    detailed: bool = False,
    callback: Optional[Callable[[dict], None]] = None,
) -> None:
    """
    简单的定时内存报告函数
    
    Args:
        interval: 报告间隔（秒）
        detailed: 是否输出详细报告
        callback: 可选的回调函数，接收内存信息字典
    """
    logger = get_logger(__name__)
    logger.info(f"开始定时内存报告，间隔: {interval}秒")

    try:
        while True:
            process = psutil.Process()
            memory_info = process.memory_info()
            system_memory = psutil.virtual_memory()

            rss_mb = memory_info.rss / 1024 / 1024
            vms_mb = memory_info.vms / 1024 / 1024
            system_percent = system_memory.percent

            if detailed:
                report = (
                    f"内存报告 - {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"进程RSS: {rss_mb:.2f} MB\n"
                    f"进程VMS: {vms_mb:.2f} MB\n"
                    f"系统使用率: {system_percent:.1f}%\n"
                    f"系统可用: {system_memory.available / 1024 / 1024:.2f} MB")
                logger.info(report)
            else:
                logger.info(
                    f"内存使用: RSS={rss_mb:.2f}MB, VMS={vms_mb:.2f}MB, 系统={system_percent:.1f}%"
                )

            # 调用回调函数
            if callback:
                info = {
                    "rss_mb": rss_mb,
                    "vms_mb": vms_mb,
                    "system_percent": system_percent,
                    "system_available_mb":
                    system_memory.available / 1024 / 1024,
                    "timestamp": time.time(),
                }
                callback(info)

            await asyncio.sleep(interval)

    except asyncio.CancelledError:
        logger.info("定时内存报告已停止")
    except Exception as e:
        logger.error(f"定时内存报告出错: {e}")


# 导出主要功能
__all__ = [
    "MemoryMonitor",
    "start_memory_monitoring",
    "periodic_memory_report",
]
