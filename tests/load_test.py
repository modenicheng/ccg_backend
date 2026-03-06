#!/usr/bin/env python3
from __future__ import annotations
"""
压力测试脚本 - 使用异步和高性能HTTP库对指定API接口进行压测
返回平均响应时间（包含服务器和网络两部分）
支持GET、POST、PUT、DELETE等方法，自定义请求头和JSON数据
"""

import asyncio
import argparse
import time
import statistics
import json
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from collections import defaultdict

import httpx
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.live import Live
from rich.panel import Panel
from rich import print as rprint

console = Console()


@dataclass
class RequestConfig:
    """请求配置"""
    method: str = "GET"
    headers: Dict[str, str] = None
    json_data: Any = None
    data: Dict[str, Any] = None
    timeout: float = 30.0

    def __post_init__(self):
        if self.headers is None:
            self.headers = {}
        if self.json_data is not None:
            self.headers.setdefault("Content-Type", "application/json")


@dataclass
class RequestResult:
    """单个请求的结果"""
    status_code: int
    response_time: float  # 单位：秒
    error: str | None = None


@dataclass
class LoadTestStats:
    """压力测试统计信息"""
    total_requests: int
    successful_requests: int
    failed_requests: int
    total_time: float
    response_times: List[float]

    @property
    def avg_response_time(self) -> float:
        """平均响应时间"""
        if not self.response_times:
            return 0.0
        return statistics.mean(self.response_times)

    @property
    def min_response_time(self) -> float:
        """最小响应时间"""
        if not self.response_times:
            return 0.0
        return min(self.response_times)

    @property
    def max_response_time(self) -> float:
        """最大响应时间"""
        if not self.response_times:
            return 0.0
        return max(self.response_times)

    @property
    def median_response_time(self) -> float:
        """中位数响应时间"""
        if not self.response_times:
            return 0.0
        try:
            return statistics.median(self.response_times)
        except statistics.StatisticsError:
            return 0.0

    @property
    def p95_response_time(self) -> float:
        """95%分位响应时间"""
        if not self.response_times or len(self.response_times) < 2:
            return 0.0
        try:
            return statistics.quantiles(self.response_times, n=100)[94]
        except statistics.StatisticsError:
            return 0.0

    @property
    def p99_response_time(self) -> float:
        """99%分位响应时间"""
        if not self.response_times or len(self.response_times) < 2:
            return 0.0
        try:
            return statistics.quantiles(self.response_times, n=100)[98]
        except statistics.StatisticsError:
            return 0.0

    @property
    def requests_per_second(self) -> float:
        """每秒请求数"""
        if self.total_time <= 0:
            return 0.0
        return self.total_requests / self.total_time

    @property
    def success_rate(self) -> float:
        """成功率"""
        if self.total_requests <= 0:
            return 0.0
        return self.successful_requests / self.total_requests * 100


async def make_request(client: httpx.AsyncClient, url: str,
                       config: RequestConfig) -> RequestResult:
    """
    发送单个请求并测量响应时间
    """
    start_time = time.perf_counter()
    try:
        # 准备请求参数
        kwargs = {
            "timeout": config.timeout,
            "headers": config.headers,
        }

        # 根据方法添加数据
        method = config.method.upper()
        if method in ["POST", "PUT", "PATCH"]:
            if config.json_data is not None:
                kwargs["json"] = config.json_data
            elif config.data is not None:
                kwargs["data"] = config.data

        # 发送请求
        response = await client.request(method, url, **kwargs)
        elapsed = time.perf_counter() - start_time
        return RequestResult(status_code=response.status_code,
                             response_time=elapsed,
                             error=None)
    except Exception as e:
        elapsed = time.perf_counter() - start_time
        return RequestResult(status_code=0, response_time=elapsed, error=str(e))


async def worker(client: httpx.AsyncClient, url: str, config: RequestConfig,
                 request_count: int, results: List[RequestResult], progress: Progress,
                 task_id: int) -> None:
    """
    工作协程：发送指定数量的请求
    """
    for _ in range(request_count):
        result = await make_request(client, url, config)
        results.append(result)
        progress.update(task_id, advance=1)


async def run_load_test(url: str,
                        config: RequestConfig,
                        concurrent: int,
                        total_requests: int,
                        show_progress: bool = True) -> LoadTestStats:
    """
    运行压力测试
    """
    # 计算每个worker的请求数
    requests_per_worker = total_requests // concurrent
    extra_requests = total_requests % concurrent

    results: List[RequestResult] = []
    start_time = time.perf_counter()

    # 创建进度条
    progress = Progress(SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        BarColumn(),
                        TaskProgressColumn(),
                        console=console)

    task_id = progress.add_task("[cyan]发送请求...", total=total_requests)

    # 创建HTTP客户端
    limits = httpx.Limits(max_keepalive_connections=concurrent,
                          max_connections=concurrent)
    async with httpx.AsyncClient(limits=limits, timeout=config.timeout) as client:
        # 启动worker任务
        tasks = []
        for i in range(concurrent):
            worker_requests = requests_per_worker + (1 if i < extra_requests else 0)
            if worker_requests > 0:
                task = asyncio.create_task(
                    worker(client, url, config, worker_requests, results, progress,
                           task_id))
                tasks.append(task)

        # 显示进度条
        if show_progress:
            with progress:
                await asyncio.gather(*tasks)
        else:
            await asyncio.gather(*tasks)

    total_time = time.perf_counter() - start_time

    # 统计结果
    successful = sum(
        1 for r in results if r.error is None and 200 <= r.status_code < 400)
    failed = total_requests - successful
    response_times = [r.response_time for r in results if r.error is None]

    return LoadTestStats(total_requests=total_requests,
                         successful_requests=successful,
                         failed_requests=failed,
                         total_time=total_time,
                         response_times=response_times)


def display_results(stats: LoadTestStats, url: str, config: RequestConfig) -> None:
    """
    使用Rich显示测试结果
    """
    console.print()
    console.print(
        Panel.fit(f"[bold cyan]压力测试结果 - {url}[/bold cyan]", border_style="cyan"))

    # 显示请求配置
    console.print(f"[dim]方法: {config.method}, 超时: {config.timeout}s[/dim]")
    if config.headers:
        console.print(f"[dim]请求头: {config.headers}[/dim]")
    if config.json_data is not None:
        console.print(f"[dim]JSON数据: {config.json_data}[/dim]")
    if config.data is not None:
        console.print(f"[dim]表单数据: {config.data}[/dim]")

    # 创建主表格
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("指标", style="dim", width=30)
    table.add_column("值", justify="right")

    table.add_row("总请求数", f"{stats.total_requests:,}")
    table.add_row("成功请求数", f"{stats.successful_requests:,}")
    table.add_row("失败请求数", f"{stats.failed_requests:,}")
    table.add_row("成功率", f"{stats.success_rate:.2f}%")
    table.add_row("总测试时间", f"{stats.total_time:.2f} 秒")
    table.add_row("每秒请求数 (RPS)", f"{stats.requests_per_second:.2f}")

    console.print(table)

    # 响应时间表格
    time_table = Table(show_header=True, header_style="bold green", title="响应时间统计")
    time_table.add_column("统计项", style="dim", width=20)
    time_table.add_column("时间 (秒)", justify="right")
    time_table.add_column("时间 (毫秒)", justify="right")

    time_table.add_row("平均响应时间", f"{stats.avg_response_time:.4f}",
                       f"{stats.avg_response_time * 1000:.2f} ms")
    time_table.add_row("最小响应时间", f"{stats.min_response_time:.4f}",
                       f"{stats.min_response_time * 1000:.2f} ms")
    time_table.add_row("最大响应时间", f"{stats.max_response_time:.4f}",
                       f"{stats.max_response_time * 1000:.2f} ms")
    time_table.add_row("中位数响应时间", f"{stats.median_response_time:.4f}",
                       f"{stats.median_response_time * 1000:.2f} ms")
    time_table.add_row("P95响应时间", f"{stats.p95_response_time:.4f}",
                       f"{stats.p95_response_time * 1000:.2f} ms")
    time_table.add_row("P99响应时间", f"{stats.p99_response_time:.4f}",
                       f"{stats.p99_response_time * 1000:.2f} ms")

    console.print(time_table)

    # 显示性能评估
    console.print()
    avg_ms = stats.avg_response_time * 1000
    if avg_ms < 100:
        rating = "[green]优秀[/green]"
    elif avg_ms < 300:
        rating = "[yellow]良好[/yellow]"
    elif avg_ms < 1000:
        rating = "[orange1]一般[/orange1]"
    else:
        rating = "[red]较差[/red]"

    console.print(f"[bold]性能评估:[/bold] {rating} (平均响应时间: {avg_ms:.2f} ms)")

    # 显示RPS评估
    rps = stats.requests_per_second
    if rps > 100:
        rps_rating = "[green]优秀[/green]"
    elif rps > 50:
        rps_rating = "[yellow]良好[/yellow]"
    elif rps > 10:
        rps_rating = "[orange1]一般[/orange1]"
    else:
        rps_rating = "[red]较差[/red]"

    console.print(f"[bold]吞吐量评估:[/bold] {rps_rating} (RPS: {rps:.2f})")

    # 显示错误摘要（如果有）
    if stats.failed_requests > 0:
        console.print()
        console.print(Panel.fit("[bold red]错误摘要[/bold red]", border_style="red"))
        console.print(
            f"[red]失败请求数: {stats.failed_requests} ({stats.failed_requests/stats.total_requests*100:.1f}%)[/red]"
        )


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="异步HTTP压力测试工具 - 使用Rich输出结果",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s http://localhost:8000/api/songlists
  %(prog)s http://localhost:8000/api/songlists -c 20 -n 1000
  %(prog)s http://localhost:8000/api/users -m POST --json '{"name": "test"}'
  %(prog)s http://localhost:8000/api/login -m POST --header "Content-Type: application/json" --json '{"username": "admin", "password": "secret"}'
        """)
    parser.add_argument("url", help="要测试的API接口URL")
    parser.add_argument("-c",
                        "--concurrent",
                        type=int,
                        default=10,
                        help="并发连接数 (默认: 10)")
    parser.add_argument("-n",
                        "--requests",
                        type=int,
                        default=100,
                        help="总请求数 (默认: 100)")
    parser.add_argument("-m",
                        "--method",
                        type=str,
                        default="GET",
                        choices=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"],
                        help="HTTP方法 (默认: GET)")
    parser.add_argument("-H",
                        "--header",
                        action="append",
                        help="请求头，格式: 'Key: Value'，可多次使用")
    parser.add_argument("--json", type=str, help="JSON请求体，例如: '{\"key\": \"value\"}'")
    parser.add_argument("--data", action="append", help="表单数据，格式: 'key=value'，可多次使用")
    parser.add_argument("--timeout",
                        type=float,
                        default=30.0,
                        help="请求超时时间（秒） (默认: 30)")
    parser.add_argument("--no-progress", action="store_true", help="不显示进度条")
    parser.add_argument("--verbose", action="store_true", help="显示详细错误信息")

    return parser.parse_args()


def build_config_from_args(args) -> RequestConfig:
    """从命令行参数构建请求配置"""
    headers = {}
    if args.header:
        for header in args.header:
            if ":" in header:
                key, value = header.split(":", 1)
                headers[key.strip()] = value.strip()
            else:
                print(f"警告: 忽略无效的请求头格式: {header}")

    json_data = None
    if args.json:
        try:
            json_data = json.loads(args.json)
        except json.JSONDecodeError as e:
            print(f"错误: JSON解析失败: {e}")
            raise

    data = None
    if args.data:
        data = {}
        for item in args.data:
            if "=" in item:
                key, value = item.split("=", 1)
                data[key.strip()] = value.strip()
            else:
                print(f"警告: 忽略无效的数据格式: {item}")

    return RequestConfig(method=args.method,
                         headers=headers,
                         json_data=json_data,
                         data=data,
                         timeout=args.timeout)


async def main():
    """主函数"""
    args = parse_args()

    try:
        config = build_config_from_args(args)
    except Exception as e:
        console.print(f"[red]配置错误: {e}[/red]")
        return

    console.print(f"[bold]开始压力测试:[/bold] {args.url}")
    console.print(
        f"[dim]方法: {config.method}, 并发数: {args.concurrent}, 总请求数: {args.requests}[/dim]"
    )

    try:
        stats = await run_load_test(url=args.url,
                                    config=config,
                                    concurrent=args.concurrent,
                                    total_requests=args.requests,
                                    show_progress=not args.no_progress)

        display_results(stats, args.url, config)

    except KeyboardInterrupt:
        console.print("\n[yellow]测试被用户中断[/yellow]")
    except Exception as e:
        console.print(f"[red]测试出错: {e}[/red]")
        if args.verbose:
            import traceback
            console.print(traceback.format_exc())


if __name__ == "__main__":
    asyncio.run(main())
