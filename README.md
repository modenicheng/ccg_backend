# CCG Backend

本文档仅描述当前代码仓库**已经实现**的能力，不包含未来规划。

## 项目概览

`ccg_backend` 是一个基于 FastAPI 的实时后端，当前提供：

- WebSocket 二进制事件接入与分发
- 心跳帧（PING/PONG）编解码与回包
- 音频帧二进制编解码工具（供协议层使用）
- 进程/系统内存定时监控能力
- 前端静态资源托管与 SPA 回退路由
- Rich + 文件滚动日志

## 运行方式

### 环境要求

- Python: `>=3.12`
- 依赖见 `pyproject.toml`

### 启动服务

服务入口：`main.py`

`main.py` 直接运行时使用：

- Host: `0.0.0.0`
- Port: `8000`

## 数据库迁移（Alembic）

本项目已接入 Alembic，并与 `db.models.Base.metadata` 对齐。

- 配置文件：`alembic.ini`
- 迁移目录：`alembic/versions/`
- 默认数据库 URL：读取 `.env` 中的 `DATABASE_URL`

常用流程（推荐通过 uv 执行）：

1. 生成迁移（基于当前 ORM 模型自动对比）
   - `uv run alembic revision --autogenerate -m "your_message"`
2. 应用到最新版本
   - `uv run alembic upgrade head`
3. 回滚一个版本
   - `uv run alembic downgrade -1`

说明：

- SQLite 开发默认使用 `sqlite+aiosqlite:///data/game.db`
- 预留 PostgreSQL：`postgresql+asyncpg://user:password@host:5432/dbname`

详细说明（推荐先读）：`docs/database_migration_guide.md`

日常最小流程（团队统一约定）：

1. 修改 ORM 模型（`db/models.py`）
2. 生成迁移：`uv run alembic revision --autogenerate -m "<简短说明>"`
3. 人工检查迁移脚本（尤其是删除列/改类型）
4. 本地执行：`uv run alembic upgrade head`
5. 提交代码：模型 + 迁移脚本一起提交

## HTTP / WebSocket 接口

### `GET /`

- 若存在前端构建产物 `../ccg_frontend/dist/index.html`，返回该页面。
- 若不存在，返回后端状态 JSON。

### `GET /{full_path:path}`（Catch-all）

- 优先返回 `dist` 下对应静态文件。
- 目录请求会尝试返回目录内 `index.html`。
- 不存在时回退到根 `index.html`（用于前端路由）。
- 包含路径越界防护（`resolve()` + 前缀校验）。

### `WebSocket /ws/`

连接后流程：

1. 服务端 `accept()` 并加入 `ClientManager`
2. 循环读取 `receive()` 消息
3. 二进制消息按首字节解析 `EventType`
4. 分发到 `handlers.handle(event, data, clients, websocket)`
5. 断连后移除客户端

> 当前文本消息分支仅保留占位（未实现 JSON 业务解析）。

## 事件系统

### 事件枚举（`utils/enumerations.py`）

- `OMIT = 0`
- `AUDIO_FRAME = 1`
- `META_DATA = 2`
- `HEARTBEAT = 3`
- `TIME_SYNC = 4`
- `MESSAGE = 255`（错误处理保留值）

当前已注册处理器：

- `HEARTBEAT`（见 `handlers/heartbeats.py`）

未注册的事件会在分发层抛出 `ValueError`。

## 二进制帧协议

实现位于 `utils/dataframe.py`。

### 公共字段

所有帧以网络字节序（big-endian，`struct` 的 `!` 前缀）编码，均包含：

- 1 字节：事件类型
- 8 字节：时间戳（毫秒，`uint64`）

### 心跳帧 `HeartbeatFrame`

格式：`!B B Q 8s Q Q Q Q`

- 1 字节：`event_type`（`HEARTBEAT`）
- 1 字节：`heartbeat_type`（`PING` / `PONG`）
- 8 字节：`timestamp`
- 8 字节：`uid`（固定 8 字节字符串）
- 8 字节：`t1`
- 8 字节：`t2`
- 8 字节：`t3`
- 8 字节：`t4`

心跳类型：

- `PING = 0`
- `PONG = 1`

处理逻辑（`handlers/heartbeats.py`）：

- 收到 `PING`：记录服务端接收时刻 `t2`，构造 `PONG` 并回发
- 收到 `PONG`：记录调试日志（用于后续时延/时钟分析）

## 客户端管理

实现：`client_manager/__init__.py`

提供能力：

- 连接集合管理：`push/pop/clear/is_empty`
- 单播：`send(client, bytes|dict)`
- 广播：`broadcast(message, except_clients=...)`
- 踢出连接：`kick(client, code, reason)`

## 内存监控

实现：`utils/memory_monitor.py`

主要能力：

- `MemoryMonitor` 异步监控器
  - 可配置 `interval`、`report_threshold_mb`、`detailed_report`
  - 支持 `start()` / `stop()`
  - 支持 `monitor_context()` 异步上下文
- `start_memory_monitoring(...)` 快捷启动
- `periodic_memory_report(...)` 轻量定时报告

在主服务中的行为：

- `startup` 自动启动监控（30s 间隔，20MB 阈值，详细模式）
- `shutdown` 自动停止监控

详细说明见：`docs/memory_monitor_usage.md`

## 日志

实现：`utils/logger.py`

- Rich 控制台输出
- 旋转文件日志（默认 `logs/app.log`）
- `init_logging()` 具备幂等初始化保护
- `get_logger(name)` 获取命名日志器

## 测试与示例

### 自动化测试

- `tests/test_audio_frame_encoding.py`
  - 验证 `AudioFrame.dump()/load()` 一致性

### 手动/示例脚本

- `test_memory_monitor.py`：内存监控功能脚本化验证
- `examples/memory_monitor_example.py`：集成示例

## 目录结构（核心）

- `main.py`：FastAPI 入口、WebSocket、静态资源路由、生命周期钩子
- `handlers/`：事件处理器与注册机制
- `client_manager/`：WebSocket 客户端集合管理
- `utils/`：协议帧、枚举、日志、内存监控、错误定义
- `tests/`：单元测试
- `docs/`：功能文档

## 当前边界与说明

- 文档中提到的房间/歌单业务流程目前**尚未在本仓库实现**。
- `EventType` 中的 `AUDIO_FRAME`、`META_DATA`、`TIME_SYNC`、`MESSAGE` 目前无对应 handler。

## 局内流程设计

1. 房主设定歌单信息，即对应的 QQ音乐歌单的 ID `songlistId`
2. 后端处理歌单信息
   1. 爬取所有的歌曲信息（可以不包括播放/下载链接）
   2. 洗牌（shuffle），生成随机播放列表
   3. 处理完后，生成房间ID
3. 玩家加入房间 `roomId`， 设定用户名 `username`
4. 加入的用户进行初始化
   - 后端请求 API 得到播放链接 -> 前端预加载音频
   - 后端请求 API 得到下载链接 -> 预下载到后端缓存，缓存完成后直接托管音频文件作为静态资源，前端再预加载
   - 前端预加载完成，信令告知后端，后端待所有用户均可以播放后通知前端状态改变
   - 后端开始预下载下一个音频
5. 轮次开始
   1. 后端统一信令 `play`， 附带将要播放的音频的元数据以便校验预加载音频是否正确
   2. 前端校验通过，开始播放
   3. 用户随时可以发送暂停 `suspend` 信令，后端收到之后广播给所有客户端；`suspend` 带有暂停时的播放进度和暂停时的校准时间戳
6. 音频第一次被停止（不管是手动的还是播完了）
   1. 前端预加载下一个音频
   2. 前端根据 `suspend` 中的播放进度同步自身，1s 内若有多个信令，按时间戳最早的那个计算
