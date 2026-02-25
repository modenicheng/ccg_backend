# CCG Backend

本文档仅描述当前代码仓库**已经实现**的能力，不包含未来规划。

## qqmusic

`9561851623` 是可用于测试的 QQ 音乐的歌单 ID

## 项目概览

`ccg_backend` 是一个基于 FastAPI 的实时后端，当前提供：

- WebSocket 二进制事件接入与分发
- 心跳帧（PING/PONG）编解码与回包
- 音频帧二进制编解码工具（供协议层使用）
- 进程/系统内存定时监控能力
- 前端静态资源托管与 SPA 回退路由
- Rich + 文件滚动日志
- Redis 缓存集成（房间状态管理、会话管理）

## 运行方式

### 环境要求

- Python: `>=3.12`
- 依赖见 `pyproject.toml`
- Redis: `>=7.0` (可选，用于房间状态管理和会话管理)
- PostgreSQL （不得不必须用这个，项目里用了些 psql 的特性）

### 配置方法

复制 `.env.template` 并重命名为 `.env` ，在其中填写配置即可。

> 约定：所有环境变量必须以 `CCG_` 为前缀，用于区分其他项目。

### 启动服务

服务入口：`main.py`

`main.py` 直接运行时使用：

- Host: `0.0.0.0`
- Port: `8000`

或你可以用以下方式启动一个自动重启的开发服务器：

```bash
uv run uvicorn main:app --reload --port 8000
```

## 数据库迁移（Alembic）

本项目已接入 Alembic，并与 `db.models.Base.metadata` 对齐。

- 配置文件：`alembic.ini`
- 迁移目录：`alembic/versions/`
- 默认数据库 URL：读取 `.env` 中的 `CCG_DATABASE_URL`

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

## Redis 缓存集成

本项目已集成 Redis 缓存，用于房间状态管理和会话管理。

### 配置方式

- 默认 Redis URL：`redis://localhost:6379/0`
- 可通过环境变量 `CCG_REDIS_URL` 自定义配置

此外，`mq/tasks.py` 使用以下环境变量：

- `CCG_QQ_MUSIC_COOKIE`：QQ 音乐 Cookie 字符串（用于需要登录态的请求）
- `CCG_SONGLIST_FETCH_CONCURRENCY`：分页抓取并发上限（默认 `8`）
- `CCG_SONGLIST_FETCH_RETRIES`：单页抓取重试次数（默认 `5`）
- `CCG_SONGLIST_FETCH_BACKOFF_SECONDS`：重试退避基数秒数（默认 `0.4`）
- `CCG_AUDIO_DOWNLOAD_RETRIES`：音频下载重试次数（默认 `3`）
- `CCG_AUDIO_DOWNLOAD_BACKOFF_SECONDS`：音频下载重试退避基数秒数（默认 `0.4`）
- `CCG_AUDIO_DOWNLOAD_DIR`：音频默认下载目录（默认 `assets/audio`，未显式传 `save_path` 时保存为 `assets/audio/<mid>.<ext>`）
- `CCG_SONG_URL_RETRIES`：歌曲 URL 获取重试次数（默认 `3`）
- `CCG_SONG_URL_BACKOFF_SECONDS`：歌曲 URL 获取重试退避基数秒数（默认 `0.4`）

端到端链路文档（歌单 ID → 入库 → 首曲缓存）：`docs/songlist_cache_flow.md`

#### 如何获取 `CCG_QQ_MUSIC_COOKIE`

1. 在浏览器登录 QQ 音乐网页版（建议使用与日常账号一致的浏览器配置文件）。
2. 打开开发者工具（F12）→ `Network`。
3. 刷新页面后，点开任意发往 `y.qq.com` / `u.y.qq.com` 的请求。
4. 在请求头中找到 `Cookie`，复制完整字符串。
5. 粘贴到 `.env` 的 `CCG_QQ_MUSIC_COOKIE=` 后面（不要加额外引号）。

建议：

- 该值属于敏感凭据，请勿提交到 Git 仓库。
- Cookie 失效后需要重新获取并更新。

### 核心功能

- **房间状态管理**：存储房间基本信息、玩家列表、准备状态、歌曲队列等
- **会话管理**：存储用户令牌与房间/玩家的映射关系
- **抢答队列**：管理玩家抢答顺序
- **过期时间**：房间数据默认 6 小时过期，会话数据默认 24 小时过期

### 目录结构

- `redis/connection.py`：Redis 连接管理
- `redis/utils.py`：Redis 操作工具类（房间管理、会话管理）

### 使用方式

```python
from redis.connection import get_redis
from redis.utils import room_manager, session_manager

# 获取 Redis 客户端
redis_client = get_redis()

# 房间管理
room_manager.create_room(room_id, host_player_id)
room_manager.add_player(room_id, player_id)
room_manager.set_player_ready(room_id, player_id, True)

# 会话管理
session_manager.create_session(token, room_id, player_id)
session_data = session_manager.get_session(token)
```

### 注意事项

- Redis 为可选依赖，若未连接 Redis，部分实时功能可能不可用
- 生产环境建议使用稳定的 Redis 服务
- 开发环境可使用本地 Redis 或 Docker 容器运行 Redis

## HTTP / WebSocket 接口

### `POST /api/room/`

- 创建房间。
- 返回：`roomId`、`playerId`、`token`。

### `GET /api/room/{roomid}`

- 获取房间信息（房主、状态、玩家列表、歌单队列、标签配置等）。

### `PATCH /api/room/{roomid}`

- 更新房间设置。
- 当前支持：`songQueue`、`title`、`description`、`tagGroups`。

### `GET /`

- 若存在前端构建产物 `../ccg_frontend/dist/index.html`，返回该页面。
- 若不存在，返回后端状态 JSON。

### `GET /{full_path:path}`（Catch-all）

- 优先返回 `dist` 下对应静态文件。
- 目录请求会尝试返回目录内 `index.html`。
- 不存在时回退到根 `index.html`（用于前端路由）。
- 包含路径越界防护（`resolve()` + 前缀校验）。

### `WebSocket /ws/{roomid}`

连接后流程：

1. 服务端 `accept()` 并校验房间是否存在
2. 可选校验 `token`（存在时要求与 `roomid` 匹配）
3. 加入对应房间的 `ClientManager`
4. 循环读取 `receive()` 消息
5. 二进制消息按首字节解析 `EventType`
6. 分发到 `handlers.handle(event, data, clients, websocket, room_id)`
7. 断连后从对应房间移除客户端

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

- 按房间连接集合管理：`push(room_id, client)` / `pop(room_id, client)`
- 单播：`send(client, bytes|dict)`
- 房间广播：`broadcast(room_id, message, except_clients=...)`
- 踢出连接：`kick(room_id, client, code, reason)`

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
  - `docs/songlist_cache_flow.md`：歌单入库与首曲缓存链路（含设计思路、优势与排障）

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
