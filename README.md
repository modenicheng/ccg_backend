# CCG Backend

本文档仅描述当前代码仓库**已经实现**的能力，不包含未来规划。

## 项目架构与设计

### 整体架构概览

CCG Backend 是一个基于 FastAPI 的实时多人游戏后端系统，采用异步架构设计，支持 WebSocket 实时通信、Redis 缓存、PostgreSQL 数据库和分布式任务队列。系统采用分层设计，分为接入层、业务逻辑层、数据访问层和基础设施层。

### 核心模块结构

#### 1. 项目入口 (`main.py`)

- **FastAPI 应用入口**：配置生命周期管理、中间件、路由注册
- **WebSocket 端点** (`/ws/{roomid}`)：处理实时游戏事件连接
- **静态文件服务**：支持前端 SPA 应用托管和路由回退
- **生命周期管理**：使用 `@asynccontextmanager` 管理启动/关闭事件
- **内存监控集成**：自动启动 `MemoryMonitor` 进行性能监控

#### 2. 数据库入口 (`db/`)

- **`models.py`**：SQLAlchemy ORM 模型定义（User、Room、Song、Songlist、TagGroup、Score、PlayerAnswer 等）
- **`session.py`**：异步数据库会话工厂，支持 SQLite/PostgreSQL 连接池
- **`crud.py`**：通用数据库操作函数，提供常用查询封装
- **设计特点**：使用 SQLAlchemy 2.0+ 异步 API，支持自动外键约束和索引

#### 3. 缓存入口 (`cache/`)

- **`connection.py`**：`RedisClient` 类管理 Redis 连接池
- **`room_cache.py`**：房间状态缓存操作（6小时 TTL）
- **`schemas.py`**：缓存数据结构 Pydantic 定义，部分模型可以继承 `schemas/` 目录下的已有模型并与 `RedisModel` Mixin
- **`utils.py`**：~~缓存工具函数（房间管理、会话管理、抢答队列、播放状态）~~【这个目前不用，大部分状态直接落库】
- **`file_cache.py`**：音频文件内存缓存管理（LRU 缓存，支持文件内容缓存和范围请求）
- **设计模式**：统一键命名规范 (`RedisKeys`)，自动过期管理

#### 4. 数据格式定义 (`schemas/`)

- **集中定义约定**：所有数据格式统一定义在 `schemas/` 目录下
- **分离原则**：
  - `schemas/*.py`：HTTP API 请求/响应格式（使用 `ConfigDict(from_attributes=True)` 支持 ORM 转换）
  - `schemas/ws_messages/*.py`：WebSocket 专用消息格式（继承 `MessageBase` 基类）
- **目录结构**：
  - `base_message.py`：WebSocket 消息基类和通用消息定义
  - `ws_messages/`：按功能模块划分（`room_schemas.py`、`playback_schemas.py`、`judge_schemas.py`、`round_event_schemas.py`、`error_schemas.py`）
  - API schemas：`room.py`、`song.py`、`songlist.py`、`tag.py`、`user.py`
- **类型安全**：严格的事件类型枚举映射，前后端数据格式一致性保证

#### 5. WebSocket 处理器 (`handlers/`)

- **注册机制**：使用装饰器模式，`@regist(event, data_validator)` 注册事件处理器
- **事件分发**：`handle()` 函数根据事件类型调用注册处理器，支持前置 Pydantic 验证
- **模块组织**：
  - `__init__.py`：事件注册和分发核心逻辑
  - `game_events.py`：连接生命周期管理（`on_connect`、`on_disconnect`），原游戏事件处理器已迁移至按事件ID范围组织的文件中
  - `audio_events_2x.py`：音频控制事件（20-29）
  - `round_events_3x.py`：回合事件（30-39）
  - `heartbeats.py`：心跳事件处理（PING/PONG）
- **前置校验设计**：处理器注册时 **必须** 指定 `data_validator`，在业务逻辑执行前自动进行 Pydantic 数据验证，提前发现格式错误

#### 6. 路由 (`router/`)

- **模块化设计**：使用 FastAPI `APIRouter` 按功能划分
- **路由文件**：
  - `room.py`：房间管理 API
  - `song.py`：歌曲管理 API
  - `songlist.py`：歌单管理 API
  - `tags.py`：标签管理 API
  - `room_songs.py`：房间歌曲管理 API
- **设计特点**：依赖注入数据库会话，统一错误处理，RESTful API 设计

#### 7. 工具类 (`utils/`)

- **`enumerations.py`**：枚举类型定义（`EventType`、`GameEventType`、`ErrorEventType`、`AudioEncoding`、`HeartbeatType`、`MusicPlatform`、`RoomStatus`）
- **`logger.py`**：Rich 控制台输出 + 文件滚动日志，`init_logging()` 幂等初始化
- **`memory_monitor.py`**：异步内存监控器，支持阈值报告和上下文监控
- **`dataframe.py`**：二进制数据帧处理（心跳帧、音频帧等协议实现）
- **`cookie.py`**：Cookie 解析工具
- **`ts.py`**：时间戳工具函数

#### 8. 异步任务队列 (`mq/`)

- **技术栈**：Huey + Redis
- **`tasks.py`**：异步任务定义（歌单抓取、音频下载、格式转换等）
- **环境配置**：支持并发控制、重试策略、退避时间等参数配置

#### 9. QQ音乐API客户端 (`qq_api/`)

- **技术栈**：Node.js + Express
- **功能**：QQ 音乐 API 代理，处理歌曲元数据获取和音频链接解析

#### 10. 客户端管理 (`client_manager/`)

- **`__init__.py`**：`ClientManager` 类管理 WebSocket 客户端集合
- **核心能力**：
  - 按房间连接集合管理：`push()` / `pop()`
  - 单播：`send(client, bytes|dict)`
  - 房间广播：`broadcast(room_id, message, except_clients=...)`
  - 踢出连接：`kick(room_id, client, code, reason)`

### 设计规范与约定

#### 数据格式集中定义

- **约定**：所有数据格式定义在 `schemas/` 目录
- **分离**：API schemas 和 WebSocket 消息 schemas 分开维护
- **验证**：使用 Pydantic 进行数据验证和序列化
- **一致性**：前后端共享相同的 schema 定义，确保类型安全

#### WebSocket 事件注册处理机制

- **装饰器模式**：使用 `@regist` 装饰器注册事件处理器
- **类型映射**：事件类型与 `GameEventType` 枚举严格对应
- **数据验证**：支持前置 Pydantic 验证，自动处理验证错误
- **错误处理**：统一的错误响应格式 (`WebSocketErrorEvent`)

#### 前置 Pydantic 校验原理

- **设计思路**：在事件处理器调用前进行数据验证，减少业务逻辑中的验证代码
- **实现方式**：`handle()` 函数根据注册的 `data_validator` 自动调用 `model_validate()`
- **优势**：提前发现数据格式错误，提供清晰的错误信息，提高代码健壮性

#### 缓存策略设计

- **房间状态**：6 小时 TTL，存储玩家列表、准备状态、歌曲队列等
- **会话管理**：24 小时 TTL，存储用户令牌与房间/玩家映射
- **播放状态**：实时记录播放进度、播放状态（播放/暂停/跳转）
- **抢答队列**：管理玩家抢答顺序，支持队列操作

#### 数据库迁移管理

- **工具**：Alembic 迁移框架
- **配置**：`alembic.ini` 读取 `.env` 中的 `CCG_DATABASE_URL`
- **工作流**：ORM 模型变更 → 自动生成迁移脚本 → 人工检查 → 执行迁移

### 工作流程

#### WebSocket 连接流程

1. 客户端通过 HTTP API 创建/加入房间，获取认证信息
2. 建立 WebSocket 连接 (`/ws/{roomid}`)，携带认证凭证
3. 服务端验证房间存在性和用户权限
4. 加入对应房间的 `ClientManager`，触发 `on_connect` 事件
5. 循环接收消息，根据消息类型（二进制/JSON）解析事件类型
6. 调用 `handlers.handle()` 分发给注册的事件处理器
7. 处理器执行业务逻辑，可能更新数据库、缓存，并广播状态变更
8. 断连后从 `ClientManager` 移除，触发 `on_disconnect` 事件

#### 事件处理流程

1. 客户端发送 JSON 或二进制消息
2. 服务端解析事件类型 (`GameEventType` 或 `EventType`)
3. 查找注册的处理器和对应的数据验证器
4. 执行前置 Pydantic 数据验证
5. 调用处理器函数，传入验证后的数据
6. 处理器执行业务逻辑，可能涉及：
   - 数据库 CRUD 操作
   - Redis 缓存更新
   - 通过 `ClientManager` 广播消息
   - 状态同步和持久化
7. 返回处理结果或错误信息

#### 歌单处理流程

1. 房主提交 QQ 音乐歌单 ID
2. 创建异步任务（`mq.tasks.fetch_songlist_task`）
3. 任务执行：分页抓取歌单元数据 → 歌曲信息入库 → 生成随机播放顺序
4. 任务状态可通过 API 查询
5. 歌曲音频预下载和缓存管理

### 技术栈总结

- **Web 框架**：FastAPI（异步支持、自动 OpenAPI 文档）
- **数据库**：SQLAlchemy 2.0+（异步 ORM）、PostgreSQL/SQLite
- **缓存**：Redis（房间状态、会话管理、任务队列）
- **消息协议**：自定义二进制帧协议 + JSON 事件协议
- **任务队列**：Huey（异步任务处理）
- **监控**：自定义内存监控、Rich 日志系统
- **外部集成**：QQ 音乐 API（通过 Node.js 代理）
- **部署**：Uvicorn ASGI 服务器

### 扩展性与维护性

- **模块化设计**：各功能模块独立，便于维护和测试
- **类型安全**：全面使用 Pydantic 和枚举，减少运行时错误
- **配置驱动**：环境变量统一前缀 (`CCG_`)，支持灵活部署
- **文档完整**：详细的 API 文档、设计文档和迁移指南
- **测试覆盖**：单元测试、集成测试、负载测试工具

## 项目功能概览

`ccg_backend` 是一个基于 FastAPI 的实时多人游戏后端，核心功能包括：

- **实时通信**：WebSocket 二进制/JSON 事件接入与分发，心跳帧处理
- **数据管理**：PostgreSQL 数据库集成，Redis 缓存（房间状态、会话、抢答队列）
- **音频处理**：音频文件内存缓存（LRU），支持 Range 请求
- **任务队列**：异步歌单抓取、音频下载、格式转换（Huey + Redis）
- **游戏逻辑**：播放控制、抢答、评分、回合事件处理
- **客户端管理**：按房间分组连接，支持广播、单播、踢出
- **监控日志**：内存监控、Rich 日志系统、性能监控

## 整体设计

### 设计规范

#### 有关数据格式

**统一定义在 `schemas/` 目录下**！！！

- `schemas/*.py` 原则上讲是只用于 API 的
- `schemas/ws_messages/*.py` 只用于 WebSocket 信令

#### 有关 WebSocket 事件处理器（handlers）

基本依据 `DESIGN.md` 里表格和事件id的划分方式分文件，避免单文件过长难以维护。当前实际组织方式：
- **按事件ID范围分文件**：`audio_events_2x.py`（事件20-29）、`round_events_3x.py`（事件30-39）
- **生命周期管理**：`game_events.py` 包含 `on_connect`、`on_disconnect` 函数
- **心跳处理**：`heartbeats.py` 处理 `HEARTBEAT` 事件

> [!IMPORTANT]
> 这个表更新可能不及时，务必依据 `DESIGN.md` 的设计进行！

| 事件名               | 类型值 | 方向     | 说明                                                         | 服务器行为        |
|----------------------|--------|----------|--------------------------------------------------------------| ------------      |
| `ROOM_JOIN`          | 11     | C→S      | 加入房间，附带房间ID、用户名                                 | state update      |
| `ROOM_STATE`         | 12     | S→C      | 推送完整房间状态（玩家列表、准备状态、歌曲列表、标签组等）   | broadcast         |
| `GAME_OVER`          | 13     | S→C      | 游戏结束，展示最终排名                                       | state & broadcast |
| `START_POS_UPDATE`   | 14     | C→S      | 房主更新起始位置百分比 (0-80)                                | state & broadcast |
| `KICK_USER`          | 15     | C→S      | 房主踢人                                                     | state & broadcast |
| `PLAYER_LEAVE`       | 16     | C→S      | 玩家离开房间                                                 | state & broadcast |

| 事件名               | 类型值 | 方向     | 说明                                                         | 服务器行为        |
|----------------------|--------|----------|--------------------------------------------------------------| ------------      |
| `PLAY`               | 20     | S→C      | 开始播放，包含音频URL、歌曲元数据、轮次索引、标签组结构      | state & broadcast |
| `PAUSE`              | 21     | S→C      | 暂停播放（由抢答或房主触发），可包含播放进度（毫秒）         | state & broadcast |
| `SEEK`               | 22     | C→S      | 调整播放进度，但不改变播放状态                               | broadcast         |
| `PRELOAD_AUDIO`      | 23     | S→C      | 音频预加载事件，通知客户端预加载下一首歌曲                   | state & broadcast |

| 事件名               | 类型值 | 方向     | 说明                                                         | 服务器行为        |
|----------------------|--------|----------|--------------------------------------------------------------| ------------      |
| `PLAYER_READY`       | 30     | C→S      | 玩家准备/取消准备   弃用                                     | state & broadcast |
| `GAME_START`         | 31     | S→C      | 房主开始游戏，禁止新玩家加入（断线重连可以）                 | state & broadcast |
| `ROUND_START`        | 32     | S→C      | 回合开始，触发倒计时（3,2,1）                                | state & broadcast |
| `ATTEMPT_ANSWER`     | 33     | C→S      | 玩家抢答，触发暂停和入队                                     | state & broadcast |
| `YOUR_TURN`          | 34     | S→C      | 广播通知指定玩家开始作答，包含剩余时间（前端显示xxx正在作答）| state & broadcast |
| `SUBMIT_ANSWER`      | 35     | C→S      | 玩家提交勾选的标签ID列表及精准描述文本                       | state & broadcast |
| `ANSWER_BROADCAST`   | 36     | S→C      | 广播某玩家提交的答案（匿名或带玩家名，不含正确性）           | state & broadcast |
| `ANSWER_QUEUE`       | 37     | S→C      | 广播当前抢答队列顺序（用于前端展示排队状态）                 | state & broadcast |
| `ROUND_END`          | 38     | S→C      | 回合结束，准备下一轮                                         | state & broadcast |

| 事件名               | 类型值 | 方向     | 说明                                                         | 服务器行为        |
|----------------------|--------|----------|--------------------------------------------------------------| ------------      |
| `JUDGING`            | 40     | S→C      | 进入判分环节，房主端显示标准答案区（含标签组和描述候选）     | state & broadcast |
| `JUDGE_SUBMIT`       | 41     | C→S      | 房主提交正确答案标签ID列表和描述ID列表（或“无描述”）         | state & broadcast |
| `SCORE_UPDATE`       | 42     | S→C      | 更新积分榜                                                   | state & broadcast |
| `SKIP_ROUND`         | 43     | C→S      | 房主跳过当前回合（不计分）                                   | state & broadcast |

## qqmusic

`9561851623` 是可用于测试的 QQ 音乐的歌单 ID


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

## 缓存集成（Redis）

本项目已集成 Redis 缓存，用于房间状态管理、会话管理、抢答队列和播放状态管理。

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
- `CCG_ASSET_CACHE_MAX_ITEMS`：音频文件内存缓存最大条目数（默认 `64`）

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
- **播放状态管理**：记录当前播放进度、播放状态（播放/暂停/跳转）
- **音频文件缓存**：内存 LRU 缓存音频文件内容，支持 Range 请求，减少磁盘 IO
- **过期时间**：房间数据默认 6 小时过期，会话数据默认 24 小时过期

### 目录结构

- `cache/connection.py`：Redis 连接管理
- `cache/utils.py`：Redis 操作工具类（目前大部分状态直接落库，Redis主要用于实时状态同步）
- `cache/schemas.py`：缓存数据结构定义
- `cache/file_cache.py`：音频文件内存缓存管理（LRU 缓存，支持文件内容缓存和范围请求）

### 使用方式

```python
from cache.connection import redis_client
from cache.utils import room_manager, session_manager

# 获取 Redis 客户端（异步）
redis = await redis_client.get_client()
if redis:
    # 执行 Redis 命令
    await redis.ping()

# 房间管理
await room_manager.create_room(room_id, host_player_id)
await room_manager.add_player(room_id, player_id)
await room_manager.set_player_ready(room_id, player_id, True)
await room_manager.update_playback_state(room_id, round_state="playing", progress_ms=0)

# 会话管理
await session_manager.create_session(token, room_id, player_id)
session_data = await session_manager.get_session(token)
```

### 注意事项

- Redis 为可选依赖，若未连接 Redis，部分实时功能可能不可用
- 生产环境建议使用稳定的 Redis 服务
- 开发环境可使用本地 Redis 或 Docker 容器运行 Redis

## HTTP / WebSocket 接口

### 房间管理 (`/api/room`)

- `POST /api/room/`：创建房间，返回 `room_id`、`host`（房主信息）
- `POST /api/room/{roomid}`：加入房间，需要 `username`，返回 `room_id`、`user`（用户信息）
- `GET /api/room/{roomid}`：获取房间详细信息（房主、状态、玩家列表、标签组等）
- `PATCH /api/room/{roomid}`：更新房间设置，支持 `song_queue`、`title`、`tag_group_ids`、`tag_groups`

### 标签管理 (`/api/tags`)

- `POST /api/tags/`：批量创建标签
- `GET /api/tags/`：获取标签列表（分页）
- `PATCH /api/tags/{tag_id}`：更新标签名称
- `DELETE /api/tags/{tag_id}`：删除标签

### 标签组管理 (`/api/tags/groups/`)

- `GET /api/tags/groups/`：获取标签组列表（分页）
- `POST /api/tags/groups/`：创建标签组，可关联现有标签或新建标签
- `PATCH /api/tags/groups/`：更新标签组（添加/移除标签、修改名称/描述）
- `DELETE /api/tags/groups/{group_id}`：删除标签组

### 歌曲管理 (`/api/songs`)

- `GET /api/songs/`：获取歌曲列表（分页、关键词搜索）
- `POST /api/songs/`：创建歌曲记录
- `GET /api/songs/{song_id}`：获取歌曲详情
- `PUT /api/songs/{song_id}`：更新歌曲信息
- `DELETE /api/songs/{song_id}`：删除歌曲
- `GET /api/songs/cache/{song_id}`：获取缓存的音频文件（支持 Range 请求）
- `POST /api/songs/cache/{song_id}`：触发音频缓存任务

### 歌单管理 (`/api/songlists`)

- `GET /api/songlists/`：获取歌单列表（分页、关键词搜索）
- `POST /api/songlists/`：从 QQ 音乐歌单 ID 创建歌单（异步任务）
- `GET /api/songlists/task/{task_id}`：查询歌单创建任务状态
- `GET /api/songlists/{songlist_id}`：获取歌单详情（包含歌曲列表）
- `PUT /api/songlists/{songlist_id}`：更新歌单信息
- `DELETE /api/songlists/{songlist_id}`：删除歌单

### 房间歌曲管理 (`/api/rooms/{roomid}/songs`)

- `GET /api/rooms/{roomid}/songs/`：获取房间内的歌曲列表（分页）
- `POST /api/rooms/{roomid}/songs/`：添加歌曲到房间
- `DELETE /api/rooms/{roomid}/songs/`：从房间移除指定歌曲
- `PUT /api/rooms/{roomid}/songs/`：批量更新歌曲顺序
- `DELETE /api/rooms/{roomid}/songs/all`：清空房间所有歌曲
- `GET /api/rooms/{roomid}/songs/{songid}`：获取房间内特定歌曲详情

### 静态文件服务

- `GET /`：若存在前端构建产物 `../ccg_frontend/dist/index.html`，返回该页面；否则返回后端状态 JSON
- `GET /{full_path:path}`（Catch-all）：优先返回 `dist` 下对应静态文件；目录请求尝试返回 `index.html`；不存在时回退到根 `index.html`（用于前端路由）；包含路径越界防护（`resolve()` + 前缀校验）

### `WebSocket /ws/{roomid}`

连接后流程：

1. 服务端 `accept()` 并校验房间是否存在
2. 校验 `token`、`user_id`、`username`（通过 cookie）
3. 加入对应房间的 `ClientManager`
4. 循环读取 `receive()` 消息
5. 消息按首字节解析 `EventType | GameEventType`，二进制和 JSON 都统一分发到 `handlers.handle()`
6. 断连后从对应房间移除客户端

> 当前已实现 JSON 业务解析，支持多种游戏事件（播放控制、抢答、评分等）。

## 事件系统

### 事件枚举（`utils/enumerations.py`）

#### 底层事件（二进制帧）

- `OMIT = 0`
- `AUDIO_FRAME = 1`
- `META_DATA = 2`
- `HEARTBEAT = 3`
- `TIME_SYNC = 4`
- `MESSAGE = 255`（错误处理保留值）

#### 游戏事件（JSON 消息）

- `ROOM_CREATE = 10`, `ROOM_JOIN = 11`, `ROOM_STATE = 12`, `GAME_OVER = 13`, `START_POS_UPDATE = 14`
- `PLAY = 20`, `PAUSE = 21`, `SEEK = 22`, `PRELOAD_AUDIO = 23`
- `PLAYER_READY = 30`, `GAME_START = 31`, `COUNTDOWN = 32`, `ATTEMPT_ANSWER = 33`, `YOUR_TURN = 34`, `SUBMIT_ANSWER = 35`, `ANSWER_BROADCAST = 36`, `ANSWER_QUEUE = 37`, `ROUND_END = 38`
- `JUDGING = 40`, `JUDGE_SUBMIT = 41`, `SCORE_UPDATE = 42`

> 注：各事件的详细说明、方向和服务端行为参见[设计规范中的事件表](#整体设计)。

### 已注册处理器

#### 二进制事件处理器

- `HEARTBEAT`（`handlers/heartbeats.py`）：处理心跳帧（PING/PONG）

#### 游戏事件处理器（按事件ID范围分布）

事件处理器按事件ID范围组织在多个文件中：

- **`audio_events_2x.py`**（事件20-29）：
  - `PLAY` (20)：开始播放，仅房主可操作
  - `PAUSE` (21)：暂停播放，由抢答或房主触发
  - `SEEK` (22)：调整播放进度
  - `PRELOAD_AUDIO` (23)：音频预加载事件（消息格式已定义，**处理器待实现**）

- **`round_events_3x.py`**（事件30-39）：
  - `GAME_START` (31)：房主开始游戏，禁止新玩家加入
  - `ATTEMPT_ANSWER` (33)：处理玩家抢答尝试，触发暂停和入队
  - `ROUND_END` (38)：回合结束，准备下一轮
  - 以下事件**处理器待实现**：`SUBMIT_ANSWER` (35)、`YOUR_TURN` (34)、`ANSWER_BROADCAST` (36)、`ANSWER_QUEUE` (37)、`CLEAR_ANSWER_QUEUE` (38)

- **`game_events.py`**：
  - 包含连接生命周期管理函数：`on_connect`、`on_disconnect`
  - 判分相关事件处理器：`JUDGING` (40)、`JUDGE_SUBMIT` (41)、`SCORE_UPDATE` (42)

- **`heartbeats.py`**：
  - `HEARTBEAT`：处理心跳帧（PING/PONG）

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

- `tests/test_audio_frame_encoding.py`：验证 `AudioFrame.dump()/load()` 一致性
- `tests/test_db_crud.py`：数据库 CRUD 操作测试
- `tests/test_db_session.py`：数据库会话管理测试
- `tests/test_qapi.py`：QQ 音乐 API 客户端测试
- `tests/test_tags_api.py`：标签 API 端点测试
- `tests/ws_conn.py`：WebSocket 集成测试工厂，封装“创建房间 →（可选）加入房间 → 组装鉴权 Cookie → 建立 WS 连接”，返回可复用连接句柄
- `tests/test_playback_message_ws.py`：真实 WebSocket 播放信令集成测试（`PLAY` / `PAUSE` / `SEEK`），支持模块级共享 room/连接复用，并通过 `rich.print` 输出收发消息和 Redis 状态便于人工核对

#### WebSocket 集成测试复用约定

- 推荐通过 `tests/ws_conn.py` 中的工厂函数创建连接，避免在每个测试里重复写建房、入房、鉴权 Cookie 逻辑。
- `tests/test_playback_message_ws.py` 采用模块级共享连接（单文件复用同一个 room 与 ws），同时提供“pipe 风格”串行用例（同一连接内按顺序发送多条消息）。
- 当前实现下 `SEEK` 事件会广播完整消息，但 Redis 播放状态仅更新 `progress_ms` / `offset_ts`，不会覆盖已存在的 `audio_url`（测试断言已按此行为对齐）。

## 当前边界与说明

### 已实现功能

- **房间/歌单业务流程**：支持创建房间、加入房间、管理歌单、添加歌曲到房间等核心功能。
- **WebSocket 事件处理器**：
  - 音频控制：`PLAY` (20)、`PAUSE` (21)、`SEEK` (22)
  - 游戏流程：`GAME_START` (31)、`ATTEMPT_ANSWER` (33)、`SUBMIT_ANSWER` (35)、`ANSWER_BROADCAST` (36)、`ANSWER_QUEUE` (37)、`ROUND_END` (38)
  - 判分环节：`JUDGING` (40)、`JUDGE_SUBMIT` (41)、`SCORE_UPDATE` (42)
  - 玩家管理：`KICK_USER` (15)、`PLAYER_LEAVE` (16)、`ROOM_JOIN` (17)、`PLAYER_READY` (18)
  - 其他：`COUNTDOWN` (32)、`YOUR_TURN` (34)
- **音频缓存与下载**：支持音频预下载和本地缓存，通过 `/api/songs/cache/{song_id}` 提供音频文件服务（支持 Range 请求）。
- **标签组评分逻辑**：已实现完整的判分逻辑，支持标签组匹配计分和精准描述计分。
- **断线重连**：通过 Cookie token 实现断线重连和会话恢复。

### 待实现功能

- `EventType` 中的 `AUDIO_FRAME`、`META_DATA`、`TIME_SYNC`、`MESSAGE` 目前无对应 handler（保留供未来扩展）。
- `PRELOAD_AUDIO` (23)：音频预加载通知
- `CLEAR_ANSWER_QUEUE` (38)：清空抢答队列
- `START_POS_UPDATE` (14)：房主控制条（起始位置百分比 0-80%）
- `GAME_OVER` (13)：游戏结束事件
- 音频缓存与下载功能需要有效的 QQ 音乐 Cookie 才能获取高质量音频 URL。

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
