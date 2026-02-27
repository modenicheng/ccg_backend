# 播放状态同步实现记录（前后端）

> 更新时间：2026-02-27  
> 范围：`ccg_frontend` + `ccg_backend`

## 1. 目标

实现房间内播放状态统一控制：

- 仅管理端（房主）可主动发送 `PLAY / PAUSE / SEEK`
- 服务端负责权限校验、状态记录、转发（排除发送者）
- 客户端收到同步消息后按 `progress_ms + offset_ts` 修正进度
- 拖动进度条时避免 UI 闪动

---

## 2. 协议约定

游戏事件沿用 `GameEventType`：

- `PLAY = 20`
- `PAUSE = 21`
- `SEEK = 22`

统一消息结构：

```json
{
  "event": 20|21|22,
  "ts": 1740000000000,
  "data": {
    "progress_ms": 12345,
    "offset_ts": 1740000000000,
    "audio_url": "https://cdn.modenc.top/files/Orig.mp3"
  }
}
```

字段说明：

- `progress_ms`：发送端采样到的播放进度（毫秒）
- `offset_ts`：发送端采样该进度时的校准时间戳（毫秒）
- `audio_url`：当前播放音频链接（用于客户端一致性校验）
- `ts`：消息发送时间（毫秒）

---

## 3. 后端实现

### 3.1 JSON 事件分发接入

文件：`ccg_backend/main.py`

- 在 WebSocket 文本消息分支中：
  1. 解析 JSON
  2. 读取 `event`
  3. 映射到 `GameEventType`
  4. 调用 `handle_json(...)`
- 将认证通过的用户对象挂到 `websocket.state.user`

### 3.2 handler 注册机制扩展

文件：`ccg_backend/handlers/__init__.py`

- `regist` 支持 `EventType | GameEventType`
- `handle_json` 支持 game event 分发
- 新增模块加载：`from . import game_events`

### 3.3 播放控制 handler

文件：`ccg_backend/handlers/game_events.py`

- 新增 `PLAY / PAUSE / SEEK` 处理器
- 房主权限判断：
  - 非房主返回错误消息
- 通过 Pydantic 校验消息结构（`schemas/game_events.py`）
- 更新 Redis 房间播放状态
- 广播给同房间其他客户端（`except_clients={websocket}`）

### 3.4 Pydantic 模型

文件：`ccg_backend/schemas/game_events.py`

- `PlayControlData`
- `PlayMessage`
- `PauseMessage`
- `SeekMessage`

### 3.5 Redis 状态记录

文件：`ccg_backend/cache/utils.py`

新增 `update_playback_state(...)`，写入：

- `current_round_state`
- `play_progress`
- `play_offset_ts`
- `last_control_ts`
- `last_control_event`
- `audio_url`（存在时）

---

## 4. 前端实现

### 4.1 房主控制入口

文件：`ccg_frontend/src/pages/RoomPage.tsx`

- 房主卡片新增播放控制按钮
- 播放/暂停合并为一个按钮（按当前状态切换）
- 图标：
  - 播放：`heroicons:play`
  - 暂停：`heroicons:pause`
  - 下一轮：`heroicons:chevron-double-right-20-solid`

### 4.2 本地行为 + 服务端同步

`sendPlaybackControl(...)`：

- 房主点击 `PLAY/PAUSE` 时先本地执行（避免被服务端排除广播后自身不更新）
- 随后发送事件给服务端，由服务端转发给其他端

### 4.3 SEEK 同步算法

客户端接收 `SEEK` 后计算：

$$
expectedMs = progress\_ms + \max(0, nowCalibrated - offset\_ts)
$$

仅当偏差超过阈值才 seek：

- `AUDIO_SYNC_THRESHOLD_MS = 50`

### 4.4 防拖动闪动

文件：`ccg_frontend/src/pages/RoomPage.tsx`

新增组件内拖动标记：

- `isProgressDraggingRef`

拖动期间禁止以下更新：

- `audio onTimeUpdate` 对进度条的回写
- `applyRemoteProgress` 的远端进度覆盖

从而避免拖动过程中进度条来回跳动（闪动）。

### 4.5 canvas 初始化

在播放器初始化阶段自动调用 `initCanvas(...)`，并保留兜底初始化逻辑，解决波形图不显示问题。

---

## 5. 关键参数

当前延迟/同步相关参数：

- 进度修正阈值：`AUDIO_SYNC_THRESHOLD_MS = 200ms`
- 心跳启动参数：`startHeartbeat(ws, 1000, 1000)`
- 时间基准：`getCalibratedNow()`（基于心跳估算时钟偏移）

---

## 6. 已完成验证

- 前端：`pnpm exec tsc --noEmit` 通过
- 后端：关键改动文件 `py_compile` 通过
- 功能回归：
  - 管理端播放/暂停本地立即生效
  - 客户端可接收同步
  - 进度拖动闪动问题已修复

---

## 7. 建议后续

1. 增加 WebSocket 集成测试（owner/非 owner 权限与广播行为）
2. 在 `ROOM_STATE` 中补充播放状态快照字段，支持重连后无缝恢复
3. 将拖动更新改为 `requestAnimationFrame` 节流，进一步优化流畅度
