# 实施计划：完成剩余 WebSocket 事件实现

## 任务概述

根据最新反馈调整计划：

### 待实现功能
1. `CLEAR_ANSWER_QUEUE` (38) - 清空抢答队列
2. `START_POS_UPDATE` (14) - 起始位置控制
3. `GAME_OVER` (13) - 游戏结束（支持自动触发和房主手动触发）
4. `PRELOAD_AUDIO` (23) - 音频预加载（需与现有代码对接，避免重复）
5. **新增**：房间状态管理类重构（核心状态机）

### 已移除/暂缓
- ~~游戏开始逻辑~~ - 已实现
- ~~禁止新玩家加入~~ - 已实现

---

## 当前状态分析

### 已存在的代码（避免重复工作）

#### 1. 音频下载和缓存（mq/tasks.py）
- `download_audio_file()` - 下载音频文件
- `download_and_cache_song()` - 下载并缓存单曲
- `_resolve_audio_download_path()` - 解析下载路径
- 音频存储在 `assets/audio/` 目录

#### 2. Redis 房间管理（cache/utils.py）
- `RedisRoomManager` 类已存在
- 方法：`_load_room_state()`, `_save_room_state()`, `get_room_info()`
- 状态模型：`RoomStateCache`（cache/schemas.py）

#### 3. 房间基础状态（cache/schemas.py）
- `RoomBaseStateCache` - 包含 status, song_start_range_percent 等
- `PlaybackState` - 播放状态
- `AnswerQueueItem` - 抢答队列项

#### 4. 抢答队列操作（cache/room_cache.py）
- `clear_answer_queue()` - 清空队列函数已实现
- `get_answer_queue()` - 获取队列
- `append_attempt_answer_player()` - 添加玩家到队列

#### 5. 数据库模型（db/models.py）
- `Room` 模型有 `status` 字段（WAITING, RUNNING, ENDED）
- `ScoreRecord` 模型记录玩家分数
- `User` 模型关联房间和玩家

#### 6. Schema 定义（schemas/ws_messages/）
- `playback_schemas.py` - `PreloadAudioMessage` 已定义
- `room_schemas.py` - 需要添加新的 schema

---

## 一、核心重构：房间状态管理类

### 目标
抽象一个 `RoomStateManager` 类，统一管理房间状态（DB + Cache），作为核心状态机。

### 设计

```python
# cache/room_state_manager.py

class RoomStateManager:
    """房间状态管理器 - 核心状态机
    
    统一管理：
    - 数据库状态（SQLAlchemy）
    - 缓存状态（Redis）
    - WebSocket 广播
    """
    
    def __init__(self, room_id: str, session: AsyncSession, clients: ClientManager):
        self.room_id = room_id
        self.session = session
        self.clients = clients
        self._room: Room | None = None
    
    # ========== 状态查询 ==========
    async def get_room(self) -> Room:
        """获取房间数据库对象（带缓存）"""
        if self._room is None:
            self._room = (await self.session.execute(
                select(Room).where(Room.id == self.room_id)
            )).scalar_one_or_none()
        return self._room
    
    async def get_current_song_index(self) -> int:
        """获取当前歌曲索引"""
        return await room_manager.get_current_song_index(self.room_id)
    
    async def get_total_songs(self) -> int:
        """获取房间歌曲总数"""
        result = await self.session.execute(
            select(func.count()).where(RoomSong.room_id == self.room_id)
        )
        return result.scalar()
    
    # ========== 状态变更 ==========
    async def start_game(self) -> bool:
        """开始游戏"""
        room = await self.get_room()
        if room.status != RoomStatusORM.WAITING:
            return False
        
        room.status = RoomStatusORM.RUNNING
        await self.session.commit()
        
        # 广播 GAME_START
        await self.broadcast_event(GameEventType.GAME_START, {})
        return True
    
    async def end_round(self) -> dict:
        """结束当前回合
        
        Returns:
            {"action": "next_round" | "game_over", "data": {...}}
        """
        current_index = await self.get_current_song_index()
        total = await self.get_total_songs()
        
        # 清空抢答队列
        await self.clear_answer_queue()
        
        # 广播 ROUND_END
        await self.broadcast_event(GameEventType.ROUND_END, {
            "next_round_index": current_index + 1 if current_index < total - 1 else current_index
        })
        
        # 检查是否是最后一首
        if current_index >= total - 1:
            # 游戏结束
            await self.end_game()
            return {"action": "game_over"}
        
        return {"action": "next_round"}
    
    async def end_game(self) -> None:
        """结束游戏"""
        room = await self.get_room()
        room.status = RoomStatusORM.ENDED
        
        # 计算最终分数
        final_scores = await self._calculate_final_scores()
        
        # 广播 GAME_OVER
        await self.broadcast_event(GameEventType.GAME_OVER, {
            "final_scores": final_scores
        })
        
        await self.session.commit()
    
    async def clear_answer_queue(self) -> None:
        """清空抢答队列"""
        await room_cache.clear_answer_queue(self.room_id)
        await self.broadcast_event(GameEventType.CLEAR_ANSWER_QUEUE, {})
    
    async def set_start_position(self, position_percent: int) -> None:
        """设置起始位置"""
        await room_cache.set_room_start_position(self.room_id, position_percent)
        await self.broadcast_event(GameEventType.START_POS_UPDATE, {
            "start_position_percent": position_percent
        })
    
    async def preload_next_song(self) -> dict | None:
        """预加载下一首歌曲
        
        与现有 mq/tasks.py 对接，避免重复实现下载逻辑
        
        Returns:
            {"audio_url": str} | None
        """
        current_index = await self.get_current_song_index()
        total = await self.get_total_songs()
        
        if current_index >= total - 1:
            return None  # 没有下一首
        
        # 获取下一首歌曲
        next_song = (await self.session.execute(
            select(RoomSong, Song)
            .join(Song, RoomSong.song_id == Song.id)
            .where(RoomSong.room_id == self.room_id)
            .where(RoomSong.song_order == current_index + 1)
        )).first()
        
        if not next_song:
            return None
        
        room_song, song = next_song
        
        # 检查是否已缓存
        if song.cached_path:
            audio_url = f"/api/songs/cache/{song.id}"
        else:
            # 触发异步下载任务（复用 mq/tasks.py）
            from mq.tasks import download_and_cache_song
            download_and_cache_song(song.mid)
            # 返回临时 URL 或 None
            audio_url = None
        
        if audio_url:
            # 广播 PRELOAD_AUDIO
            await self.broadcast_event(GameEventType.PRELOAD_AUDIO, {
                "audio_url": audio_url,
                "progress_ms": 0,
                "offset_ts": get_ts_ms()
            })
            return {"audio_url": audio_url}
        
        return None
    
    # ========== 辅助方法 ==========
    async def _calculate_final_scores(self) -> list[dict]:
        """计算最终分数排名"""
        scores = (await self.session.execute(
            select(ScoreRecord).where(ScoreRecord.room_id == self.room_id)
        )).scalars().all()
        
        players = (await self.session.execute(
            select(User).where(User.room_id == self.room_id)
        )).scalars().all()
        
        player_scores = {}
        for score in scores:
            player_scores[score.user_id] = player_scores.get(score.user_id, 0) + score.score
        
        final_scores = [
            {
                "player_id": player.id,
                "username": player.username,
                "score": player_scores.get(player.id, 0)
            }
            for player in players
        ]
        
        final_scores.sort(key=lambda x: x["score"], reverse=True)
        return final_scores
    
    async def broadcast_event(self, event_type: GameEventType, data: dict) -> None:
        """广播事件给所有客户端"""
        message = {
            "event": event_type.value,
            "ts": get_ts_ms(),
            "data": data
        }
        await self.clients.broadcast(self.room_id, message)
```

### 使用示例

```python
# handlers/round_events_3x.py

@regist(GameEventType.ROUND_END, data_validator=RoundEndMessage)
async def handle_round_end(
    data: RoundEndMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
) -> None:
    if not client.user.is_owner:
        await client.send_error(...)
        return
    
    async with AsyncSessionLocal() as session:
        manager = RoomStateManager(room_id, session, clients)
        result = await manager.end_round()
        
        if result["action"] == "next_round":
            # 可以在这里触发预加载
            await manager.preload_next_song()
```

---

## 二、后端实施步骤

### 阶段 1：创建 RoomStateManager

**文件**: `cache/room_state_manager.py`

- 实现上述 RoomStateManager 类
- 集成现有 `mq/tasks.py` 的下载功能
- 集成现有 `cache/room_cache.py` 的缓存操作
- 集成现有 `cache/utils.py` 的 RedisRoomManager

### 阶段 2：创建 Schema 定义

**文件**: `schemas/ws_messages/room_schemas.py`

添加：
- `StartPosUpdateData` / `StartPosUpdateMessage`
- `GameOverScore` / `GameOverData` / `GameOverMessage`
- `ClearAnswerQueueData` / `ClearAnswerQueueMessage`

### 阶段 3：重构现有 Handlers

使用 RoomStateManager 重构：

1. **`handlers/round_events_3x.py`**
   - `handle_game_start` - 使用 manager.start_game()
   - `handle_round_end` - 使用 manager.end_round()

2. **`handlers/connection_lifespan.py`**
   - `handle_start_pos_update` - 使用 manager.set_start_position()
   - `handle_clear_answer_queue` - 使用 manager.clear_answer_queue()
   - 新增 `handle_game_over_manual` - 房主手动结束游戏

### 阶段 4：添加缓存函数

**文件**: `cache/room_cache.py`

添加：
- `set_room_start_position()`
- `get_room_start_position()`

---

## 三、前端实施步骤

### 阶段 1：更新类型定义

**文件**: `types/eventTypes.ts`
- 添加 `CLEAR_ANSWER_QUEUE: 38`

**文件**: `types/wsMessages.ts`
- 添加 `StartPosUpdateData`
- 添加 `GameOverScore` / `GameOverData`
- 添加 `ClearAnswerQueueData`

### 阶段 2：实现事件处理器

**文件**: `pages/RoomPage.tsx`

添加 case：
- `START_POS_UPDATE` - 更新起始位置显示
- `CLEAR_ANSWER_QUEUE` - 清空抢答队列
- `GAME_OVER` - 显示游戏结束界面
- `PRELOAD_AUDIO` - 预加载音频（调用 audioPlayer）

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
- `startPosition: number`
- `isGameOver: boolean`
- `finalScores: GameOverScore[]`

添加 actions：
- `setStartPosition()`
- `setGameOver()`
- `clearAnswerQueue()`

---

## 四、文件修改清单

### 后端

| 文件 | 修改内容 |
|------|----------|
| `cache/room_state_manager.py` | **新建** - 核心状态机类 |
| `schemas/ws_messages/room_schemas.py` | 添加 3 个事件的 schema |
| `cache/room_cache.py` | 添加起始位置读写函数 |
| `handlers/round_events_3x.py` | 使用 RoomStateManager 重构 |
| `handlers/connection_lifespan.py` | 添加 handlers，使用 RoomStateManager |
| `handlers/__init__.py` | 导入新模块 |
| `README.md` | 更新完成状态 |

### 前端

| 文件 | 修改内容 |
|------|----------|
| `types/eventTypes.ts` | 添加 CLEAR_ANSWER_QUEUE |
| `types/wsMessages.ts` | 添加数据类型 |
| `pages/RoomPage.tsx` | 添加 4 个事件处理器 |
| `pages/RoomManagePage.tsx` | 添加起始位置条、结束游戏按钮 |
| `components/GameOverModal.tsx` | **新建** - 游戏结束弹窗 |
| `components/ConfirmDialog.tsx` | **新建** 或复用 - 二次确认弹窗 |
| `stores/gameStore.ts` | 添加状态和 actions |
| `README.md` | 更新完成状态 |

---

## 五、实施顺序建议

### 第一阶段：核心重构（高优先级）
1. **RoomStateManager 类** - 先实现核心状态机
2. **Schema 定义** - 前后端同时进行
3. **测试 RoomStateManager** - 确保重构不破坏现有功能

### 第二阶段：基础事件（高优先级）
4. **CLEAR_ANSWER_QUEUE** - 最简单，先实现
5. **START_POS_UPDATE** - 基础功能
6. **前端对应 UI** - 控制条

### 第三阶段：游戏流程（中优先级）
7. **GAME_OVER** - 自动触发逻辑
8. **房主手动结束** - 二次确认
9. **前端游戏结束弹窗**

### 第四阶段：音频预加载（低优先级）
10. **PRELOAD_AUDIO** - 与现有下载逻辑对接
11. **前端预加载处理**

### 第五阶段：文档更新
12. **README.md** - 前后端都更新

---

## 六、注意事项

### 避免重复工作
- ✅ 使用 `mq/tasks.py` 的下载功能
- ✅ 使用 `cache/room_cache.py` 的队列操作
- ✅ 使用 `cache/utils.py` 的 Redis 管理
- ✅ 使用 `schemas/ws_messages/playback_schemas.py` 的 `PreloadAudioMessage`

### 状态一致性
- RoomStateManager 同时操作 DB 和 Cache
- 使用事务确保一致性
- 广播事件通知所有客户端

### 房主权限检查
- 所有管理操作检查 `client.user.is_owner`
- 非房主返回错误

### 二次确认
- 结束游戏按钮需要二次确认
- 防止误操作
