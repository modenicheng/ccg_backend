# 实施计划：完成剩余 WebSocket 事件实现

## 任务概述

### 待实现功能

1. `START_POS_UPDATE` (14) - 起始位置控制
2. `GAME_OVER` (13) - 游戏结束（支持自动触发和房主手动触发）
3. `PRELOAD_AUDIO` (23) - 音频预加载（需与现有代码对接，避免重复）
4. **完成** `CLEAR_ANSWER_QUEUE` (38) - 完成前端事件处理和WebSocket事件注册

***

## 当前状态分析

### 已存在的代码（避免重复工作）

#### 1. 音频下载和缓存（mq/tasks.py）

* `download_audio_file()` - 下载音频文件

* `download_and_cache_song()` - 下载并缓存单曲

* `_resolve_audio_download_path()` - 解析下载路径

* 音频存储在 `assets/audio/` 目录

#### 2. Redis 房间管理（cache/utils.py）

* `RedisRoomManager` 类已存在

* 方法：`_load_room_state()`, `_save_room_state()`, `get_room_info()`

* 状态模型：`RoomStateCache`（cache/schemas.py）

#### 3. 房间基础状态（cache/schemas.py）

* `RoomBaseStateCache` - 包含 status, song\_start\_range\_percent 等

* `PlaybackState` - 播放状态

* `AnswerQueueItem` - 抢答队列项

#### 4. 抢答队列操作（cache/room\_cache.py）

* `clear_answer_queue()` - 清空队列函数已实现

* `get_answer_queue()` - 获取队列

* `append_attempt_answer_player()` - 添加玩家到队列

#### 5. 数据库模型（db/models.py）

* `Room` 模型有 `status` 字段（WAITING, RUNNING, ENDED）

* `ScoreRecord` 模型记录玩家分数

* `User` 模型关联房间和玩家

#### 6. Schema 定义（schemas/ws\_messages/）

* `playback_schemas.py` - `PreloadAudioMessage` 已定义

* `room_schemas.py` - 需要添加新的 schema

***

## 后端 RoomStateMachine 统一房间状态可行性评估

**Session ID:** ses\_34ec6534fffeW3to4qhZ63ewfG
**Created:** 2026/3/3 09:04:32
**Updated:** 2026/3/3 09:06:03

### User

做个调研，现在后端有必要抽象一个 RoomStateMachine 出来用于统一处理房间状态吗？（当然如果有这个状态机类，我们也会对应有一个hashmap来存roomid和这些实例的关系） 然后问题来了，如果是服务端以外下线，这个方法不利于状态恢复，如果直接用redis/db就不用这么麻烦

### Agent

**当前状态管理方式：**

* 数据库 `Room.status` 存储持久状态

* Redis 缓存 `RoomBaseStateCache` 存储实时状态（播放进度、玩家列表、答题队列等）

* 状态转换逻辑分散在各 WebSocket 处理器（如 `round_events_3x.py:139` 直接修改 `room.status`）

**抽象 RoomStateMachine 的利弊：**

| 优点                               | 缺点                      |
| -------------------------------- | ----------------------- |
| 集中化状态转换逻辑，避免分散在各处理器              | 增加架构复杂度                 |
| 统一校验状态转移合法性（如 WAITING → RUNNING） | 若状态机实例驻留内存，服务重启/扩展时状态丢失 |
| 便于维护和测试，新增状态/转换更简单               | 每次操作需读写 Redis/DB，可能增加延迟 |

**建议方案：**
实现**无状态**的 `RoomStateMachine`，仅封装转换逻辑，不持有实例状态。所有状态持久化在 Redis/DB，状态机负责：

1. 验证当前状态是否允许转移到目标状态
2. 原子化更新数据库 `Room.status` 和 Redis `RoomBaseStateCache`
3. 触发相关副作用（如广播状态变更事件）

**示例设计：**

```python
class RoomStateMachine:
    async def transition(self, room_id: str, target: RoomStatus, session: AsyncSession) -> bool:
        # 1. 从 DB 加载当前状态
        # 2. 校验转移规则（如 WAITING → RUNNING 允许，RUNNING → WAITING 禁止）
        # 3. 更新 DB
        # 4. 更新 Redis 缓存（通过 room_cache.save_room_state）
        # 5. 返回成功/失败
```

**结论：**

* **推荐**实现无状态状态机，统一转换逻辑，保持可恢复性。

* **避免**内存驻留实例，直接依赖 Redis/DB 作为状态源。

* 现有代码中状态转换较分散，抽象后可提升可维护性，且不影响水平扩展。

***

## 一、核心重构：房间状态管理类

### 目标

抽象一个 `RoomStateManager` 类，统一管理房间状态（DB + Cache），作为核心状态机。

***

## 二、后端实施步骤

### 阶段 1：创建 RoomStateManager

**文件**: `cache/room_state_manager.py`

* 实现上述 RoomStateManager 类

* 集成现有 `mq/tasks.py` 的下载功能

* 集成现有 `cache/room_cache.py` 的缓存操作

* 集成现有 `cache/utils.py` 的 RedisRoomManager

### 阶段 2：创建 Schema 定义

**文件**: `schemas/ws_messages/room_schemas.py`

添加：

* `StartPosUpdateData` / `StartPosUpdateMessage`

* `GameOverScore` / `GameOverData` / `GameOverMessage`

* `ClearAnswerQueueData` / `ClearAnswerQueueMessage`

### 阶段 3：重构现有 Handlers

使用 RoomStateManager 重构：

1. **`handlers/round_events_3x.py`**

   * `handle_game_start` - 使用 manager.start\_game()

   * `handle_round_end` - 使用 manager.end\_round()

2. **`handlers/connection_lifespan.py`**

   * `handle_start_pos_update` - 使用 manager.set\_start\_position()

   * `handle_clear_answer_queue` - 使用 manager.clear\_answer\_queue()

   * 新增 `handle_game_over_manual` - 房主手动结束游戏

### 阶段 4：添加缓存函数

**文件**: `cache/room_cache.py`

添加：

* `set_room_start_position()`

* `get_room_start_position()`

***

## 三、前端实施步骤

### 阶段 1：更新类型定义

**文件**: `types/eventTypes.ts`

* 添加 `CLEAR_ANSWER_QUEUE: 38`

**文件**: `types/wsMessages.ts`

* 添加 `StartPosUpdateData`

* 添加 `GameOverScore` / `GameOverData`

* 添加 `ClearAnswerQueueData`

### 阶段 2：实现事件处理器

**文件**: `pages/RoomPage.tsx`

添加 case：

* `START_POS_UPDATE` - 更新起始位置显示

* `CLEAR_ANSWER_QUEUE` - 清空抢答队列

* `GAME_OVER` - 显示游戏结束界面

* `PRELOAD_AUDIO` - 预加载音频（调用 audioPlayer）

### 阶段 3：UI 实现

#### 3.1 起始位置控制条（RoomManagePage.tsx）

```tsx
// 添加状态
const [startPosition, setStartPosition] = useState(0);

// 发送更新
const handleStartPositionChange = async (value: number) => {
  setStartPosition(value);
  await wsClient?.sendJson({
    event: GameEventId.START_POS_UPDATE,
    data: { start_position_percent: value }
  });
};

// JSX
<input
  type="range"
  min="0"
  max="80"
  value={startPosition}
  onChange={(e) => handleStartPositionChange(Number(e.target.value))}
  className="range range-primary"
/>
```

#### 3.2 房主手动结束游戏按钮（RoomManagePage.tsx）

```tsx
const [showEndGameConfirm, setShowEndGameConfirm] = useState(false);

const handleEndGame = async () => {
  await wsClient?.sendJson({
    event: GameEventId.GAME_OVER,
    data: { manual: true }
  });
  setShowEndGameConfirm(false);
};

// JSX - 危险操作按钮
<button 
  className="btn btn-error"
  onClick={() => setShowEndGameConfirm(true)}
>
  结束游戏
</button>

// 二次确认弹窗
{showEndGameConfirm && (
  <ConfirmDialog
    title="确认结束游戏"
    message="确定要结束当前游戏吗？此操作不可撤销。"
    onConfirm={handleEndGame}
    onCancel={() => setShowEndGameConfirm(false)}
  />
)}
```

#### 3.3 游戏结束弹窗（components/GameOverModal.tsx）

```tsx
interface GameOverModalProps {
  finalScores: Array<{ player_id: number; username: string; score: number }>;
  onClose: () => void;
  onReturnHome: () => void;
}

export const GameOverModal: React.FC<GameOverModalProps> = ({
  finalScores, onClose, onReturnHome
}) => {
  return (
    <div className="modal modal-open">
      <div className="modal-box max-w-2xl">
        <h2 className="text-2xl font-bold text-center mb-6">🎉 游戏结束</h2>
        <div className="space-y-2">
          {finalScores.map((player, index) => (
            <div key={player.player_id} 
                 className={`flex justify-between p-4 rounded-lg ${
                   index === 0 ? "bg-yellow-100" :
                   index === 1 ? "bg-gray-100" :
                   index === 2 ? "bg-orange-100" : "bg-base-200"
                 }`}>
              <div className="flex items-center gap-3">
                <span className="text-2xl">
                  {index === 0 ? "🥇" : index === 1 ? "🥈" : index === 2 ? "🥉" : index + 1}
                </span>
                <span>{player.username}</span>
              </div>
              <span className="font-bold">{player.score} 分</span>
            </div>
          ))}
        </div>
        <div className="modal-action">
          <button className="btn btn-primary" onClick={onReturnHome}>返回首页</button>
          <button className="btn" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
};
```

### 阶段 4：更新 gameStore

**文件**: `stores/gameStore.ts`

添加状态：

* `startPosition: number`

* `isGameOver: boolean`

* `finalScores: GameOverScore[]`

添加 actions：

* `setStartPosition()`

* `setGameOver()`

* `clearAnswerQueue()`

***

## 四、文件修改清单

### 后端

| 文件                                    | 修改内容                            |
| ------------------------------------- | ------------------------------- |
| `cache/room_state_manager.py`         | **新建** - 核心状态机类                 |
| `schemas/ws_messages/room_schemas.py` | 添加 3 个事件的 schema                |
| `cache/room_cache.py`                 | 添加起始位置读写函数                      |
| `handlers/round_events_3x.py`         | 使用 RoomStateManager 重构          |
| `handlers/connection_lifespan.py`     | 添加 handlers，使用 RoomStateManager |
| `handlers/__init__.py`                | 导入新模块                           |
| `README.md`                           | 更新完成状态                          |

### 前端

| 文件                             | 修改内容                    |
| ------------------------------ | ----------------------- |
| `types/eventTypes.ts`          | 添加 CLEAR\_ANSWER\_QUEUE |
| `types/wsMessages.ts`          | 添加数据类型                  |
| `pages/RoomPage.tsx`           | 添加 4 个事件处理器             |
| `pages/RoomManagePage.tsx`     | 添加起始位置条、结束游戏按钮          |
| `components/GameOverModal.tsx` | **新建** - 游戏结束弹窗         |
| `components/ConfirmDialog.tsx` | **新建** 或复用 - 二次确认弹窗     |
| `stores/gameStore.ts`          | 添加状态和 actions           |
| `README.md`                    | 更新完成状态                  |

***

## 五、实施顺序建议

### 第一阶段：完成基础事件（高优先级）

1. **START\_POS\_UPDATE** - 实现后端和前端的起始位置控制
2. **GAME\_OVER** - 实现游戏结束逻辑和UI
3. **CLEAR\_ANSWER\_QUEUE** - 完成前端事件处理和WebSocket事件注册

### 第二阶段：完成音频预加载（低优先级）

1. **PRELOAD\_AUDIO** - 与现有下载逻辑对接，实现前端预加载处理

### 第三阶段：文档更新

1. **README.md** - 前后端都更新

## 六、未完成任务的详细实现

### 1. START\_POS\_UPDATE (14) - 起始位置控制

#### 后端实现

* **文件**: `cache/room_cache.py`

  * 添加 `set_room_start_position(room_id: str, position: float)` 函数

  * 添加 `get_room_start_position(room_id: str) -> float` 函数

* **文件**: `schemas/ws_messages/room_schemas.py`

  * 添加 `StartPosUpdateData` / `StartPosUpdateMessage`

* **文件**: `handlers/connection_lifespan.py`

  * 添加 `handle_start_pos_update` 处理器

  * 使用 `RoomStateMachine` 更新起始位置

#### 前端实现

* **文件**: `types/eventTypes.ts`

  * 添加 `START_POS_UPDATE: 14`

* **文件**: `types/wsMessages.ts`

  * 添加 `StartPosUpdateData` 接口

* **文件**: `pages/RoomPage.tsx`

  * 添加 `START_POS_UPDATE` 事件处理器

* **文件**: `pages/RoomManagePage.tsx`

  * 添加起始位置控制条UI

### 2. GAME\_OVER (13) - 游戏结束

#### 后端实现

* **文件**: `schemas/ws_messages/room_schemas.py`

  * 添加 `GameOverScore` / `GameOverData` / `GameOverMessage`

* **文件**: `handlers/connection_lifespan.py`

  * 添加 `handle_game_over_manual` 处理器

  * 实现自动触发游戏结束的逻辑（当所有歌曲都已播放完毕）

#### 前端实现

* **文件**: `types/wsMessages.ts`

  * 添加 `GameOverScore` / `GameOverData` 接口

* **文件**: `pages/RoomPage.tsx`

  * 添加 `GAME_OVER` 事件处理器

* **文件**: `components/GameOverModal.tsx`

  * 新建游戏结束弹窗组件

* **文件**: `pages/RoomManagePage.tsx`

  * 添加结束游戏按钮和二次确认弹窗

* **文件**: `stores/gameStore.ts`

  * 添加 `isGameOver`、`finalScores` 等状态和相应的actions

### 3. PRELOAD\_AUDIO (23) - 音频预加载

#### 后端实现

* 确保 `PreloadAudioMessage` 已正确实现

* 与现有 `mq/tasks.py` 中的下载功能对接

#### 前端实现

* **文件**: `pages/RoomPage.tsx`

  * 添加 `PRELOAD_AUDIO` 事件处理器

  * 与现有的 `audioPlayer` 集成

### 4. CLEAR\_ANSWER\_QUEUE (38) - 完成

#### 后端实现

* **文件**: `schemas/ws_messages/room_schemas.py`

  * 添加 `ClearAnswerQueueData` / `ClearAnswerQueueMessage`

* **文件**: `handlers/connection_lifespan.py`

  * 添加 `handle_clear_answer_queue` 处理器

#### 前端实现

* **文件**: `types/eventTypes.ts`

  * 添加 `CLEAR_ANSWER_QUEUE: 38`

* **文件**: `types/wsMessages.ts`

  * 添加 `ClearAnswerQueueData` 接口

* **文件**: `pages/RoomPage.tsx`

  * 添加 `CLEAR_ANSWER_QUEUE` 事件处理器

## 六、注意事项

### 避免重复工作

* ✅ 使用 `mq/tasks.py` 的下载功能

* ✅ 使用 `cache/room_cache.py` 的队列操作

* ✅ 使用 `cache/utils.py` 的 Redis 管理

* ✅ 使用 `schemas/ws_messages/playback_schemas.py` 的 `PreloadAudioMessage`

### 状态一致性

* RoomStateMachine 同时操作 DB 和 Cache

* 使用事务确保一致性

* 广播事件通知所有客户端

### 房主权限检查

* 所有管理操作检查 `client.user.is_owner`

* 非房主返回错误

### 二次确认

* 结束游戏按钮需要二次确认

* 防止误操作

