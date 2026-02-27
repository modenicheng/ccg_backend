#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mock压测脚本 (CLI工具)
使用httpx进行高性能HTTP请求，使用rich进行输出
"""
import asyncio
import argparse
import time
import statistics
from typing import List, Dict, Any, Optional
try:
    import httpx
except ImportError:
    print("错误: 未安装httpx。请使用 pip install httpx 安装。")
    print("或者使用 pip install httpx[cli] 以获取额外功能。")
    exit(1)
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box

console = Console()


async def make_request(
    client: httpx.AsyncClient,
    url: str,
    method: str = "GET",
    timeout: float = 30.0,
    **kwargs
) -> Dict[str, Any]:
    """发送单个请求并返回结果统计"""
    start_time = time.perf_counter()
    try:
        response = await client.request(method, url, timeout=timeout, **kwargs)
        elapsed = time.perf_counter() - start_time
        return {
            "success": True,
            "status_code": response.status_code,
            "elapsed": elapsed,
            "error": None,
        }
    except Exception as e:
        elapsed = time.perf_counter() - start_time
        return {
            "success": False,
            "status_code": None,
            "elapsed": elapsed,
            "error": str(e),
        }


async def run_load_test(
    url: str,
    total_requests: int = 100,
    concurrency: int = 10,
    method: str = "GET",
    timeout: float = 30.0,
    **kwargs
) -> Dict[str, Any]:
    """运行压测并返回统计结果"""
    # 限制并发数不超过总请求数
    concurrency = min(concurrency, total_requests)

    results = []
    semaphore = asyncio.Semaphore(concurrency)

    async def make_request_with_semaphore(client, url, method, timeout, **kwargs):
        async with semaphore:
            return await make_request(client, url, method, timeout, **kwargs)

    # 使用rich进度条
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        task = progress.add_task(f"发送 {total_requests} 个请求...", total=total_requests)

        # 创建异步客户端
        async with httpx.AsyncClient() as client:
            # 创建所有任务
            tasks = []
            for i in range(total_requests):
                task_future = make_request_with_semaphore(client, url, method, timeout, **kwargs)
                tasks.append(task_future)

            # 使用asyncio.as_completed获取完成的任务
            for coro in asyncio.as_completed(tasks):
                result = await coro
                results.append(result)
                progress.update(task, advance=1)

    return analyze_results(results)


def analyze_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """分析结果并计算统计数据"""
    success_results = [r for r in results if r["success"]]
    failed_results = [r for r in results if not r["success"]]

    elapsed_times = [r["elapsed"] for r in results]
    success_times = [r["elapsed"] for r in success_results]

    # 计算百分位数
    p90 = p95 = p99 = 0.0
    if elapsed_times:
        try:
            quantiles = statistics.quantiles(elapsed_times, n=100, method='inclusive')
            # quantiles 返回的是 [第1百分位数, 第2百分位数, ..., 第99百分位数]
            # 我们需要第90、95、99百分位数
            p90 = quantiles[89] if len(quantiles) >= 90 else quantiles[-1]  # 索引89对应第90百分位数
            p95 = quantiles[94] if len(quantiles) >= 95 else quantiles[-1]
            p99 = quantiles[98] if len(quantiles) >= 99 else quantiles[-1]
        except Exception:
            # 如果计算失败，使用近似值
            sorted_times = sorted(elapsed_times)
            p90 = sorted_times[int(len(sorted_times) * 0.9) - 1] if sorted_times else 0.0
            p95 = sorted_times[int(len(sorted_times) * 0.95) - 1] if sorted_times else 0.0
            p99 = sorted_times[int(len(sorted_times) * 0.99) - 1] if sorted_times else 0.0

    stats = {
        "total": len(results),
        "success": len(success_results),
        "failed": len(failed_results),
        "success_rate": len(success_results) / len(results) if results else 0,
        "total_time": sum(elapsed_times),
        "avg_time": statistics.mean(elapsed_times) if elapsed_times else 0,
        "min_time": min(elapsed_times) if elapsed_times else 0,
        "max_time": max(elapsed_times) if elapsed_times else 0,
        "median_time": statistics.median(elapsed_times) if elapsed_times else 0,
        "std_time": statistics.stdev(elapsed_times) if len(elapsed_times) > 1 else 0,
        "avg_success_time": statistics.mean(success_times) if success_times else 0,
        "p90_time": p90,
        "p95_time": p95,
        "p99_time": p99,
        "status_codes": {},
        "errors": {},
    }

    # 统计状态码
    for r in success_results:
        code = r["status_code"]
        stats["status_codes"][code] = stats["status_codes"].get(code, 0) + 1

    # 统计错误类型
    for r in failed_results:
        error = r["error"]
        stats["errors"][error] = stats["errors"].get(error, 0) + 1

    return stats


def display_results(stats: Dict[str, Any], url: str) -> None:
    """使用rich表格显示结果"""
    console.print()
    console.print(f"[bold cyan]压测结果 - {url}[/bold cyan]")
    console.print()

    # 创建摘要表格
    summary_table = Table(box=box.ROUNDED, show_header=True)
    summary_table.add_column("指标", style="cyan")
    summary_table.add_column("值", style="green")

    summary_table.add_row("总请求数", str(stats["total"]))
    summary_table.add_row("成功请求数", str(stats["success"]))
    summary_table.add_row("失败请求数", str(stats["failed"]))
    summary_table.add_row("成功率", f"{stats['success_rate']:.2%}")
    summary_table.add_row("总耗时", f"{stats['total_time']:.3f}s")
    summary_table.add_row("平均响应时间", f"{stats['avg_time']*1000:.2f}ms")
    summary_table.add_row("平均成功响应时间", f"{stats['avg_success_time']*1000:.2f}ms")
    summary_table.add_row("最小响应时间", f"{stats['min_time']*1000:.2f}ms")
    summary_table.add_row("最大响应时间", f"{stats['max_time']*1000:.2f}ms")
    summary_table.add_row("中位数响应时间", f"{stats['median_time']*1000:.2f}ms")
    summary_table.add_row("P90响应时间", f"{stats['p90_time']*1000:.2f}ms")
    summary_table.add_row("P95响应时间", f"{stats['p95_time']*1000:.2f}ms")
    summary_table.add_row("P99响应时间", f"{stats['p99_time']*1000:.2f}ms")
    summary_table.add_row("标准差", f"{stats['std_time']*1000:.2f}ms")

    console.print(summary_table)

    # 状态码分布
    if stats["status_codes"]:
        code_table = Table(title="状态码分布", box=box.ROUNDED)
        code_table.add_column("状态码", style="yellow")
        code_table.add_column("数量", style="green")
        code_table.add_column("比例", style="blue")

        for code, count in sorted(stats["status_codes"].items()):
            percentage = count / stats["total"]
            code_table.add_row(str(code), str(count), f"{percentage:.2%}")

        console.print()
        console.print(code_table)

    # 错误分布
    if stats["errors"]:
        error_table = Table(title="错误分布", box=box.ROUNDED)
        error_table.add_column("错误类型", style="red")
        error_table.add_column("数量", style="green")

        for error, count in sorted(stats["errors"].items()):
            error_table.add_row(error[:100], str(count))

        console.print()
        console.print(error_table)


def main():
    """主函数：解析命令行参数并运行压测"""
    parser = argparse.ArgumentParser(
        description="Mock压测脚本 - 使用httpx和rich进行HTTP接口压测",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python mock_load_test.py http://localhost:8000/api/test
  python mock_load_test.py http://localhost:8000/api/test -n 1000 -c 50
  python mock_load_test.py http://localhost:8000/api/test -m POST -H '{"Content-Type": "application/json"}' -d '{"key": "value"}'
        """
    )

    parser.add_argument("url", help="要压测的接口URL")
    parser.add_argument("-n", "--requests", type=int, default=100,
                       help="总请求数 (默认: 100)")
    parser.add_argument("-c", "--concurrency", type=int, default=10,
                       help="并发数 (默认: 10)")
    parser.add_argument("-m", "--method", default="GET",
                       help="HTTP方法 (默认: GET)")
    parser.add_argument("-t", "--timeout", type=float, default=30.0,
                       help="请求超时时间(秒) (默认: 30)")
    parser.add_argument("-H", "--headers", type=str,
                       help="请求头 (JSON格式)")
    parser.add_argument("-d", "--data", type=str,
                       help="请求体数据 (JSON格式)")

    args = parser.parse_args()

    # 准备请求参数
    kwargs = {}
    if args.headers:
        try:
            import json
            kwargs["headers"] = json.loads(args.headers)
        except json.JSONDecodeError:
            console.print("[red]错误: 请求头必须是有效的JSON格式[/red]")
            return 1

    if args.data:
        try:
            import json
            kwargs["json"] = json.loads(args.data)
        except json.JSONDecodeError:
            console.print("[red]错误: 请求体必须是有效的JSON格式[/red]")
            return 1

    # 运行压测
    console.print(f"[bold]开始压测:[/bold] {args.url}")
    console.print(f"请求数: {args.requests}, 并发数: {args.concurrency}, 方法: {args.method}")

    try:
        stats = asyncio.run(
            run_load_test(
                url=args.url,
                total_requests=args.requests,
                concurrency=args.concurrency,
                method=args.method,
                timeout=args.timeout,
                **kwargs
            )
        )

        display_results(stats, args.url)

    except KeyboardInterrupt:
        console.print("\n[yellow]压测被用户中断[/yellow]")
        return 1
    except Exception as e:
        console.print(f"[red]压测失败: {e}[/red]")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())