# 猜猜歌系统项目蓝图

## 1. 项目概述

猜猜歌系统是一个基于 Web 的多人实时竞猜游戏。玩家可以创建房间（成为房主）或加入已有房间，在播放歌曲片段的过程中抢答，从预设的标签列表中勾选正确答案，并输入“精准描述”字段，房主最终确认评分。系统强调实时性、公平性和趣味性，支持断线重连、历史标注复用，以及灵活的标签组（组内互斥、组间不互斥）计分规则。

本项目基于现有 FastAPI 后端框架（`ccg_backend`）进行扩展，该框架已提供 WebSocket 二进制事件、心跳、客户端管理、内存监控等基础设施。本蓝图补充完整的游戏业务逻辑、数据持久化（使用 PostgreSQL）、外部服务集成（如 QQ 音乐 API）及前端交互设计，同时保证良好的可扩展性和易部署性。

## 2. 核心功能需求

- **房间系统**：房主创建房间，生成唯一房间 ID（6位字母数字）；玩家通过房间 ID 和用户名加入；房主可踢人、开始游戏、结束回合。
- **用户与会话**：无全局用户系统。玩家在创建或加入房间时输入用户名，同一房间内用户名唯一（数据库唯一约束保证）。通过 Cookie 中的令牌（token）实现断线重连，确保一个浏览器/设备同时只能处于一个房间（同一用户在同一房间只能有一个WebSocket连接）。
- **歌曲管理**：房主通过 QQ 音乐歌单 ID 导入曲目；后端爬取歌曲元数据；歌曲按房间独立管理，支持设置播放队列顺序；支持音频预下载与缓存。
- **标签系统**：房主可预设多个标签组，每组内标签互斥（例如"年代"组：80年代、90年代、00年代），组间不互斥；每个标签可计分。另设"精准描述"字段，玩家需手动输入短文本，房主在判分时从多个候选描述中选择正确项（可多选或不选）。
- **游戏流程**：开始游戏 → 回合开始（倒计时） → 播放 → 抢答（可排队） → 作答（依次） → 判分 → 下一轮（或结束）。
- **抢答机制**：玩家可随时点击抢答，后端维护一个抢答队列（Redis List）。当有玩家抢答时，立即暂停播放，该玩家进入作答轮次；在此期间其他玩家仍可继续抢答，但会被加入队列，待当前玩家作答完毕后再依次处理。
- **作答机制**：轮到作答的玩家从标签组中勾选标签（每组至多选一个），并填写"精准描述"。提交后该玩家状态保持，后端广播作答内容（不含正确答案）。若超时未提交，视为弃权。
- **判分机制**：房主主动进入判分环节。房主在标准答案区勾选正确标签（考虑组互斥），并从"精准描述"候选列表（系统根据历史推荐或房主手动输入）中选择正确的一个或多个，或选择"无正确描述"。提交后系统自动计分：每个命中标签计1分，但若多名玩家同时命中同一标签，仅最先抢答的玩家得分；精准描述也计1分。支持"不计分"跳过当前回合。
- **历史标注复用**：相同曲目的历史标注会被记录并推荐给房主，提高判分效率。
- **数据持久化**：用户、房间、歌曲、标签组、标注历史、得分记录需持久化存储（PostgreSQL/SQLite）；房间实时状态（播放状态、抢答队列）存于缓存（Redis）。

## 3. 系统架构

```plain
┌───────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   前端 (Vue/React)│────▶│   FastAPI 后端  │────▶│   PostgreSQL    │
│   WebSocket       │◀────│   WebSocket     │     │   (持久化数据)  │
└───────────────────┘     └─────────────────┘     └─────────────────┘
                               │       │
                               │       └─────────────────┐
                               │                         │
                               ▼                         ▼
                       ┌─────────────────┐     ┌─────────────────┐
                       │     Redis       │     │   QQ音乐 API    │
                       │ (实时状态/缓存) │     │  (爬虫/官方API) │
                       └─────────────────┘     └─────────────────┘
                               │
                               ▼
                       ┌─────────────────┐
                       │  Huey Task Queue│
                       │   (任务队列)    │
                       └─────────────────┘
```

- **前端**：单页应用（SPA），负责 UI 交互、音频播放、WebSocket 通信。
- **后端**：FastAPI 应用，提供 HTTP API 和 WebSocket 服务，处理业务逻辑。
- **数据库**：PostgreSQL（生产环境）或 SQLite（开发环境）作为主数据库，通过 SQLAlchemy async ORM 操作。配置 `CCG_DATABASE_URL` 指定。
- **缓存**：Redis 存储房间实时状态（播放状态、抢答队列等），支持高并发和快速过期。
- **任务队列**：Huey + Redis 用于处理音频预下载等异步任务。
- **外部服务**：QQ 音乐 API（或爬虫）获取歌单歌曲元数据及播放链接；音频文件可缓存至后端本地静态目录。

## 4. 技术栈选型

| 层级           | 技术                        | 说明                                                  |
| ------------ | ------------------------- | --------------------------------------------------- |
| 前端           | Vue 3 + TypeScript + Vite | 响应式 UI，组合式 API；WebSocket 客户端使用原生 API                |
| 后端           | Python 3.12 + FastAPI     | 高性能异步框架，原生支持 WebSocket                              |
| WebSocket 协议 | JSON 帧（游戏事件）              | 游戏事件（11-70范围）使用 JSON 格式；二进制帧用于实时音频元数据传输             |
| 数据库          | PostgreSQL / SQLite       | 通过 `asyncpg`（PostgreSQL）或 `aiosqlite`（SQLite）实现异步操作 |
| 缓存           | Redis 7                   | 存储房间实时状态（播放状态、抢答队列等），支持高并发和快速过期                     |
| 任务队列         | Huey + Redis              | 用于异步爬取歌单、预下载音频（避免阻塞主线程）                             |
| 音频缓存         | 后端本地文件，通过 API 路由提供        | 预下载的音频文件通过 `/api/songs/cache/{song_id}` 路由提供        |

## 5. 详细模块设计

### 5.1 房间管理模块

- **创建房间**：房主提供用户名和房间标题，后端生成唯一房间 ID（6位字母数字），创建 `User` 记录（标记为房主 `is_owner=True`）和 `Room` 记录。生成一个全局唯一的令牌（token，UUID）用于后续认证，通过 Set-Cookie `HttpOnly` 传递给前端。房间信息存入数据库（PostgreSQL/SQLite），状态为 `WAITING`。
- **加入房间**：用户提供房间 ID 和用户名，后端检查房间是否存在且状态为 `WAITING`；若该房间内用户名已存在（唯一约束），返回错误。创建该房间内的新 `User` 记录，生成新令牌，关联房间和玩家。
- **房间状态**：数据库 `Room` 表存储房间基础状态（status, current\_song\_index, round\_state, song\_start\_range\_percent 等）；Redis 存储播放状态（PlaybackState）和抢答队列（AnswerQueue）。
- **断线重连**：用户通过 Cookie 中的 Token 重新连接 WebSocket，后端校验令牌有效性（数据库查询），自动将 WebSocket 连接绑定到原用户，恢复其在房间内的状态，并推送当前房间状态同步。同一用户在同一房间只能有一个 WebSocket 连接（`duplicate connection is not allowed`）。

### 5.2 用户与会话模块

- **用户标识**：每个玩家在所属房间内由唯一数字 ID（`id`，自增，存储在 `users` 表）标识，前端展示的用户名由玩家输入，同一房间内唯一（数据库唯一约束 `uq_users_room_id_username`）。
- **认证方式**：创建/加入房间时生成随机令牌（UUID），通过 Set-Cookie `HttpOnly; Path=/` 传递给前端；WebSocket 连接时通过 `simple_authentication` 验证 Cookie 中的 token。
- **单房间限制**：WebSocket 连接时检查该用户是否已在该房间有活跃连接（`has_user_connection`），若有则拒绝连接（code 1008）。
- **重连机制**：若 WebSocket 断开，前端自动重连，携带相同 Cookie；后端验证通过后恢复连接。

### 5.3 歌曲管理模块

- **歌单导入**：房主提供 QQ 音乐歌单 ID，后端异步爬取歌曲列表（名称、歌手、封面、播放链接等）。使用 Huey 任务队列异步执行爬取，避免阻塞主线程。爬取结果存入 `songs` 表、`songlists` 表及相关关联表。
- **歌曲管理**：歌曲元数据独立管理（`songs` 表），通过 `room_songs` 关联表与房间关联。每个房间可有独立的歌曲队列，通过 `song_order` 字段控制顺序。
- **音频预下载**：游戏开始前，后端根据播放列表预下载音频到本地缓存目录（`assets/audio/`），并通过 `/api/songs/cache/{song_id}` 路由提供访问。使用临时 token 验证访问权限。
- **缓存策略**：已下载的音频文件通过 `cached_path` 字段记录路径，永久保留。通过 `platform_song_id` 去重，避免重复下载。

### 5.4 标签系统模块

- **标签组定义**：房主在创建房间或游戏准备阶段可配置多个标签组。每个标签组有名称（如"年代"）和描述，组内包含若干互斥标签（如"80年代"、"90年代"）。组之间独立，不互斥。
- **标签存储**：标签信息持久化在 `tag_groups` 和 `tags` 表中，通过 `tag_group_tags` 关联表实现多对多关系（一个标签可属于多个标签组）。标签组与房间通过 `tag_groups_rooms` 关联表实现多对多关系。
- **精准描述**：额外字段，用于玩家输入自由文本。判分时，房主可从历史出现的描述或手动输入中选择正确项。历史描述存储在 `song_description_history` 表中。

### 5.5 游戏流程模块（状态机）

游戏房间内维护两个有限状态机：

**房间状态机** **`RoomStateMachine`**：

- `WAITING` → `RUNNING` → `ENDED`
- `ENDED` → `WAITING`（游戏结束后可重新开始）

**回合状态机** **`RoundStateMachine`**：

- `PENDING` → `PLAYING_AUDIO` → `ANSWERING`/`JUDGING` → `COMPLETED` → `PENDING`

**游戏流程**：

1. **等待开始**：房主可调整歌单、标签组、起始位置设置；玩家加入房间。
2. **游戏开始**：房主发送 `GAME_START` 事件，后端验证歌曲已下载，完成状态转换 `WAITING` → `RUNNING`，广播 `GAME_START`。
3. **回合开始**：后端广播 `ROUND_START` 事件（含音频 URL、轮次索引、起始位置百分比）。前端开始播放。
4. **起始位置控制**：
   - 房主可调整起始位置百分比 `song_start_range_percent`（0-100），存储在数据库 `Room` 表。
   - 播放时从歌曲前 `x%` 的区间内均匀随机选取起始点。
5. **抢答排队**：
   - 玩家发送 `ATTEMPT_ANSWER` 事件，后端将玩家加入 Redis 抢答队列（`AnswerQueueItem`）。
   - 若播放中，立即暂停（广播 `PAUSE` 事件），设置当前作答玩家，广播 `YOUR_TURN`。
   - 若已暂停，新抢答者仅入队。
6. **作答轮次**：
   - 当前作答玩家发送 `SUBMIT_ANSWER` 事件（包含选中的标签 ID 列表和精准描述）。
   - 后端保存答案到 `PlayerAnswer` 表，广播 `ANSWER_BROADCAST`。
   - 若队列非空，更新下一个作答玩家；若队列为空，进入待判分状态。
7. **判分环节**：
   - 房主发送 `JUDGING` 事件（手动触发），后端广播 `JUDGING` 事件（含歌曲信息、历史标签 ID、玩家提交的描述候选列表）。
   - 房主提交正确答案（`JUDGE_SUBMIT`），包含正确标签 ID 列表、正确描述 ID 列表、新描述文本，或选择跳过计分。
8. **计分**：
   - 遍历每个抢答玩家的答案：
     - 对于每个标签，如果该标签在房主答案中，则该玩家得1分，但仅当该玩家是第一个提交包含该标签的答案的玩家（按抢答顺序）。即同一标签最多让一个玩家得分。
     - 对于精准描述，如果该描述在房主答案中，则该玩家得1分。
   - 更新积分榜 `Score` 表，广播 `SCORE_UPDATE` 事件。
   - 将房主标注的正确答案存入 `song_tag_history` 和 `song_description_history` 表。
9. **下一轮**：若还有歌曲，更新 `current_song_index`，进入下一个 `ROUND_START`；否则发送 `GAME_OVER` 事件。

### 5.6 计分与历史标注模块

- **计分规则**：每个命中标签计1分，但同一标签仅最先抢答者得分。若多名玩家答案中包含同一正确标签，只有抢答顺序最先的那位得分。精准描述命中计1分。
- **历史标注**：
  - `song_tag_history` 表记录每次判分时房主选中的标签，供后续相同歌曲推荐时参考。
  - `song_description_history` 表记录房主选中的精准描述文本，判分时随机选择1-3个参考描述展示给房主。

### 5.7 WebSocket 协议扩展

游戏事件使用 JSON 格式传输（除音频元数据使用二进制帧）。事件值分配：

- `1x` 房间事件
- `2x` 音频/播放事件
- `3x` 游戏流程事件
- `4x` 判分事件
- `5x` 客户端同步事件

**房间事件（1x）**：

| 事件名                | 类型值 | 方向      | 说明                            | 服务器行为             |
| ------------------ | --- | ------- | ----------------------------- | ----------------- |
| `ROOM_JOIN`        | 11  | C→S     | 加入房间（WebSocket 连接时自动处理）       | -                 |
| `ROOM_STATE`       | 12  | S→C     | 推送完整房间状态（玩家列表、准备状态、歌曲列表、标签组等） | broadcast         |
| `GAME_OVER`        | 13  | S→C     | 游戏结束，展示最终排名                   | state & broadcast |
| `START_POS_UPDATE` | 14  | C→S/S→C | 房主更新起始位置百分比 (0-100)           | state & broadcast |
| `KICK_USER`        | 15  | C→S     | 房主踢人                          | state & broadcast |
| `PLAYER_LEAVE`     | 16  | C→S     | 玩家离开房间                        | state & broadcast |

**音频/播放事件（2x）**：

| 事件名             | 类型值 | 方向      | 说明                    | 服务器行为             |
| --------------- | --- | ------- | --------------------- | ----------------- |
| `PLAY`          | 20  | C→S     | 前端播放开始，携带当前播放进度       | state & broadcast |
| `PAUSE`         | 21  | C→S/S→C | 暂停播放，携带播放进度（毫秒）       | state & broadcast |
| `SEEK`          | 22  | C→S     | 调整播放进度，携带目标进度         | broadcast         |
| `PRELOAD_AUDIO` | 23  | S→C     | 音频预加载事件，通知客户端预加载下一首歌曲 | state & broadcast |

**游戏流程事件（3x）**：

| 事件名                | 类型值 | 方向      | 说明                        | 服务器行为             |
| ------------------ | --- | ------- | ------------------------- | ----------------- |
| `GAME_START`       | 31  | C→S/S→C | 房主开始游戏，验证歌曲已下载，广播游戏开始     | state & broadcast |
| `ROUND_START`      | 32  | S→C     | 回合开始，携带音频URL、轮次索引、起始位置百分比 | state & broadcast |
| `ATTEMPT_ANSWER`   | 33  | C→S     | 玩家抢答，加入队列，触发暂停            | state & broadcast |
| `YOUR_TURN`        | 34  | S→C     | 通知当前作答玩家                  | broadcast         |
| `SUBMIT_ANSWER`    | 35  | C→S     | 玩家提交答案（标签ID列表 + 精准描述）     | state & broadcast |
| `ANSWER_BROADCAST` | 36  | S→C     | 广播某玩家提交的答案                | broadcast         |
| `ANSWER_QUEUE`     | 37  | S→C     | 广播当前抢答队列状态                | broadcast         |
| `ROUND_END`        | 38  | C→S/S→C | 回合结束（房主手动或自动触发）           | state & broadcast |

**判分事件（4x）**：

| 事件名                  | 类型值 | 方向      | 说明                                                         | 服务器行为             |
| -------------------- | --- | ------- | ---------------------------------------------------------- | ----------------- |
| `JUDGING`            | 40  | C→S/S→C | 进入判分环节，携带歌曲信息和描述候选                                         | state & broadcast |
| `JUDGE_SUBMIT`       | 41  | C→S     | 房主提交正确答案（含正确标签、描述、跳过等）                                     | state & broadcast |
| `SCORE_UPDATE`       | 42  | S→C     | 更新积分榜                                                      | broadcast         |
| `SKIP_ROUND`         | 43  | C→S     | 房主跳过当前回合（不计分）                                              | state & broadcast |
| `SHOW_ANSWER`        | 44  | S→C     | 显示正确答案                                                     | broadcast         |
| `ROUND_STATE_UPDATE` | 45  | S→C     | 回合状态更新（PENDING/PLAYING\_AUDIO/ANSWERING/JUDGING/COMPLETED） | broadcast         |

**客户端同步事件（5x）**：

| 事件名                         | 类型值 | 方向  | 说明        | 服务器行为     |
| --------------------------- | --- | --- | --------- | --------- |
| `PLAYER_ANSWER`             | 50  | S→C | 全量玩家答案同步  | broadcast |
| `PLAYER_SELECTION_UPDATE`   | 51  | C→S | 玩家选择的增量更新 | state     |
| `PLAYER_DESCRIPTION_UPDATE` | 52  | C→S | 玩家描述的增量更新 | state     |
| `CLEAR_ANSWER_QUEUE`        | 53  | S→C | 清空抢答队列    | broadcast |

约定通用格式：

```json
{
  "event": u8,
  "ts": u64,
  "data": object
}
```

### 5.8 HTTP API 设计

> 约定：所有 API 接口以 `/api` 为前缀，`/ws/` 前缀用于 WebSocket 连接

| 端点                          | 方法    | 说明         | 请求体/参数                                             | 返回                         |
| --------------------------- | ----- | ---------- | -------------------------------------------------- | -------------------------- |
| `/api/room/`                | POST  | 创建房间       | `{ title: string, host_name: string }`             | `{ room_id, host: {...} }` |
| `/api/room/{roomId}`        | POST  | 加入房间       | `{ username: string }`                             | `{ room_id, user: {...} }` |
| `/api/room/{roomId}`        | GET   | 获取房间公开信息   | -                                                  | 房间基本信息                     |
| `/api/room/{roomId}`        | PATCH | 更新房间设置     | `{ title?, tag_group_ids?, tag_groups? }`          | 房间信息                       |
| `/api/songs/`               | GET   | 获取歌曲列表（分页） | `offset, limit, kw`                                | 歌曲列表                       |
| `/api/songs/`               | POST  | 创建歌曲       | 歌曲信息                                               | 歌曲信息                       |
| `/api/songs/{songId}`       | GET   | 获取歌曲详情     | -                                                  | 歌曲信息                       |
| `/api/songs/cache/{songId}` | GET   | 获取缓存的音频文件  | -                                                  | 音频流                        |
| `/api/songs/cache/{songId}` | POST  | 触发音频预下载    | -                                                  | 任务开始                       |
| `/api/tags/`                | GET   | 获取标签列表（分页） | `limit, offset`                                    | 标签列表                       |
| `/api/tags/`                | POST  | 创建标签       | `{ tags: [...] }`                                  | 标签列表                       |
| `/api/tags/groups/`         | GET   | 获取标签组列表    | `limit, offset`                                    | 标签组列表                      |
| `/api/tags/groups/`         | POST  | 创建标签组      | `{ name, description?, tags?, existing_tag_ids? }` | 标签组信息                      |
| `/api/tags/groups/`         | PATCH | 更新标签组      | 标签组更新                                              | 标签组信息                      |

## 6. 数据模型设计

实现上，使用 SQLAlchemy 2.0 style ORM 作为数据库交互接口；数据校验采用"边界优先"策略：

- HTTP/WS 输入边界使用 Pydantic 做反序列化与约束；
- ORM 模型仅负责持久化，不承担输入校验职责。

数据库访问约定（当前实现）：

- `db/session.py` 提供 `session_scope` 异步上下文管理器用于事务管理；
- `db/crud.py` 中的写入函数只负责 `add/update + flush`，不在函数内部 `commit`；
- 事务提交与回滚由上层业务边界统一管理。

### 6.1 数据库表结构

> 详细表结构请参考 `db/models.py`，以下是核心表概览：

**用户表** **`users`**

- 用户属于房间（`room_id` 外键）
- `token`: UUID 令牌用于 WebSocket 认证
- `is_owner`: 是否为房主
- `online`: 当前是否在线

**房间表** **`rooms`**

- `id`: 房间 ID（6位字母数字）
- `status`: 状态（WAITING=0, RUNNING=1, ENDED=2）
- `current_song_index`: 当前播放歌曲索引
- `round_state`: 回合状态（PENDING=0, PLAYING\_AUDIO=1, ANSWERING=2, JUDGING=3, COMPLETED=4）
- `song_start_range_percent`: 起始位置百分比（0-100）

**歌曲表** **`songs`**、**歌单表** **`songlists`**

- 支持多平台（QQ音乐等）
- `cached_path`: 本地缓存路径

**标签组表** **`tag_groups`**、**标签表** **`tags`**

- 通过 `tag_group_tags` 关联表实现多对多关系
- 标签可跨多个标签组使用

**历史标注表** **`song_tag_history`**、**`song_description_history`**

- 记录房主判分时选择的标签和描述

**积分表** **`scores`**

- 记录每轮得分变化和累计得分

**玩家答案表** **`player_answers`**

- 记录玩家提交的标签和精准描述

**任务表** **`tasks`**

- 存储 Huey 异步任务状态

详细 ORM 模型定义请参考 `db/models.py`。

### 6.2 Redis 数据结构

- **播放状态** `room:{roomId}:playback` (Hash)
  - `play_state`: "playing" | "paused"
  - `progress_ms`: 当前播放进度（毫秒）
  - `offset_ts`: 播放偏移时间戳
  - `audio_url`: 音频访问 URL（含临时 token）
- **抢答队列** `room:{roomId}:answer_queue` (List)
  - 存储 `AnswerQueueItem` JSON：`{player_id, offset_ts, server_ts, order, is_answering}`
- **当前作答玩家** `room:{roomId}:current_answerer` (String)
  - 当前正在作答的玩家 ID

其他状态（房间基础状态、玩家状态、歌曲队列）存储在 PostgreSQL 数据库中，通过 SQLAlchemy ORM 管理。

***

## 7. 接口定义（详细）

### 7.1 WebSocket 事件载荷示例

- **`PLAY`** (S→C)
  ```json
  {
    "event": 20,
    "ts": 1620000000000,
    "data": {
      "progress_ms": 0,
      "offset_ts": 1620000000000,
      "audio_url": "/static/audio/abc.mp3"
    }
  }
  ```
  注：PLAY 事件只包含播放控制数据，不包含曲目名称、封面等信息，以防止提前泄露答案。
- **`START_POS_UPDATE`** (C→S / S→C)
  ```json
  {
    "start_position_percent": 50
  }
  ```
  - 房主调整时发送 (C→S)，后端校验范围 0-80 后广播给所有客户端 (S→C)
- **`ATTEMPT_ANSWER`** (C→S)
  ```json
  {
    "progress_ms": 12345
  }
  ```
- **`YOUR_TURN`** (S→C, 私有)
  ```json
  {
    "time_limit_sec": 30,
    "tag_groups": [
      { "group_id": 1, "name": "年代", "tags": [{"id":101,"name":"80年代"},{"id":102,"name":"90年代"}] },
      { "group_id": 2, "name": "曲风", "tags": [{"id":201,"name":"摇滚"},{"id":202,"name":"流行"}] }
    ]
  }
  ```
- **`SUBMIT_ANSWER`** (C→S)
  ```json
  {
    "selected_tags": [101, 201],  // 标签ID列表（每组至多一个）
    "description": "这是一首经典摇滚"
  }
  ```
- **`ANSWER_BROADCAST`** (S→C)
  ```json
  {
    "player_id": 42,
    "username": "bob",
    "selected_tags": [101, 201],
    "description": "这是一首经典摇滚"
  }
  ```
- **`ANSWER_QUEUE`** (S→C)
  ```json
  {
    "queue": [42, 37, 15]  // 玩家ID列表，按抢答顺序排列
  }
  ```
- **`JUDGING`** (S→C)
  ```json
  {
    "tag_groups": [
      { "group_id": 1, "name": "年代", "tags": [{"id":101,"name":"80年代"},{"id":102,"name":"90年代"}] },
      { "group_id": 2, "name": "曲风", "tags": [{"id":201,"name":"摇滚"},{"id":202,"name":"流行"}] }
    ],
    "description_candidates": [
      { "id": 1001, "text": "这是一首经典摇滚", "count": 3 },  // count表示历史上被房主选为正确的次数，供排序参考
      { "id": 1002, "text": "旋律优美", "count": 1 }
    ],
    "answers": [  // 本轮所有玩家提交的答案（用于房主参考）
      { "player_id": 42, "username": "bob", "selected_tags": [101,201], "description": "这是一首经典摇滚" },
      { "player_id": 37, "username": "alice", "selected_tags": [102], "description": "节奏感强" }
    ]
  }
  ```
- **`JUDGE_SUBMIT`** (C→S, 仅房主)
  ```json
  {
    "event": 41,
    "ts": 1620000000000,
    "data": {
      "correct_tags": [101, 201],
      "correct_description_ids": [1001],  // 空数组表示无正确描述
      "new_correct_descriptions": ["歌手早年经典", "电影主题曲"], // 手动输入的新描述，会被添加到历史中，同时生成description_id供未来选择
      "skip_scoring": false
    }
  }
  ```
- **`SCORE_UPDATE`** (S→C)
  ```json
  {
    "event": 42,
    "ts": 1620000000000,
    "data": {
      "scores": [
        { "player_id": 42, "username": "bob", "score": 15 },
        { "player_id": 37, "username": "alice", "score": 10 }
      ]
    }
  }
  ```
- **`ROUND_END`** (S→C)
  ```json
  {
    "next_round_index": 2
  }
  ```
- **`GAME_OVER`** (S→C)
  ```json
  {
    "final_scores": [
      { "player_id": 42, "username": "bob", "score": 30 },
      { "player_id": 37, "username": "alice", "score": 25 }
    ]
  }
  ```
- **`JUDGING`** (S→C)
  ```json
  {
    "event": 40,
    "ts": 1620000000000,
    "data": {
      "song": {
        "title": "歌曲名",
        "artist": "歌手",
        "album": "专辑名",
        "cover_url": "https://..."
      },
      "tag_groups": [
        { "group_id": 1, "name": "年代", "tags": [{"id":101,"name":"80年代"}] }
      ],
      "description_candidates": [
        { "id": 1001, "text": "这是一首经典摇滚", "count": 3 }
      ],
      "answers": [
        { "player_id": "42", "username": "bob", "selected_tags": [101,201], "description": "这是一首经典摇滚" }
      ]
    }
  }
  ```
  注：JUDGING 事件包含完整的曲目信息，在判分环节显示给所有玩家。

### 7.2 HTTP API 示例

#### **创建房间**

```json
POST /api/room/
Content-Type: application/json

{
  "title": "我的房间",
  "host_name": "alice"
}

响应 200:
{
  "room_id": "ABC123",
  "host": {
    "id": 1,
    "username": "alice",
    "is_owner": true,
    "token": "uuid-token-string"
  }
}
Set-Cookie: token=uuid-token-string; HttpOnly; Path=/
```

#### **加入房间**

```json
POST /api/room/ABC123
Content-Type: application/json

{"username": "bob"}

响应 200:
{
  "room_id": "ABC123",
  "user": {
    "id": 2,
    "username": "bob",
    "is_owner": false,
    "token": "uuid-token-string"
  }
}
Set-Cookie: token=uuid-token-string; HttpOnly; Path=/
```

#### **获取房间公开信息**

```json
GET /api/room/ABC123

响应 200:
{
  "room_id": "ABC123",
  "host_player_id": "1",
  "status": 0,
  "title": "我的房间",
  "players": [
    {"id": 1, "username": "alice", "is_owner": true, "online": true},
    {"id": 2, "username": "bob", "is_owner": false, "online": true}
  ],
  "tag_groups": [...]
}
```

#### **更新房间设置**

```json
PATCH /api/room/ABC123
Content-Type: application/json

{
  "title": "新标题",
  "tag_group_ids": [1, 2, 3]
}

响应 200: 房间信息（同上）
```

#### **获取歌曲列表**

```json
GET /api/songs/?offset=0&limit=20&kw=周杰伦

响应 200:
{
  "total": 100,
  "list": [
    {"id": 1, "title": "晴天", "artist": "周杰伦", "cover_url": "...", ...}
  ]
}
```

#### **触发音频预下载**

```json
POST /api/songs/cache/123

响应 200:
{"message": "Caching task started"}
```

#### **获取标签组列表**

```json
GET /api/tags/groups/

响应 200:
[
  {
    "id": 1,
    "name": "年代",
    "description": "歌曲发布年代",
    "tags": [
      {"id": 1, "name": "80年代"},
      {"id": 2, "name": "90年代"}
    ]
  }
]
```

#### **创建标签组**

```json
POST /api/tags/groups/
Content-Type: application/json

{
  "name": "曲风",
  "description": "音乐风格",
  "tags": ["摇滚", "流行", "民谣"],
  "existing_tag_ids": [1, 2]
}

响应 200: 标签组信息（同上格式）
```

## 8. 前端要点

- **音频预加载**：使用 Web Audio API 或 `<audio>` 标签，提前获取音频文件并缓冲。
- **进度同步**：收到 `PAUSE` 事件后，根据 `progress_ms` 跳转到指定位置，并暂停。
- **重连逻辑**：检测到 WebSocket 断开，启动指数退避重连，重连成功后请求同步状态。
- **UI 状态机**：根据房间状态展示不同界面（准备区、播放区、作答区、判分区）。作答区需显示剩余时间、标签组（每组单选）和精准描述输入框。
- **排队展示**：前端应展示当前抢答队列，让玩家了解自己排在第几位。
- **房主权限控制**：房主界面显示额外控件（开始游戏、结束回合、判分提交），判分界面展示所有玩家的答案，以及标签组勾选框和描述候选列表。
- **前端路由**：
  - `/` - 首页（创建/加入房间）
  - `/room/:roomid` - 房间游戏页面
  - `/room/:roomid/manage` - 房间管理页面（仅房主可访问）
- **UI 库**：使用 daisyUI 组件库，提供现代化的界面设计

## 9. 部署方案

**环境要求**：

- Python 3.12+
- Redis 7+
- PostgreSQL 14+（生产环境）或 SQLite（开发环境）

**启动步骤**：

1. 复制 `.env.template` → `.env`，配置环境变量
2. 可选：复制 `config.template.yaml` → `config.yaml`
3. 运行数据库迁移：`uv run alembic upgrade head`
4. 启动后端：`uv run uvicorn main:app --reload --port 8000`
5. 启动 Huey 任务队列（可选）：`uv run huey_consumer.py mq.tasks.huey`

**环境变量说明**：

| 变量                       | 说明                | 默认值                                |
| ------------------------ | ----------------- | ---------------------------------- |
| `CCG_DATABASE_URL`       | 数据库连接 URL         | `sqlite+aiosqlite:///data/game.db` |
| `CCG_REDIS_URL`          | Redis 连接 URL      | `redis://localhost:6379/0`         |
| `CCG_QQ_MUSIC_COOKIE`    | QQ 音乐 Cookie（爬虫用） | -                                  |
| `CCG_AUDIO_DOWNLOAD_DIR` | 音频缓存目录            | `assets/audio`                     |
| `CCG_LOG_LEVEL`          | 日志级别              | `INFO`                             |

**前端部署**：前端构建产物位于 `ccg_frontend/dist/`，由 FastAPI 自动托管。

## 10. 后续优化方向

### 10.1 短期优化（1-2 个月）

#### 10.1.1 标签组模板库
允许用户保存常用标签组配置，供以后快速选用。

**设计要点**：
- 新增 `tag_group_templates` 表，存储用户自定义模板
- 模板包含：模板名称、标签组列表、适用场景描述
- 房主创建房间时可快速加载模板
- 支持模板分享（公开/私有）

**数据库扩展**：
```python
class TagGroupTemplate(Base):
    __tablename__ = "tag_group_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]  # 模板名称
    creator_id: Mapped[int]  # 创建者用户 ID
    tag_groups: Mapped[list[TagGroup]]  # 关联的标签组
    is_public: Mapped[bool] = mapped_column(default=False)  # 是否公开
    usage_count: Mapped[int] = mapped_column(default=0)  # 使用次数
```

#### 10.1.2 快速加入链接
生成房间加入链接，方便玩家直接加入房间。

**设计要点**：
- 生成短链接：`https://example.com/join/ABC123`
- 支持二维码生成（前端实现）
- 链接带预填充用户名参数：`?username=bob`
- 房主可设置房间最大人数限制

**API 扩展**：
```python
# GET /api/room/{roomId}/join-link
# 返回：{ "join_url": "https://...", "qr_code_data_url": "data:image/png;base64,..." }
```

#### 10.1.3 音频来源扩展
支持网易云、Spotify 等其他平台。

**设计要点**：
- 抽象音乐平台接口：`MusicPlatform` 基类
- 实现不同平台适配器：`QQMusicPlatform`, `NeteaseMusicPlatform`, `SpotifyPlatform`
- 统一歌曲元数据格式
- 支持多平台歌曲混合播放

**代码结构**：
```
utils/
├─ music_platforms/
│  ├─ base.py           # MusicPlatform 基类
│  ├─ qq_music.py       # QQ 音乐实现
│  ├─ netease_music.py  # 网易云实现
│  └─ spotify.py        # Spotify 实现
```

### 10.2 中期优化（3-6 个月）

#### 10.2.1 观战系统增强
完善观战功能，支持更多互动。

**功能设计**：
- 观战人数限制（可配置）
- 观战者聊天室（文字聊天）
- 观战者竞猜（预测获胜者）
- 精彩回合回放（录制/分享）

**WebSocket 扩展**：
```python
# 观战者事件（6x）
WATCH_CHAT_MESSAGE = 60  # 聊天消息
WATCH_PREDICTION = 61    # 竞猜投注
WATCH_REPLAY_REQUEST = 62  # 请求回放
```

#### 10.2.2 智能推荐系统
基于历史数据推荐标签和描述。

**功能设计**：
- 基于歌曲特征的标签推荐（协同过滤）
- 描述文本智能推荐（NLP 相似度匹配）
- 房主判分时自动排序候选描述
- 新歌冷启动策略（基于歌手/专辑推荐）

**技术栈**：
- 使用 `scikit-learn` 进行相似度计算
- TF-IDF + Cosine Similarity 处理描述文本
- 离线训练 + 在线推理

#### 10.2.3 房间匹配系统
支持随机匹配陌生人游戏。

**功能设计**：
- 匹配队列（按技能等级/偏好）
- 自动创建房间（系统作为房主）
- 匹配优先级（等待时间、技能匹配度）
- 匹配失败补偿（机器人填充）

**Redis 数据结构**：
```
match_queue: {
  "casual": [user1, user2, ...],     # 休闲模式队列
  "ranked": [user3, user4, ...],     # 排位模式队列
  "beginner": [user5, user6, ...]    # 新手模式队列
}
```
