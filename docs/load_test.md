# 异步压力测试工具使用指南

## 概述

`load_test.py` 是一个基于异步和高性能 HTTP 客户端的压力测试 CLI 工具，专门用于对 API 接口进行性能测试。该工具使用 `httpx` 作为异步 HTTP 客户端，使用 `rich` 库提供美观的表格输出，能够测量平均响应时间（包含服务器和网络两部分）并提供详细的性能统计。

## 功能特性

- **异步高性能压测**：使用 `asyncio` + `httpx.AsyncClient` 实现高并发请求
- **全面的统计指标**：
  - 平均、最小、最大、中位数响应时间
  - P95、P99 分位响应时间
  - 成功率、RPS（每秒请求数）、总测试时间
- **灵活的请求配置**：
  - 支持 GET、POST、PUT、DELETE、PATCH、HEAD 方法
  - 自定义请求头
  - JSON 请求体和表单数据
  - 可配置超时时间
- **可视化输出**：
  - 实时进度条显示压测进度
  - Rich 表格展示统计结果
  - 性能评估（优秀/良好/一般/较差）

## 安装与依赖

项目已包含所需依赖，无需额外安装：

```toml
# pyproject.toml 中的相关依赖
dependencies = [
    "httpx>=0.27.0",    # 高性能异步 HTTP 客户端
    "rich>=14.3.2",     # 美观的终端输出
]
```

如果需要在其他项目中使用，可通过以下命令安装：

```bash
uv add httpx rich
```

## 使用方法

### 基本命令

```bash
python tests/load_test.py [URL] [选项]
```

### 参数说明

| 参数 | 缩写 | 默认值 | 描述 |
|------|------|--------|------|
| `url` | - | **必选** | 要测试的 API 接口 URL |
| `--concurrent` | `-c` | 10 | 并发连接数 |
| `--requests` | `-n` | 100 | 总请求数 |
| `--method` | `-m` | GET | HTTP 方法（GET/POST/PUT/DELETE/PATCH/HEAD） |
| `--header` | `-H` | - | 请求头，格式：`"Key: Value"`，可多次使用 |
| `--json` | - | - | JSON 请求体，例如：`'{"key": "value"}'` |
| `--data` | - | - | 表单数据，格式：`"key=value"`，可多次使用 |
| `--timeout` | - | 30 | 请求超时时间（秒） |
| `--no-progress` | - | false | 不显示进度条 |
| `--verbose` | - | false | 显示详细错误信息 |

## 使用示例

### 示例 1：测试 GET 接口

```bash
# 基本用法：测试歌单列表接口
python tests/load_test.py http://localhost:8000/api/songlists

# 增加并发和请求数
python tests/load_test.py http://localhost:8000/api/songlists -c 20 -n 1000
```

### 示例 2：测试 POST 接口 with JSON

```bash
# 创建用户接口
python tests/load_test.py http://localhost:8000/api/users -m POST \
  --json '{"name": "test", "email": "test@example.com"}'

# 登录接口带自定义请求头
python tests/load_test.py http://localhost:8000/api/login -m POST \
  -H "Content-Type: application/json" \
  --json '{"username": "admin", "password": "secret"}'
```

### 示例 3：测试表单提交

```bash
# 表单数据提交
python tests/load_test.py http://localhost:8000/api/upload -m POST \
  --data "name=test" \
  --data "file=example.txt"
```

### 示例 4：自定义配置

```bash
# 禁用进度条，显示详细错误
python tests/load_test.py http://localhost:8000/api/songlists \
  -c 50 -n 5000 --timeout 10 --no-progress --verbose
```

## 输出说明

### 1. 进度显示

压测过程中会显示实时进度条：
```
发送请求... ████████████████████████████████████ 100%
```

### 2. 统计结果表格

**主统计表**：
```
+------------------------------------------+
| 指标                           |      值 |
|--------------------------------+---------|
| 总请求数                       |     100 |
| 成功请求数                     |      98 |
| 失败请求数                     |       2 |
| 成功率                         |  98.00% |
| 总测试时间                     |   5.23秒 |
| 每秒请求数 (RPS)               |   19.12 |
+------------------------------------------+
```

**响应时间统计表**：
```
+------------------------------------------------+
| 统计项               | 时间 (秒) | 时间 (毫秒) |
|----------------------+-----------+-------------|
| 平均响应时间         |    0.0543 |    54.30 ms |
| 最小响应时间         |    0.0231 |    23.10 ms |
| 最大响应时间         |    0.5120 |   512.00 ms |
| 中位数响应时间       |    0.0452 |    45.20 ms |
| P95响应时间          |    0.2345 |   234.50 ms |
| P99响应时间          |    0.4123 |   412.30 ms |
+------------------------------------------------+
```

### 3. 性能评估

基于平均响应时间的评估：
- **优秀**：< 100ms
- **良好**：100-300ms
- **一般**：300-1000ms
- **较差**：> 1000ms

基于 RPS 的评估：
- **优秀**：> 100 RPS
- **良好**：50-100 RPS
- **一般**：10-50 RPS
- **较差**：< 10 RPS

## 性能指标解读

### 响应时间指标

1. **平均响应时间**：所有成功请求响应时间的算术平均值
2. **最小响应时间**：最快的一次响应时间
3. **最大响应时间**：最慢的一次响应时间
4. **中位数响应时间**：排序后位于中间位置的响应时间
5. **P95响应时间**：95% 的请求比这个时间快
6. **P99响应时间**：99% 的请求比这个时间快

### 吞吐量指标

- **RPS (Requests Per Second)**：每秒处理的请求数，衡量系统吞吐能力
- **成功率**：成功请求占总请求数的百分比

## 使用建议

### 1. 测试环境准备

```bash
# 1. 启动服务
uv run uvicorn main:app --host 0.0.0.0 --port 8000

# 2. 运行小规模测试（验证接口可用性）
python tests/load_test.py http://localhost:8000/api/songlists -n 10 -c 2

# 3. 运行正式压测
python tests/load_test.py http://localhost:8000/api/songlists -n 1000 -c 50
```

### 2. 参数选择建议

- **并发数 (`-c`)**：根据服务器配置调整，通常从 10 开始逐步增加
- **总请求数 (`-n`)**：至少 100 以上才能获得有意义的统计
- **超时时间 (`--timeout`)**：根据接口响应时间调整，避免因个别慢请求影响测试

### 3. 结果分析

1. **查看成功率**：低于 99% 可能需要检查服务稳定性
2. **关注 P95/P99**：这些指标更能反映用户体验
3. **对比不同配置**：调整并发数观察性能变化

## 注意事项

### 1. 资源限制

- 工具会根据并发数自动调整 HTTP 连接池大小
- 过高的并发数可能导致本地资源耗尽或服务拒绝连接
- 建议逐步增加并发数，观察性能变化

### 2. 网络影响

- 测试结果包含网络延迟，建议在局域网内测试
- 公网测试时注意网络波动对结果的影响

### 3. 服务保护

- 避免对生产环境进行高强度压测
- 压测前确保服务有足够的资源（CPU、内存、数据库连接等）

### 4. 错误处理

- 网络错误、超时、4xx/5xx 状态码都会计入失败统计
- 使用 `--verbose` 参数查看详细错误信息

## 进阶用法

### 1. 批量测试多个接口

```bash
#!/bin/bash
# batch_test.sh

APIS=(
  "http://localhost:8000/api/songlists"
  "http://localhost:8000/api/songs"
  "http://localhost:8000/api/tags"
)

for API in "${APIS[@]}"; do
  echo "测试: $API"
  python tests/load_test.py "$API" -n 500 -c 20 --no-progress
  echo "----------------------------------------"
done
```

### 2. 生成测试报告

```bash
# 将输出重定向到文件
python tests/load_test.py http://localhost:8000/api/songlists -n 1000 -c 50 > load_test_report.txt

# 提取关键指标
python tests/load_test.py http://localhost:8000/api/songlists -n 1000 -c 50 2>&1 | grep -E "(平均响应时间|成功率|RPS)"
```

## 故障排除

### Q1: 测试过程中出现大量超时

**可能原因**：
1. 服务器资源不足
2. 数据库连接池耗尽
3. 网络带宽限制

**解决方案**：
1. 降低并发数：`-c 10`
2. 增加超时时间：`--timeout 60`
3. 检查服务端日志

### Q2: 成功率低于预期

**可能原因**：
1. 接口参数错误
2. 认证/授权问题
3. 服务端异常

**解决方案**：
1. 使用 `--verbose` 查看详细错误
2. 先用浏览器或 curl 验证接口可用性
3. 检查请求头和请求体格式

### Q3: RPS 异常低

**可能原因**：
1. 服务器性能瓶颈
2. 网络延迟过高
3. 客户端资源限制

**解决方案**：
1. 在服务器本地测试排除网络影响
2. 监控服务器资源使用情况
3. 调整客户端连接池参数

## 版本历史

- **v1.0** (2026-02-27): 初始版本
  - 支持异步高并发压测
  - 提供丰富的统计指标
  - 支持多种 HTTP 方法和请求格式
  - 使用 Rich 库提供美观的输出

## 相关资源

- [httpx 官方文档](https://www.python-httpx.org/)
- [Rich 官方文档](https://rich.readthedocs.io/)
- [FastAPI 性能测试指南](https://fastapi.tiangolo.com/benchmarks/)

---

> **提示**：压测工具是性能优化的重要辅助，但真实的用户体验还需要结合业务场景、用户行为和系统监控综合评估。
