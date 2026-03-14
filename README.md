# CCG 全栈项目说明（Frontend + Backend）

> 本文档面向当前工作区中的两个仓库：
>
> - `ccg_frontend`（React + Vite）
> - `ccg_backend`（FastAPI + SQLAlchemy + Redis + Huey）

---

## 1. 项目概览

CCG 是一个多人实时猜歌系统，核心能力包括：

- 房间创建 / 加入 / 观战
- 歌曲与歌单管理（含 QQ 歌单导入）
- WebSocket 实时同步（播放控制、抢答、判分、积分）
- 音频预加载与缓存

前端负责：交互界面、音频播放体验、WebSocket 客户端状态管理。  
后端负责：业务状态机、事件分发、数据库持久化、缓存与异步任务。

---

## 2. 仓库与目录结构（详细）

### 2.1 前端仓库 `ccg_frontend`

```text
ccg_frontend/
├─ src/
│  ├─ App.tsx                 # 路由入口：大厅/房间/管理/观战
│  ├─ main.tsx                # React 挂载与主题初始化
│  ├─ api/                    # REST API 封装（room/song/songlist/tag/room_songs）
│  ├─ pages/
│  │  ├─ HomePage.tsx         # 创建房间/加入房间/观战入口
│  │  ├─ RoomPage.tsx         # 主游戏页（WebSocket 事件中心）
│  │  ├─ RoomManagePage.tsx   # 房间管理（歌单、歌曲、TagGroup）
│  │  └─ SpectatorPage.tsx    # 观战页（只读同步）
│  ├─ wsClient/
│  │  ├─ index.ts             # WS 类：连接、重连、事件分派
│  │  └─ handlers.ts          # 心跳与时钟偏移处理
│  ├─ stores/
│  │  ├─ persistStore.ts      # 持久化：主题、音量、房间用户
│  │  ├─ webSocketStore.ts    # 连接态、延迟、时钟偏移、ws实例
│  │  └─ gameStore.ts         # 房间状态与积分状态
│  ├─ audioPlayer/            # 音频播放与可视化
│  ├─ components/             # UI 组件
│  ├─ types/                  # 事件与消息类型定义
│  └─ utils/
│     └─ roomAuth.ts          # token/user 写入 cookie + sessionStorage
├─ vite.config.ts             # /api 与 /ws 代理到后端
├─ package.json               # pnpm scripts 与前端依赖
└─ README.md
```

前端技术栈：React 19、TypeScript、Vite 7、Tailwind CSS 4、daisyUI 5、Zustand。

### 2.2 后端仓库 `ccg_backend`

```text
ccg_backend/
├─ main.py                    # FastAPI 入口、WS 端点、静态托管、生命周期
├─ router/                    # REST API 路由层
│  ├─ room.py                 # 房间创建/加入/信息/配置
│  ├─ room_songs.py           # 房间歌曲队列管理
│  ├─ song.py                 # 歌曲 CRUD + 音频缓存接口
│  ├─ songlist.py             # 歌单 CRUD + 异步抓取任务
│  ├─ tags.py                 # Tag / TagGroup 管理
│  └─ audio_stream.py         # 音频流相关
├─ handlers/                  # WebSocket 事件处理
│  ├─ registe_manager.py      # 事件注册 + 分发 + Pydantic 校验
│  ├─ connection_lifespan.py  # 连接/断开生命周期 + 部分事件
│  ├─ audio_events_2x.py      # 20~29 音频控制事件
│  ├─ round_events_3x.py      # 30~39 回合事件
│  └─ judge_events_4x.py      # 40~49 判分事件
├─ db/
│  ├─ models.py               # ORM 模型
│  ├─ session.py              # AsyncSession 管理
│  └─ crud/                   # 数据访问层
├─ cache/
│  ├─ room_cache.py           # 房间状态缓存
│  ├─ room_state_manager.py   # 回合/播放状态管理
│  ├─ file_cache.py           # 音频文件 LRU 缓存
│  └─ connection.py           # Redis 连接
├─ mq/
│  └─ tasks.py                # Huey 异步任务（抓歌单/下载音频等）
├─ config/
│  └─ settings.py             # 配置加载（os.environ > .env > yaml）
├─ alembic/                   # 数据库迁移
├─ tests/                     # 单元/集成测试
└─ pyproject.toml             # Python 依赖与测试配置
```

后端技术栈：FastAPI、SQLAlchemy 2.x（async）、PostgreSQL、Redis、Huey、Alembic。

---

## 3. 前后端交互逻辑（核心链路）

### 3.1 登录与入房链路（HTTP + Cookie/Session）

1. 前端大厅页调用：
   - `POST /api/room/`（创建房间）
   - `POST /api/room/{roomid}`（加入房间）
2. 后端返回 `token / user_id / username`。
3. 前端通过 `src/utils/roomAuth.ts` 将认证信息写入：
   - `cookie`（供后端 WebSocket 握手读取）
   - `sessionStorage`（前端本地恢复）

### 3.2 WebSocket 实时链路

- 玩家：`/ws/{roomid}`
- 观战：`/ws/{roomid}/watch`

后端在 `main.py` 中统一接收消息，并通过 `handlers/registe_manager.py` 分发。  
分发流程：

1. 解析 event
2. 根据 event 找到处理器
3. 使用对应 Pydantic schema 做前置校验
4. 执行业务逻辑（DB / Redis / 广播）

前端 `RoomPage.tsx` / `SpectatorPage.tsx` 注册大量 JSON 事件监听，驱动 UI 状态同步。

### 3.3 典型实时事件流

#### 房间初始化

- 连接成功后，后端下发 `ROOM_STATE`。
- 前端据此建立房间快照（玩家、标签组、播放状态、积分）。

#### 音频控制

- 房主发送：`PLAY` / `PAUSE` / `SEEK`
- 后端校验后广播
- 客户端依据 `progress_ms + offset_ts` 做时间校准，修正播放进度

#### 抢答与判分

- 玩家：`ATTEMPT_ANSWER`、`SUBMIT_ANSWER`
- 后端：`ANSWER_QUEUE`、`YOUR_TURN`、`ANSWER_BROADCAST`
- 房主判分：`JUDGING`、`JUDGE_SUBMIT`
- 全员更新：`SCORE_UPDATE`

### 3.4 管理端链路（RoomManagePage）

前端管理页通过 REST 完成配置变更：

- 房间配置：`PATCH /api/room/{roomid}`
- 标签：`/api/tags/*`
- 歌曲：`/api/songs/*`
- 歌单：`/api/songlists/*`
- 房间歌曲队列：`/api/rooms/{roomid}/songs/*`

后端在房间歌曲变更后会触发预下载任务，提升后续播放命中率。

---

## 4. 部署与运行流程（从 0 到可用）

## 4.1 基础依赖

- Node.js >= 20（前端）
- pnpm >= 10（前端）
- Python >= 3.12（后端）
- uv（后端包管理）
- PostgreSQL（生产必需）
- Redis（实时状态与任务队列建议必备）

### 4.2 后端配置

1. 在 `ccg_backend` 复制：
   - `.env.template` → `.env`
   - （可选）`config.template.yaml` → `config.yaml`
2. 配置加载优先级：

`os.environ > .env > config.yaml`

1. 关键配置项（示例）：

- `CCG_DATABASE_URL`
- `CCG_REDIS_URL`
- `CCG_QQ_MUSIC_COOKIE`
- `CCG_AUDIO_DOWNLOAD_DIR`
- `CCG_LOG_LEVEL`

### 4.3 数据库迁移

在 `ccg_backend` 执行：

- 生成迁移：`uv run alembic revision --autogenerate -m "..."`
- 应用迁移：`uv run alembic upgrade head`

### 4.4 启动后端

开发模式：

- `uv run uvicorn main:app --reload --port 8000`

默认提供：

- REST：`http://localhost:8000/api/...`
- WS：`ws://localhost:8000/ws/{roomid}`

### 4.5 启动前端（开发联调）

在 `ccg_frontend`：

- 安装：`pnpm install`
- 启动：`pnpm dev`

`vite.config.ts` 已配置代理：

- `/api` → `http://localhost:8000`
- `/ws` → `ws://localhost:8000`

因此开发时前端可直接用同源路径访问后端。

### 4.6 生产部署建议

#### 方案 A：前后端分离部署（推荐）

- 前端：`pnpm build` 后将 `dist/` 部署到 Nginx / CDN
- 后端：Uvicorn/Gunicorn + 反向代理（Nginx）
- 通过网关统一转发 `/api` 和 `/ws`

#### 方案 B：后端托管前端静态文件

后端 `main.py` 会尝试读取：

`../ccg_frontend/dist`

若存在则返回前端 `index.html` 与静态资源。  
可用于简化单机部署，但需注意构建产物路径与发布流程一致。

### 4.7 异步任务进程（可选但推荐）

需要启用 Huey 消费者以处理歌单抓取/音频下载：

- `uv run huey_consumer.py mq.tasks.huey`

---

## 5. 常见开发命令

### 前端

- `pnpm dev`
- `pnpm build`
- `pnpm lint`

### 后端

- `uv run uvicorn main:app --reload --port 8000`
- `uv run pytest`
- `uv run alembic upgrade head`

---

## 6. 排障建议

- 前端能打开但接口失败：检查后端是否在 `:8000` 运行。
- WebSocket 401/1008：检查 cookie 中 room token 是否存在且 roomId 对应。
- 歌单导入失败：先确认 `CCG_QQ_MUSIC_COOKIE` 有效。
- 房间状态不同步：检查 Redis 可用性与后端日志中的事件分发错误。

---

## 7. 贡献建议

- 前端遵循：`ccg_frontend/AGENTS.md`（pnpm、daisyUI/Tailwind、TypeScript 约定）
- 后端遵循：`ccg_backend/AGENTS.md`（uv、yapf/pylint、async + schema 约定）
- 提交信息建议使用 Conventional Commits（`feat`/`fix`/`docs` 等）
