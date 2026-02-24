# 猜猜歌系统项目蓝图

## 1. 项目概述

猜猜歌系统是一个基于 Web 的多人实时竞猜游戏。玩家可以创建房间（成为房主）或加入已有房间，在播放歌曲片段的过程中抢答，从预设的标签列表中勾选正确答案，并输入“精准描述”字段，房主最终确认评分。系统强调实时性、公平性和趣味性，支持断线重连、历史标注复用，以及灵活的标签组（组内互斥、组间不互斥）计分规则。

本项目基于现有 FastAPI 后端框架（`ccg_backend`）进行扩展，该框架已提供 WebSocket 二进制事件、心跳、客户端管理、内存监控等基础设施。本蓝图补充完整的游戏业务逻辑、数据持久化（使用 SQLite）、外部服务集成及前端交互设计，同时保证良好的可扩展性和易部署性。

## 2. 核心功能需求

- **房间系统**：房主创建房间，生成唯一房间 ID；玩家通过房间 ID 和用户名加入；房主可踢人、开始游戏、结束回合。
- **用户与会话**：无全局用户系统。玩家在创建或加入房间时输入用户名，同一房间内用户名唯一（后端自动添加随机后缀解决重名）。通过 Cookie 中的令牌（token）实现断线重连，并确保一个浏览器/设备同时只能处于一个房间。
- **歌曲管理**：房主通过 QQ 音乐歌单 ID 导入曲目；后端爬取歌曲元数据并生成随机播放列表；支持音频预下载与缓存。
- **标签系统**：房主可预设多个标签组，每组内标签互斥（例如“年代”组：80年代、90年代、00年代），组间不互斥；每个标签可计分。另设“精准描述”字段，玩家需手动输入短文本，房主在判分时从多个候选描述中选择正确项（可多选或不选）。
- **游戏流程**：准备 → 倒计时 → 播放 → 抢答（可排队） → 作答（依次） → 判分 → 下一轮。
- **抢答机制**：玩家可随时点击抢答，后端维护一个抢答队列。当有玩家抢答时，立即暂停播放，该玩家进入作答轮次；在此期间其他玩家仍可继续抢答，但会被加入队列，待当前玩家作答完毕后再依次处理。
- **作答机制**：轮到作答的玩家从标签组中勾选标签（每组至多选一个），并填写“精准描述”。提交后该玩家状态变为“已作答”，后端广播作答内容（不含正确答案）。
- **判分机制**：当队列为空且所有房间内玩家均已作答，或房主主动结束回合，或曲目播放完毕时，进入判分环节。房主在标准答案区勾选正确标签（考虑组互斥），并从“精准描述”候选列表（系统根据历史推荐或房主手动输入）中选择正确的一个或多个，或选择“无正确描述”。提交后系统自动计分：每个命中标签计1分，但若多名玩家同时命中同一标签，仅最先抢答的玩家得分；精准描述也计1分。支持“不计分”跳过当前回合。
- **历史标注复用**：相同曲目的相同标签组的历史标注会被记录并推荐给房主，提高判分效率。
- **数据持久化**：用户、房间、歌曲、标签组、标注历史、得分记录需持久化存储；房间实时状态存于缓存（Redis）。

## 3. 系统架构

```plain
┌───────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   前端 (Vue/React)│────▶│   FastAPI 后端  │────▶│     SQLite      │
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
```

- **前端**：单页应用（SPA），负责 UI 交互、音频播放、WebSocket 通信。
- **后端**：FastAPI 应用，提供 HTTP API 和 WebSocket 服务，处理业务逻辑。
- **数据库**：SQLite / pgsql 作为主数据库，存储所有持久化数据。利用 WAL 模式支持并发读，通过连接池或文件锁机制管理写操作（房间规模不大时可接受）。
- **缓存**：Redis 存储房间实时状态（玩家列表、准备状态、当前轮次、抢答队列等），支持高并发和快速过期。
- **任务队列**：Redis + Celery（或它的类似物）用于处理音频预处理
- **外部服务**：QQ 音乐 API（或爬虫）获取歌单歌曲元数据及播放链接；音频文件可缓存至后端本地静态目录。

## 4. 技术栈选型

| 层级       | 技术                             | 说明                                                                 |
|------------|---------------------------------- |---------------------------------------------------------------------- |
| 前端       | Vue 3 + TypeScript + Vite        | 响应式 UI，组合式 API；WebSocket 客户端使用原生 API 或 Socket.io 兼容层 |
| 后端       | Python 3.12 + FastAPI            | 高性能异步框架，原生支持 WebSocket                                   |
| WebSocket 协议 | 二进制帧（现有框架扩展）     | 基于现有 EventType 增加游戏事件；保持低延迟                          |
| 数据库     | SQLite 3                         | 轻量级文件数据库，支持 JSON 扩展；使用 `aiosqlite` 实现异步操作      |
| 缓存       | Redis 7                          | 存储房间实时状态，支持发布订阅（可用于广播）                          |
| 任务队列   | 可选：Huey/flowrra + Redis              | 用于异步爬取歌单、预下载音频（避免阻塞主线程）                        |
| 音频缓存   | 后端本地文件，后端自行维护一个cache路由      | 预下载的音频文件作为静态资源提供，减少对外部依赖                      |

## 5. 详细模块设计

### 5.1 房间管理模块

- **创建房间**：房主提供用户名，后端生成唯一房间 ID（如 6 位字母数字），并在该房间内创建房主玩家记录（包含用户名，并标记为房主）。生成一个全局唯一的令牌（token）用于后续认证，将该令牌与房间 ID、玩家 ID 关联存入 Redis（可同时持久化到 SQLite 会话表），并设置过期时间。房间信息存入 Redis（过期时间 6 小时）和 SQLite（持久化记录）。
- **加入房间**：用户提供房间 ID 和用户名，后端检查房间是否存在且未开始；若该房间内用户名已存在，则自动添加随机后缀（如 `#1234`）或提示用户修改。创建该房间内的新玩家记录，生成新令牌，关联房间和玩家，存入 Redis。同时检查该用户是否已有有效令牌（通过 Cookie 携带），若有则强制清除旧会话（实现单房间限制）。
- **房间状态**：Redis Hash 存储房间信息，包括房主 ID、玩家列表（Set）、当前歌曲索引、各玩家准备状态、轮次状态、抢答队列（List）、当前作答玩家ID等。
- **断线重连**：用户通过 Cookie 中的 Token 重新连接 WebSocket，后端校验令牌有效性（Redis 中存在且未过期），自动将 WebSocket 连接绑定到原玩家，恢复其在房间内的状态，并推送当前房间状态同步，确保一个浏览器/设备同时只能处于一个房间。若房间已结束，则引导用户创建新房间或加入其他房间。

### 5.2 用户与会话模块

- **用户标识**：每个玩家在所属房间内由唯一数字 ID（`player_id`，自增，关联房间）标识，前端展示的用户名由玩家输入，同一房间内唯一。
- **认证方式**：创建/加入房间时生成随机令牌（如 UUID），通过 Set-Cookie `HttpOnly` 传递给前端；WebSocket 连接时携带该 Cookie 进行身份验证。令牌与 `(room_id, player_id)` 的映射存储在 Redis 中，并设置过期时间（如 24 小时）。
- **单房间限制**：当用户尝试创建或加入新房间时，后端检查其现有令牌是否有效；若存在有效令牌，则清除旧会话（从 Redis 中删除令牌映射，并通知旧连接断开，广播玩家离开，并清理相关状态），并删除旧令牌，确保同一浏览器/设备只能同时处于一个房间。
- **重连机制**：若 WebSocket 断开，前端自动尝试重连，携带相同 Cookie；后端重新绑定连接，并推送当前房间状态同步。

### 5.3 歌曲管理模块

- **歌单导入**：房主提供 QQ 音乐歌单 ID，后端异步爬取歌曲列表（名称、歌手、封面、播放链接等）。可使用 `aiohttp` + 解析网页或调用第三方非官方 API（需注意合规性）。爬取结果存入 SQLite 的 `songs` 表，并与房间关联。
- **播放列表生成**：将歌曲列表洗牌（shuffle），生成随机顺序，存储于 Redis 房间信息中的 `song_queue` 列表。
- **音频预下载**：游戏开始前，后端根据播放列表预下载下一首音频到本地缓存目录（如 `static/audio/`），并生成静态资源 URL。下载完成通过 WebSocket 通知前端预加载。
- **缓存策略**：已下载的音频文件保留一段时间（如 7 天，或设置永久保留），通过文件名 MD5 或歌曲 ID 去重，避免重复下载。

### 5.4 标签系统模块

- **标签组定义**：房主在创建房间或游戏准备阶段可配置多个标签组。每个标签组有名称（如“年代”），组内包含若干互斥标签（如“80年代”、“90年代”）。组之间独立，不互斥。
- **标签存储**：标签信息持久化在 SQLite 的 `tag_groups` 和 `tags` 表中。每个标签属于一个组，组内互斥由应用逻辑保证。
- **精准描述**：额外字段，用于玩家输入自由文本。判分时，房主可从历史出现的描述或手动输入中选择正确项。历史描述存储在 `song_descriptions` 表中，与歌曲和房间关联。

### 5.5 游戏流程模块（状态机）

游戏房间内维护一个有限状态机，状态流转如下：

1. **等待准备**：房主可调整歌单、标签组；玩家点击“准备”，状态存入 Redis 缓存。
2. **倒计时**：当所有玩家准备就绪（或房主强制开始），后端广播 3 秒倒计时事件，同时通知前端预加载音频。
3. **播放中**：倒计时结束，后端广播 `play` 事件（含音频元数据、当前轮次索引、标签组信息）。前端开始播放。
4. **抢答排队**：
   - 玩家点击抢答，前端发送 `attempt_answer` 事件，携带当前播放进度。
   - 后端将该玩家加入抢答队列（Redis List），若队列此前为空，则立即暂停播放（广播 `pause` 事件，含播放进度），并开始处理第一个玩家作答。
   - 若播放已暂停且有玩家正在作答，新抢答者仅入队，不重复暂停。
5. **作答轮次**：
   - 后端从队列中取出队首玩家，将其状态设为“作答中”，广播 `your_turn` 事件
   - 该玩家在限定时间内（如 30 秒）提交答案，包含各组选中的标签 ID 列表及精准描述文本。提交后，后端广播 `answer_submitted` 事件（公开答案内容，不含正确性）。
   - 若超时未提交，自动视为弃权，从队列移除，继续处理下一个玩家。
   - 若队列非空，继续处理下一个玩家；若队列为空，则继续播放音频。
6. **判分环节**：
   - 触发条件：队列为空且房间内所有玩家均已作答，或房主点击“结束回合”，或歌曲播放完毕且无人抢答。
   - 后端广播 `judging` 事件，房主界面显示标准答案区：展示所有标签组供勾选（考虑组内互斥），以及精准描述候选列表（根据历史标注或空列表），房主可手动添加新描述选项。
   - 房主提交正确答案（含选中的标签 ID 列表和选中的精准描述 ID 列表，或“无描述”）。后端进行计分。
7. **计分**：
   - 遍历每个抢答玩家的答案：
     - 对于每个标签，如果该标签在房主答案中，则该玩家得1分，但仅当该玩家是第一个提交包含该标签的答案的玩家（按抢答顺序）。即同一标签最多让一个玩家得分。
     - 对于精准描述，如果该描述在房主答案中，则该玩家得1分。
   - 更新积分榜，广播 `score_update` 事件。
   - 将房主标注的正确答案存入历史表 `song_tag_history` 和 `song_description_history`，用于未来推荐。
8. **下一轮**：重置玩家状态（未抢答），清空抢答队列，返回步骤 2 ，进入倒计时；房主可选择结束游戏，广播 `game_over`。

### 5.6 计分与历史标注模块

- **计分规则**：每个命中标签计1分，但同一标签仅最先抢答者得分。若多名玩家答案中包含同一正确标签，只有抢答顺序最先的那位得分。
- **历史标注**：
  - `song_tag_history` 表记录每次判分时房主选中的标签（可多条），供后续相同歌曲推荐时统计频次。
  - `song_description_history` 表记录房主选中的精准描述文本（或标记为“不正确”），推荐时按频次排序展示。
  - 进入判分环节时，后端查询该歌曲的历史标签和描述，按频次降序返回给房主作为候选。（方便起见，可直接简单传入每组标签的最新历史记录。）

### 5.7 WebSocket 协议扩展

在现有二进制帧基础上，增加游戏事件类型（事件值从10开始）：

为了可扩展性，我们如下划分并预留一部分

- `1x` 房间事件
- `2x` 音频事件
- `3x` 玩家操作
- `4x` 管理操作

| 事件名               | 类型值 | 方向     | 说明                                                         | 服务器行为        |
|----------------------|--------|----------|--------------------------------------------------------------| ------------      |
| `ROOM_CREATE`        | 10     | C→S      | 创建房间，附带用户名                                         | state update      |
| `ROOM_JOIN`          | 11     | C→S      | 加入房间，附带房间ID、用户名                                 | state update      |
| `ROOM_STATE`         | 12     | S→C      | 推送完整房间状态（玩家列表、准备状态、歌曲列表、标签组等）   | broadcast         |

| 事件名               | 类型值 | 方向     | 说明                                                         | 服务器行为        |
|----------------------|--------|----------|--------------------------------------------------------------| ------------      |
| `LOAD`               | 20     | S→C      | 音频URL、歌曲元数据、轮次索引、标签组结构                    | state & broadcast |
| `PLAY`               | 21     | S→C      | 开始播放~~，包含音频URL、歌曲元数据、轮次索引、标签组结构~~  | state & broadcast |
| `PAUSE`              | 22     | S→C      | 暂停播放（由抢答或房主触发），可包含播放进度（毫秒）         | state & broadcast |
| `SEEK`               | 23     | S→C      | 调整播放进度，但不改变播放状态                               | broadcast         |

| 事件名               | 类型值 | 方向     | 说明                                                         | 服务器行为        |
|----------------------|--------|----------|--------------------------------------------------------------| ------------      |
| `PLAYER_READY`       | 30     | C→S      | 玩家准备/取消准备                                            | state & broadcast |
| `GAME_START`         | 31     | S→C      | 房主开始游戏，禁止新玩家加入（断线重连可以）                 | state & broadcast |
| `COUNTDOWN`          | 32     | S→C      | 倒计时更新（3,2,1）  【可以不要？】                          | state & broadcast |
| `ATTEMPT_ANSWER`     | 33     | C→S      | 玩家抢答，触发暂停和入队                                     | state & broadcast |
| `YOUR_TURN`          | 34     | S→C      | 广播通知指定玩家开始作答，包含剩余时间（前端显示xxx正在作答）| state & broadcast |
| `SUBMIT_ANSWER`      | 35     | C→S      | 玩家提交勾选的标签ID列表及精准描述文本                       | state & broadcast |
| `ANSWER_BROADCAST`   | 36     | S→C      | 广播某玩家提交的答案（匿名或带玩家名，不含正确性）           | state & broadcast |
| `ANSWER_QUEUE`       | 37     | S→C      | 广播当前抢答队列顺序（用于前端展示排队状态）                 | state & broadcast |

| 事件名               | 类型值 | 方向     | 说明                                                         | 服务器行为        |
|----------------------|--------|----------|--------------------------------------------------------------| ------------      |
| `JUDGING`            | 40     | S→C      | 进入判分环节，房主端显示标准答案区（含标签组和描述候选）     | state & broadcast |
| `JUDGE_SUBMIT`       | 41     | C→S      | 房主提交正确答案标签ID列表和描述ID列表（或“无描述”）         | state & broadcast |
| `SCORE_UPDATE`       | 42     | S→C      | 更新积分榜                                                   | state & broadcast |

| 事件名               | 类型值 | 方向     | 说明                                                         | 服务器行为        |
|----------------------|--------|----------|--------------------------------------------------------------| ------------      |
| `ROUND_END`          | 38     | S→C      | 回合结束，准备下一轮                                         | state & broadcast |

| 事件名               | 类型值 | 方向     | 说明                                                         | 服务器行为        |
|----------------------|--------|----------|--------------------------------------------------------------| ------------      |
| `GAME_OVER`          | 13     | S→C      | 游戏结束，展示最终排名                                       | state & broadcast |

简单起见，游戏事件走 JSON 格式。统一约定如下（字段使用 `snake_case`）：

#### 通用 Envelope

```json
{
  "v": 1,
  "event": 20,
  "ts": 1735567890123,
  "request_id": "req_01JY8FQ9C6Y7R6J0M8N3K4P2T1",
  "seq": 1024,
  "trace_id": "tr_01JY8FR2N6C5Q1B2K8W9E0R7T6",
  "data": {
    "audio_url": "/static/audio/abc.mp3"
  }
}
```

字段说明：

- `v`（u8，必填）：协议版本，当前固定为 `1`。
- `event`（u8，必填）：事件类型值（见上表）。
- `ts`（u64，必填）：服务端/客户端发送时的 Unix 毫秒时间戳。
- `seq`（u64，可选）：广播序列号。仅 S→C 广播消息建议携带，按房间内单调递增。
- `trace_id`（string，可选）：链路追踪 ID，用于日志排障。
- `data`（object，必填）：事件业务载荷。

约束约定：

- 所有业务 ID 字段统一为 `*_id`（例如 `room_id`、`player_id`、`song_id`）。
- 时长/进度统一使用 `*_ms`（毫秒）。
- 列表字段使用复数命名（例如 `selected_tag_ids`、`scores`）。

#### 事件 data 字段定义

##### 1x 房间事件

- `ROOM_CREATE` (10, C→S)

  ```json
  {
    "username": "alice"
  }
  ```

  > 弃用，走RESTful

- `ROOM_JOIN` (11, C→S)

  ```json
  {
    "room_id": "ABC123",
    "username": "bob"
  }
  ```

  > 弃用，走RESTful

- `ROOM_STATE` (12, S→C)

  ```json
  {
    "room_id": "ABC123",
    "status": "waiting",
    "players": [
      { "player_id": 1, "username": "alice", "is_host": true, "is_ready": true, "score": 3 },
      { "player_id": 2, "username": "bob", "is_host": false, "is_ready": false, "score": 1 }
    ],
    "current_round_index": 1,
    "current_song_index": 0,
    "current_song_id": 123,
    "play_state": "paused",
    "play_progress_ms": 12500,
    "queue_player_ids": [2],
    "current_answerer_player_id": 2,
    "tag_groups": [
      {
        "group_id": 1,
        "name": "年代",
        "tags": [
          { "tag_id": 101, "name": "80年代" },
          { "tag_id": 102, "name": "90年代" }
        ]
      }
    ]
  }
  ```

- `GAME_OVER` (13, S→C)

  ```json
  {
    "room_id": "ABC123",
    "final_scores": [
      { "player_id": 1, "username": "alice", "score": 30, "rank": 1 },
      { "player_id": 2, "username": "bob", "score": 25, "rank": 2 }
    ]
  }
  ```

##### 2x 音频事件

- `LOAD` (20, S→C)

  ```json
  {
    "song_id": 123,
    "title": "歌曲名",
    "artist": "歌手",
    "cover_url": "https://...",
    "audio_url": "/static/audio/abc.mp3",
    "duration_ms": 215000,
    "round_index": 1,
    "tag_groups": [
      {
        "group_id": 1,
        "name": "年代",
        "tags": [
          { "tag_id": 101, "name": "80年代" },
          { "tag_id": 102, "name": "90年代" }
        ]
      }
    ]
  }
  ```

- `PLAY` (21, S→C)

  ```json
  {
    "song_id": 123,
    "progress_ms": 12500,
  }
  ```

- `PAUSE` (22, S→C)

  ```json
  {
    "song_id": 123,
    "progress_ms": 20123,
    "reason": "attempt_answer",
    "trigger_player_id": 2
  }
  ```

- `SEEK` (23, S→C)

  ```json
  {
    "song_id": 123,
    "progress_ms": 30000,
    "keep_playing": false
  }
  ```

##### 3x 玩家操作

- `PLAYER_READY` (30, C→S)

  ```json
  {
    "user_id": "ABC123",
    "is_ready": true
  }
  ```

- `GAME_START` (31, S→C)

  ```json
  {
    "room_id": "ABC123",
    "round_index": 1,
    "locked_join": true
  }
  ```

- `COUNTDOWN` (32, S→C)

  ```json
  {
    "round_index": 1,
    "remain_sec": 3
  }
  ```

- `ATTEMPT_ANSWER` (33, C→S)

  ```json
  {
    "room_id": "ABC123",
    "progress_ms": 12345
  }
  ```

- `YOUR_TURN` (34, S→C)

  ```json
  {
    "room_id": "ABC123",
    "song_id": 123,
    "answerer_player_id": 2,
    "time_limit_sec": 30,
    "queue_position": 1,
    "tag_groups": [
      {
        "group_id": 1,
        "name": "年代",
        "tags": [
          { "tag_id": 101, "name": "80年代" },
          { "tag_id": 102, "name": "90年代" }
        ]
      }
    ]
  }
  ```

- `SUBMIT_ANSWER` (35, C→S)

  ```json
  {
    "room_id": "ABC123",
    "song_id": 123,
    "selected_tag_ids": [101, 201],
    "description_text": "这是一首经典摇滚"
  }
  ```

- `ANSWER_BROADCAST` (36, S→C)

  ```json
  {
    "room_id": "ABC123",
    "song_id": 123,
    "player_id": 2,
    "username": "bob",
    "selected_tag_ids": [101, 201],
    "description_text": "这是一首经典摇滚",
    "answer_order": 1
  }
  ```

- `ANSWER_QUEUE` (37, S→C)

  ```json
  {
    "room_id": "ABC123",
    "queue_player_ids": [2, 5, 8]
  }
  ```

- `ROUND_END` (38, S→C)

  ```json
  {
    "room_id": "ABC123",
    "ended_round_index": 1,
    "next_round_index": 2,
    "next_song_id": 124
  }
  ```

##### 4x 管理操作

- `JUDGING` (40, S→C)

  ```json
  {
    "room_id": "ABC123",
    "song_id": 123,
    "round_index": 1,
    "tag_groups": [
      {
        "group_id": 1,
        "name": "年代",
        "tags": [
          { "tag_id": 101, "name": "80年代" },
          { "tag_id": 102, "name": "90年代" }
        ]
      }
    ],
    "description_candidates": [
      { "description_id": 1001, "text": "这是一首经典摇滚", "count": 3 },
      { "description_id": 1002, "text": "旋律优美", "count": 1 }
    ],
    "answers": [
      {
        "player_id": 2,
        "username": "bob",
        "selected_tag_ids": [101, 201],
        "description_text": "这是一首经典摇滚",
        "answer_order": 1
      }
    ]
  }
  ```

- `JUDGE_SUBMIT` (41, C→S)

  ```json
  {
    "room_id": "ABC123",
    "song_id": 123,
    "correct_tag_ids": [101, 201],
    "correct_description_ids": [1001],
    "new_correct_descriptions": ["歌手早年经典", "电影主题曲"],
    "skip_scoring": false
  }
  ```

- `SCORE_UPDATE` (42, S→C)

  ```json
  {
    "room_id": "ABC123",
    "round_index": 1,
    "scores": [
      { "player_id": 1, "username": "alice", "score_delta": 2, "total_score": 15 },
      { "player_id": 2, "username": "bob", "score_delta": 1, "total_score": 10 }
    ]
  }
  ```

#### ACK / ERROR 统一格式

- ACK（建议事件号沿用原事件号，`event` 不变）：

> 此处可以不要

  ```json
  {
    "v": 1,
    "event": 33,
    "ts": 1735567890666,
    "request_id": "req_01JY8FQ9C6Y7R6J0M8N3K4P2T1",
    "trace_id": "tr_01JY8FR2N6C5Q1B2K8W9E0R7T6",
    "data": {
      "ok": true
    }
  }
  ```

- ERROR（建议统一通过 `MESSAGE(255)` 或同事件号返回，二选一，务必全链路一致）：

  ```json
  {
    "v": 1,
    "event": 255,
    "ts": 1735567890777,
    "trace_id": "tr_01JY8FR2N6C5Q1B2K8W9E0R7T6",
    "data": {
      "ok": false,
      "error": {
        "code": "ROOM_NOT_FOUND",
        "message": "room_id does not exist",
        "details": {
          "room_id": "ABC123"
        }
      }
    }
  }
  ```

### 5.8 HTTP API 设计

> 约定：所有 API 接口以 `/api` 为前缀，`/ws/` 前缀用于 ws 连接

| 端点                 | 方法   | 说明                                   | 请求体/参数                                | 返回                                |
|----------------------|--------|----------------------------------------|--------------------------------------------| ----------------------------------- |
| `/api/room/`         | POST   | 创建房间                               | `{ username: string, tagGroups?: [...] }`  | `{ roomId: string, token: string }` |
| `/api/room/join`     | POST   | 加入房间                               | `{ roomId: string, username: string }`     | `{ token: string, roomState: ... }` |
| `/api/room/:roomId`  | GET    | 获取房间公开信息（用于展示）           | -                                          | 房间基本信息                        |
| `/api/song/search`   | GET    | 搜索歌曲（备用）                       | `q: string`                                | 歌曲列表                            |
| `/api/song/playlist` | POST   | 导入QQ音乐歌单                         | `{ playlistId: string }`                   | 歌曲列表                            |
| `/api/user/reconnect`| POST   | 通过Cookie重连（获取最新状态）         | Cookie中包含token                          | 房间状态                            |
| `/api/tags/suggest`  | GET    | 根据歌曲ID获取历史标签和描述推荐       | `songId: int`                              | 标签频次列表、描述频次列表          |

## 6. 数据模型设计

**以下 sql 语句内容仅供数据结构参考**。

实现上，使用 sqlalchemy orm 作为数据库交互接口；数据校验采用“边界优先”策略：

- HTTP/WS 输入边界可使用 pydantic（或等价校验器）做反序列化与约束；
- ORM 模型仅负责持久化，不承担输入校验职责；
- 二进制数据继续由现有的 frame 基类和相关类处理。

数据库访问约定（当前实现）：

- `db/crud.py` 中的写入函数只负责 `add/update + flush`，不在函数内部 `commit`；
- 事务提交与回滚由上层业务边界统一管理（例如 handler/service 一次请求或一次任务批次）；
- 多个 CRUD 调用可组合在同一事务中，保证原子性。

### 6.1 SQLite 表结构

使用 SQLite，启用外键约束（`PRAGMA foreign_keys = ON`），并采用 WAL 模式提高并发。

**用户表 `users`**

```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    display_suffix TEXT,  -- 用于显示的唯一后缀
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**房间表 `rooms`**

```sql
CREATE TABLE rooms (
    id TEXT PRIMARY KEY,  -- 房间ID，如 "ABC123"
    host_player_id INTEGER NOT NULL,  -- 房主玩家ID，关联 room_players.id
    playlist_id TEXT,     -- QQ音乐歌单ID
    tag_groups_json TEXT, -- 标签组配置的JSON序列化
    status INTEGER DEFAULT 0,   -- 0:等待中,1:游戏中,2:已结束
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP
);
```

**房间玩家表 `room_players`**

```sql
CREATE TABLE room_players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    is_host BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(room_id, username)  -- 同一房间内用户名唯一
);
```

**歌曲表 `songs`**

```sql
CREATE TABLE songs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT,                -- 如 qqmusic
    platform_song_id TEXT,
    title TEXT,
    artist TEXT,
    cover_url TEXT,
    audio_url TEXT,               -- 原始播放链接
    cached_path TEXT,             -- 本地缓存路径
    metadata_json TEXT,           -- 额外元数据（JSON）
    UNIQUE(platform_song_id)
);
```

**房间歌曲关联表 `room_songs`**

```sql
CREATE TABLE room_songs (
    room_id TEXT REFERENCES rooms(id) ON DELETE CASCADE,
    song_id INTEGER REFERENCES songs(id),
    song_order INTEGER,           -- 播放顺序
    PRIMARY KEY (room_id, song_id)
);
```

**标签组表 `tag_groups`**

```sql
CREATE TABLE tag_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    room_id TEXT REFERENCES rooms(id) ON DELETE CASCADE,  -- 可为空，表示全局模板
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**标签表 `tags`**

```sql
CREATE TABLE tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER REFERENCES tag_groups(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    UNIQUE(group_id, name)
);
```

**歌曲标签历史表 `song_tag_history`**

```sql
CREATE TABLE song_tag_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id INTEGER REFERENCES songs(id),
    tag_id INTEGER REFERENCES tags(id),
    judged_by_player_id INTEGER REFERENCES room_players(id),
    room_id TEXT REFERENCES rooms(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**精准描述历史表 `song_description_history`**

```sql
CREATE TABLE song_description_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id INTEGER REFERENCES songs(id),
    description_text TEXT NOT NULL,
    is_correct BOOLEAN DEFAULT 1,   -- 房主是否标记为正确
    judged_by_player_id INTEGER REFERENCES room_players(id),
    room_id TEXT REFERENCES rooms(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**积分记录表 `scores`**

```sql
CREATE TABLE scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id TEXT REFERENCES rooms(id),
    player_id INTEGER REFERENCES room_players(id),
    round_index INTEGER,
    score_delta INTEGER,            -- 本轮得分变化
    total_score INTEGER,             -- 累计得分（可冗余）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**玩家答案记录表 `player_answers`**（可选，用于审计和重放）

```sql
CREATE TABLE player_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id TEXT REFERENCES rooms(id),
    player_id INTEGER REFERENCES room_players(id),
    song_id INTEGER REFERENCES songs(id),
    round_index INTEGER,
    selected_tag_ids TEXT,          -- JSON数组
    description_text TEXT,
    answer_order INTEGER,           -- 该轮抢答顺序
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

以下是对应的 ORM 模型

```python
# 文件: models.py
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text, JSON, 
    ForeignKey, UniqueConstraint, PrimaryKeyConstraint, func
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()

class Room(Base):
    """房间表 rooms"""
    __tablename__ = 'rooms'

    id = Column(String, primary_key=True)
    host_player_id = Column(Integer, ForeignKey('room_players.id'), nullable=False)  # 房主玩家ID
    playlist_id = Column(String)
    tag_groups_json = Column(JSON)
    status = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.current_timestamp())
    ended_at = Column(DateTime, nullable=True)

    # 关系
    host_player = relationship('RoomPlayer', foreign_keys=[host_player_id])
    players = relationship('RoomPlayer', back_populates='room', foreign_keys='RoomPlayer.room_id')
    room_songs = relationship('RoomSong', back_populates='room')
    tag_groups = relationship('TagGroup', back_populates='room')
    scores = relationship('Score', back_populates='room')
    player_answers = relationship('PlayerAnswer', back_populates='room')
    song_tag_history = relationship('SongTagHistory', back_populates='room')
    song_description_history = relationship('SongDescriptionHistory', back_populates='room')


class RoomPlayer(Base):
    """房间玩家表 room_players"""
    __tablename__ = 'room_players'

    id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(String, ForeignKey('rooms.id', ondelete='CASCADE'), nullable=False)
    username = Column(String, nullable=False)
    is_host = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.current_timestamp())

    __table_args__ = (
        UniqueConstraint('room_id', 'username', name='uq_room_player_username'),
    )

    # 关系
    room = relationship('Room', back_populates='players', foreign_keys=[room_id])
    scores = relationship('Score', back_populates='player')
    player_answers = relationship('PlayerAnswer', back_populates='player')
    song_tag_judgements = relationship('SongTagHistory', back_populates='judged_by_player')
    song_description_judgements = relationship('SongDescriptionHistory', back_populates='judged_by_player')


class Song(Base):
    """歌曲表 songs"""
    __tablename__ = 'songs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String)
    platform_song_id = Column(String)
    title = Column(String)
    artist = Column(String)
    cover_url = Column(String)
    audio_url = Column(String)
    cached_path = Column(String)
    metadata_json = Column(JSON)

    __table_args__ = (
        UniqueConstraint('platform_song_id', name='uq_song_platform_id'),
    )

    rooms = relationship('RoomSong', back_populates='song')


class RoomSong(Base):
    """房间歌曲关联表 room_songs"""
    __tablename__ = 'room_songs'

    room_id = Column(String, ForeignKey('rooms.id', ondelete='CASCADE'), primary_key=True)
    song_id = Column(Integer, ForeignKey('songs.id'), primary_key=True)
    song_order = Column(Integer)

    room = relationship('Room', back_populates='room_songs')
    song = relationship('Song', back_populates='rooms')


class TagGroup(Base):
    """标签组表 tag_groups"""
    __tablename__ = 'tag_groups'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    room_id = Column(String, ForeignKey('rooms.id', ondelete='CASCADE'), nullable=True)
    created_at = Column(DateTime, default=func.current_timestamp())

    room = relationship('Room', back_populates='tag_groups')
    tags = relationship('Tag', back_populates='group', cascade='all, delete-orphan')


class Tag(Base):
    """标签表 tags"""
    __tablename__ = 'tags'

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey('tag_groups.id', ondelete='CASCADE'), nullable=False)
    name = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint('group_id', 'name', name='uq_tag_group_name'),
    )

    group = relationship('TagGroup', back_populates='tags')
    song_history = relationship('SongTagHistory', back_populates='tag')


class SongTagHistory(Base):
    """歌曲标签历史表 song_tag_history"""
    __tablename__ = 'song_tag_history'

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_id = Column(Integer, ForeignKey('songs.id'), nullable=False)
    tag_id = Column(Integer, ForeignKey('tags.id'), nullable=False)
    judged_by_player_id = Column(Integer, ForeignKey('room_players.id'), nullable=False)
    room_id = Column(String, ForeignKey('rooms.id'), nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())

    song = relationship('Song')
    tag = relationship('Tag', back_populates='song_history')
    judged_by_player = relationship('RoomPlayer', foreign_keys=[judged_by_player_id], back_populates='song_tag_judgements')
    room = relationship('Room', back_populates='song_tag_history')


class SongDescriptionHistory(Base):
    """精准描述历史表 song_description_history"""
    __tablename__ = 'song_description_history'

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_id = Column(Integer, ForeignKey('songs.id'), nullable=False)
    description_text = Column(Text, nullable=False)
    is_correct = Column(Boolean, default=True)
    judged_by_player_id = Column(Integer, ForeignKey('room_players.id'), nullable=False)
    room_id = Column(String, ForeignKey('rooms.id'), nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())

    song = relationship('Song')
    judged_by_player = relationship('RoomPlayer', foreign_keys=[judged_by_player_id], back_populates='song_description_judgements')
    room = relationship('Room', back_populates='song_description_history')


class Score(Base):
    """积分记录表 scores"""
    __tablename__ = 'scores'

    id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(String, ForeignKey('rooms.id'), nullable=False)
    player_id = Column(Integer, ForeignKey('room_players.id'), nullable=False)
    round_index = Column(Integer)
    score_delta = Column(Integer)
    total_score = Column(Integer)
    created_at = Column(DateTime, default=func.current_timestamp())

    room = relationship('Room', back_populates='scores')
    player = relationship('RoomPlayer', back_populates='scores')


class PlayerAnswer(Base):
    """玩家答案记录表 player_answers"""
    __tablename__ = 'player_answers'

    id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(String, ForeignKey('rooms.id'), nullable=False)
    player_id = Column(Integer, ForeignKey('room_players.id'), nullable=False)
    song_id = Column(Integer, ForeignKey('songs.id'), nullable=False)
    round_index = Column(Integer)
    selected_tag_ids = Column(JSON)
    description_text = Column(Text)
    answer_order = Column(Integer)
    created_at = Column(DateTime, default=func.current_timestamp())

    room = relationship('Room', back_populates='player_answers')
    player = relationship('RoomPlayer', back_populates='player_answers')
    song = relationship('Song')
```

### 6.2 Redis 数据结构

- **房间实时状态** `room:{roomId}` (Hash)
  - `host_player_id`: 房主玩家ID
  - `status`: waiting/playing/ended
  - `current_song_index`: 当前播放歌曲索引
  - `current_round_state`: 轮次状态 (playing/paused/judging)
  - `play_progress`: 当前播放进度（毫秒）
  - `tag_groups`: 标签组配置（JSON，与数据库同步）
  - `answer_queue`: 抢答队列（List，存储玩家ID顺序）
  - `current_answerer`: 当前正在作答的玩家ID
  - `answers`: 已提交的答案（Hash，玩家ID -> JSON {tags:[], description:""}）
- **玩家集合** `room:{roomId}:players` (Set) 存储玩家ID
- **玩家准备状态** `room:{roomId}:ready` (Hash) 玩家ID -> boolean
- **玩家连接映射** `player:{playerId}:ws` (String) 存储WebSocket连接ID（用于单播）
- **歌曲队列** `room:{roomId}:song_queue` (List) 存储歌曲ID列表
- **会话映射** `session:{token}` (String) 存储 `room_id:player_id`，并设置过期时间

---

## 7. 接口定义（详细）

### 7.1 WebSocket 事件载荷示例

- **`PLAY`** (S→C)

  ```json
  {
    "song_id": 123,
    "title": "歌曲名",
    "artist": "歌手",
    "cover_url": "https://...",
    "audio_url": "/static/audio/abc.mp3",
    "round_index": 1
  }
  ```

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
    "selected_tag_ids": [101, 201],  // 标签ID列表（每组至多一个）
    "description_text": "这是一首经典摇滚"
  }
  ```

- **`ANSWER_BROADCAST`** (S→C)

  ```json
  {
    "player_id": 42,
    "username": "bob",
    "selected_tag_ids": [101, 201],
    "description_text": "这是一首经典摇滚"
  }
  ```

- **`ANSWER_QUEUE`** (S→C)

  ```json
  {
    "queue_player_ids": [42, 37, 15]  // 玩家ID列表，按抢答顺序排列
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
      { "player_id": 42, "username": "bob", "selected_tag_ids": [101,201], "description_text": "这是一首经典摇滚" },
      { "player_id": 37, "username": "alice", "selected_tag_ids": [102], "description_text": "节奏感强" }
    ]
  }
  ```

- **`JUDGE_SUBMIT`** (C→S, 仅房主)

  ```json
  {
    "correct_tag_ids": [101, 201],
    "correct_description_ids": [1001],  // 空数组表示无正确描述
    "new_correct_descriptions": ["歌手早年经典", "电影主题曲"], // 手动输入的新描述，会被添加到历史中，同时生成description_id供未来选择
    "skip_scoring": false
  }
  ```

- **`SCORE_UPDATE`** (S→C)

  ```json
  {
    "scores": [
      { "player_id": 42, "username": "bob", "score": 15 },
      { "player_id": 37, "username": "alice", "score": 10 }
    ]
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

### 7.2 HTTP API 示例

#### **创建房间**

```json
POST /api/room/create
Content-Type: application/json

{
  "username": "alice",
  "tagGroups": [
    { "name": "年代", "tags": ["80年代", "90年代", "00年代"] },
    { "name": "曲风", "tags": ["摇滚", "流行", "民谣"] }
  ]
}

响应 200:
{
  "roomId": "ABC123",
  "playerId": 1,
  "token": "eyJhbGci...",
  "expires_in": 7200
}
Set-Cookie: token=eyJhbGci...; HttpOnly; Path=/; Max-Age=7200
```

#### **加入房间**

```json
POST /api/room/join
Content-Type: application/json

{"roomId": "ABC123", "username": "bob"}

响应 200:
{
  "token": "eyJhbGci...",
  "playerId": 2,
  "roomState": {
    "host": "alice",
    "players": [
      {"playerId": 1, "username": "alice"},
      {"playerId": 2, "username": "bob"}
    ],
    "songCount": 10,
    "currentRound": 0,
    "status": "waiting",
    "tagGroups": [
      { "name": "年代", "tags": ["80年代", "90年代", "00年代"] },
      { "name": "曲风", "tags": ["摇滚", "流行", "民谣"] }
    ]
  }
}
Set-Cookie: token=...; HttpOnly; ...
```

#### **获取房间公开信息**

```json
GET /api/room/ABC123

响应 200:
{
  "roomId": "ABC123",
  "host": "alice",
  "playerCount": 2,
  "songCount": 10,
  "status": "waiting"
}
```

#### **导入QQ音乐歌单**

```json
POST /api/song/playlist
Content-Type: application/json

{"playlistId": "123456789"}

响应 200:
{
  "songs": [
    {"songId": 101, "title": "歌曲1", "artist": "歌手1"},
    {"songId": 102, "title": "歌曲2", "artist": "歌手2"}
  ]
}
```

#### **重连**

```json
POST /api/player/reconnect
Cookie: token=eyJhbGci...

响应 200:
{
  "roomId": "ABC123",
  "playerId": 2,
  "roomState": { ... }  // 同加入房间返回的roomState
}
```

## 8. 前端要点

- **音频预加载**：使用 Web Audio API 或 `<audio>` 标签，提前获取音频文件并缓冲。
- **进度同步**：收到 `PAUSE` 事件后，根据 `progress_ms` 跳转到指定位置，并暂停。
- **重连逻辑**：检测到 WebSocket 断开，启动指数退避重连，重连成功后请求同步状态。
- **UI 状态机**：根据房间状态展示不同界面（准备区、播放区、作答区、判分区）。作答区需显示剩余时间、标签组（每组单选）和精准描述输入框。
- **排队展示**：前端应展示当前抢答队列，让玩家了解自己排在第几位。
- **房主权限控制**：房主界面显示额外控件（开始游戏、结束回合、判分提交），判分界面展示所有玩家的答案，以及标签组勾选框和描述候选列表。

## 9. 部署方案

放过我吧别用docker

使用 Docker Compose 编排服务，确保易部署：

```yaml
version: '3.8'
services:
  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    depends_on:
      - redis
    environment:
      REDIS_URL: redis://redis:6379
      DATABASE_PATH: /app/data/game.db   # SQLite 文件路径
      AUDIO_CACHE_DIR: /app/static/audio
    volumes:
      - ./backend/data:/app/data          # 持久化 SQLite 文件
      - ./backend/static/audio:/app/static/audio
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./frontend/dist:/usr/share/nginx/html
      - ./nginx.conf:/etc/nginx/conf.d/default.conf
    depends_on:
      - backend
volumes:
  redis_data:
```

**说明**：

- SQLite 数据库文件 `game.db` 存放在宿主机的 `./backend/data` 目录，通过 volume 挂载到容器 `/app/data`，实现数据持久化。
- 音频缓存目录同样挂载，避免容器重启丢失。
- Redis 使用 AOF 持久化，数据保存在 `redis_data` volume。
- 前端静态文件通过 Nginx 提供服务，API 和 WebSocket 反向代理到后端。

**易部署性**：只需安装 Docker 和 Docker Compose，运行 `docker-compose up -d` 即可启动整个系统。SQLite 无需额外数据库服务，适合小型部署和开发测试。

## 10. 后续优化方向

- **防作弊**：限制抢答后必须等待一定时间才能再次抢答；播放进度同步时考虑网络延迟；可引入音频指纹技术防止外放录音。
- **标签组模板库**：允许用户保存常用标签组配置，供以后快速选用。
- **音频来源扩展**：支持网易云、Spotify 等其他平台。
- **观战模式**：允许游客进入房间观看，不参与游戏。
- **数据统计**：记录玩家胜率、常用标签、精准描述词云，形成个人报告。
- **性能优化**：若房间并发增加，可考虑将 SQLite 替换为 PostgreSQL 或使用读写分离；但当前设计已预留扩展空间，只需修改数据库连接层。
- **微服务拆分**：若未来需要，可将房间管理、游戏逻辑拆分为独立服务，但当前单体架构足够满足需求且易于维护。
