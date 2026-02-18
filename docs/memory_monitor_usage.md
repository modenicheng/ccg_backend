# 内存定时报告模块使用指南

## 概述

本模块提供了一个完整的异步内存监控解决方案，可以定时报告进程和系统的内存使用情况。模块包含多种使用方式，适合不同的应用场景。

## 安装依赖

```bash
pip install psutil
```

## 快速开始

### 1. 基本使用

```python
import asyncio
from utils.memory_monitor import periodic_memory_report

# 简单的定时内存报告
async def main():
    # 每60秒报告一次内存使用情况
    task = asyncio.create_task(periodic_memory_report(interval=60.0))
    await asyncio.sleep(300)  # 运行5分钟
    task.cancel()

asyncio.run(main())
```

### 2. 使用MemoryMonitor类（推荐）

```python
import asyncio
from utils.memory_monitor import MemoryMonitor

async def main():
    # 创建内存监控器
    monitor = MemoryMonitor(
        interval=30.0,           # 每30秒检查一次
        report_threshold_mb=50.0, # 内存变化超过50MB时报告
        detailed_report=True,    # 输出详细报告
    )
    
    # 启动监控
    await monitor.start()
    
    # 运行一段时间
    await asyncio.sleep(300)
    
    # 停止监控
    await monitor.stop()

asyncio.run(main())
```

### 3. 使用上下文管理器

```python
import asyncio
from utils.memory_monitor import MemoryMonitor

async def main():
    monitor = MemoryMonitor(interval=10.0)
    
    async with monitor.monitor_context():
        # 在这个代码块中，内存监控会自动运行
        await perform_memory_intensive_operations()
        await asyncio.sleep(60)
    
    # 离开上下文后，监控自动停止

asyncio.run(main())
```

## API参考

### MemoryMonitor类

#### 构造函数

```python
MemoryMonitor(
    interval: float = 60.0,
    report_threshold_mb: float = 100.0,
    detailed_report: bool = False,
)
```

**参数：**

- `interval`: 报告间隔时间（秒），默认60秒
- `report_threshold_mb`: 内存变化报告阈值（MB），默认100MB
- `detailed_report`: 是否输出详细报告，默认False

#### 方法

- `async start()`: 启动内存监控
- `async stop()`: 停止内存监控
- `_get_memory_info() -> dict`: 获取当前内存信息（内部方法）
- `_format_memory_report(memory_info: dict) -> str`: 格式化内存报告（内部方法）

#### 属性

- `interval`: 报告间隔
- `report_threshold_mb`: 报告阈值
- `detailed_report`: 是否详细报告

### 便捷函数

#### `async start_memory_monitoring()`

```python
async def start_memory_monitoring(
    interval: float = 60.0,
    report_threshold_mb: float = 100.0,
    detailed_report: bool = False,
) -> MemoryMonitor:
```

快速启动内存监控并返回监控器实例。

#### `async periodic_memory_report()`

```python
async def periodic_memory_report(
    interval: float = 60.0,
    detailed: bool = False,
    callback: Optional[Callable[[dict], None]] = None,
) -> None:
```

简单的定时内存报告函数，适合简单场景。

## 在FastAPI应用中使用

### 集成到现有应用

```python
from fastapi import FastAPI
from utils.memory_monitor import MemoryMonitor

app = FastAPI()
memory_monitor = None

@app.on_event("startup")
async def startup_event():
    global memory_monitor
    memory_monitor = MemoryMonitor(interval=30.0)
    await memory_monitor.start()

@app.on_event("shutdown")
async def shutdown_event():
    if memory_monitor:
        await memory_monitor.stop()

@app.get("/memory")
async def get_memory_info():
    if memory_monitor:
        info = memory_monitor._get_memory_info()
        return {
            "rss_mb": info["rss_mb"],
            "system_percent": info["system_percent"],
        }
    return {"error": "Memory monitor not initialized"}
```

### 提供内存监控API端点

```python
@app.get("/api/memory/status")
async def memory_status():
    """获取当前内存状态"""
    monitor = MemoryMonitor()
    info = monitor._get_memory_info()
    
    return {
        "process": {
            "rss_mb": round(info["rss_mb"], 2),
            "vms_mb": round(info["vms_mb"], 2),
        },
        "system": {
            "total_mb": round(info["system_total_mb"], 2),
            "used_mb": round(info["system_used_mb"], 2),
            "available_mb": round(info["system_available_mb"], 2),
            "percent": round(info["system_percent"], 1),
        },
        "timestamp": info["timestamp"],
    }
```

## 配置选项

### 报告间隔

根据应用需求调整报告间隔：

- **开发环境**: 10-30秒
- **测试环境**: 30-60秒  
- **生产环境**: 60-300秒

### 报告阈值

设置合适的内存变化阈值，避免频繁报告：

- **内存敏感应用**: 10-50MB
- **一般应用**: 50-200MB
- **内存密集型应用**: 200-500MB

### 详细报告模式

- **详细模式**: 包含进程和系统的完整内存信息
- **简洁模式**: 只输出关键内存指标

## 日志输出

内存报告会通过logger输出，格式如下：

### 详细报告示例

```
==================================================
内存使用报告 - 2026-02-18 16:52:04
--------------------------------------------------
进程内存:
  RSS内存: 34.48 MB
  VMS内存: 22.70 MB
系统内存:
  总内存: 32189.29 MB
  已使用: 31361.02 MB (97.4%)
  可用内存: 828.27 MB
==================================================
```

### 简洁报告示例

```
内存使用: RSS=34.48MB, VMS=22.70MB, 系统=97.4%
```

## 最佳实践

### 1. 根据环境配置

```python
import os

def get_memory_monitor_config():
    env = os.getenv("ENVIRONMENT", "development")
    
    if env == "production":
        return {
            "interval": 300.0,  # 5分钟
            "report_threshold_mb": 200.0,
            "detailed_report": False,
        }
    elif env == "staging":
        return {
            "interval": 60.0,
            "report_threshold_mb": 100.0,
            "detailed_report": True,
        }
    else:  # development
        return {
            "interval": 30.0,
            "report_threshold_mb": 50.0,
            "detailed_report": True,
        }
```

### 2. 错误处理

```python
async def safe_memory_monitoring():
    try:
        monitor = MemoryMonitor(interval=60.0)
        await monitor.start()
        
        # 主应用逻辑
        await run_application()
        
    except Exception as e:
        logger.error(f"内存监控出错: {e}")
    finally:
        if monitor:
            await monitor.stop()
```

### 3. 性能考虑

- 监控间隔不宜过短（建议≥10秒）
- 在生产环境中使用简洁报告模式
- 考虑使用阈值过滤，避免频繁报告

## 故障排除

### 常见问题

1. **psutil未安装**

   ```
   ImportError: No module named 'psutil'
   ```

   **解决方案**: `pip install psutil`

2. **内存报告不准确**
   - 确保有足够的权限读取进程信息
   - 检查psutil版本是否兼容

3. **监控任务未停止**
   - 确保在应用关闭时调用`await monitor.stop()`
   - 使用上下文管理器自动管理生命周期

### 调试模式

```python
import logging
logging.getLogger("utils.memory_monitor").setLevel(logging.DEBUG)
```

## 示例代码

更多完整示例请参考：

- `examples/memory_monitor_example.py` - 完整的使用示例
- `test_memory_monitor.py` - 单元测试示例

## 扩展功能

### 自定义回调

```python
def custom_memory_callback(info: dict):
    # 发送到监控系统
    send_to_monitoring_system({
        "metric": "memory.usage",
        "value": info["rss_mb"],
        "tags": {"type": "process"},
    })
    
    # 触发警报
    if info["system_percent"] > 90:
        send_alert("系统内存使用率超过90%")

# 使用自定义回调
asyncio.create_task(periodic_memory_report(
    interval=60.0,
    callback=custom_memory_callback,
))
```

### 集成到现有监控系统

```python
class IntegratedMemoryMonitor(MemoryMonitor):
    def __init__(self, monitoring_client, **kwargs):
        super().__init__(**kwargs)
        self.monitoring_client = monitoring_client
    
    async def _monitor_loop(self):
        while self._running:
            memory_info = self._get_memory_info()
            
            # 发送到监控系统
            self.monitoring_client.record_memory_metrics(
                rss_mb=memory_info["rss_mb"],
                system_percent=memory_info["system_percent"],
            )
            
            # 调用父类方法输出日志
            await super()._monitor_loop()
```

## 许可证

本模块遵循项目原有许可证。
