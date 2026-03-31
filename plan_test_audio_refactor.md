# Test Audio 重构计划

## 目标
完全复用赛中音频播放链路实现 test_audio（预热 BGM）功能，解决预下载缺失和持久化问题。

## 核心问题
1. **404 错误**：新创建的歌曲没有缓存，`/api/songs/file/{song_id}` 返回 404
2. **预下载缺失**：test_audio 没有复用赛中的 PRELOAD_AUDIO 机制
3. **持久化缺失**：test_audio 只在前端广播，没有持久化到 Redis

## 实现步骤

### 步骤 1：后端 - 添加 PRELOAD_AUDIO 处理器
**文件**：`handlers/audio_events_2x.py`

添加 `handle_preload_audio` 函数，处理 PRELOAD_AUDIO 事件并广播给所有客户端。

```python
@regist(GameEventType.PRELOAD_AUDIO, data_validator=playback_schemas.PreloadAudioMessage)
async def handle_preload_audio(
    data: playback_schemas.PreloadAudioMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
) -> None:
    """Handle PRELOAD_AUDIO event: broadcast to all clients for preloading."""
    logger.info(
        "Received PRELOAD_AUDIO event in room %s: audio_url=%s",
        room_id,
        data.data.audio_url,
    )
    # 广播给房间内所有客户端，包括发送者
    res = await clients.broadcast(room_id, data.model_dump())
    if isinstance(res, Exception):
        logger.error(
            "Exception occurred while broadcasting PRELOAD_AUDIO event in room %s: %s",
            room_id,
            res,
            exc_info=res,
        )
```

### 步骤 2：后端 - 修改 auto-setup-test-audio 端点
**文件**：`router/room.py`

修改 `auto_setup_test_audio` 函数：
1. 创建歌曲后，立即触发后台下载任务
2. 广播 PRELOAD_AUDIO 事件（而不是 SET_TEST_AUDIO）
3. 同时广播 PLAY 事件开始播放

关键改动：
```python
# 在创建歌曲并添加到房间歌单后：
# 1. 触发后台下载任务
from mq import tasks
tasks.download_and_cache_song(mid=song.platform_song_id)

# 2. 广播 PRELOAD_AUDIO 事件
audio_url = f"/api/songs/file/{song.id}"
preload_data = {
    "event": GameEventType.PRELOAD_AUDIO.value,
    "ts": int(time.time() * 1000),
    "data": {"audio_url": audio_url, "progress_ms": 0, "offset_ts": None},
}
await clients.broadcast(room_id, preload_data)

# 3. 广播 PLAY 事件（等待短暂延迟确保预下载开始）
await asyncio.sleep(0.5)  # 等待 500ms
play_data = {
    "event": GameEventType.PLAY.value,
    "ts": int(time.time() * 1000),
    "data": {"audio_url": audio_url, "progress_ms": 0, "offset_ts": None},
}
await clients.broadcast(room_id, play_data)
```

### 步骤 3：后端 - 持久化 test_audio 到 Redis
**文件**：`router/room.py`

在广播 PLAY 事件的同时，调用 `set_room_playback_state` 持久化播放状态到 Redis：

```python
from cache.room_cache import set_room_playback_state
from cache.schemas import PlaybackState

playback_state = PlaybackState(
    audio_url=audio_url,
    progress_ms=0,
    updated_at=int(time.time() * 1000),
    offset_ts=int(time.time() * 1000),
    play_state="playing",
    current_order=0,
)
await set_room_playback_state(room_id, playback_state)
```

### 步骤 4：前端 - 移除独立的 test_audio 代码
**文件**：`src/pages/RoomPage.tsx`

删除内容：
1. `testAudioSongId`、`isTestAudioPlaying`、`isWindowFocused` 状态
2. `playTestAudio`、`stopTestAudio` 函数
3. test_audio 专用的 useEffect（窗口 focus 检测、自动播放控制）
4. SET_TEST_AUDIO 事件监听器
5. 调用 auto-setup-test-audio 的代码（改为后端自动触发）

保留内容：
1. PRELOAD_AUDIO 事件监听器（已存在）
2. PLAY/PAUSE/SEEK 事件处理（已存在）
3. 窗口 focus 检测（如果需要用于其他用途）

### 步骤 5：前端 - 修改 RoomManagePage
**文件**：`src/pages/RoomManagePage.tsx`

修改 `handleSetTestAudio` 函数：
1. 调用 HTTP 端点设置 test_audio（触发预下载和播放）
2. 不再需要手动广播 SET_TEST_AUDIO 事件

### 步骤 6：前端 - 移除 SET_TEST_AUDIO 事件定义
**文件**：`src/types/eventTypes.ts`

删除 `SET_TEST_AUDIO: 63`（不再需要此事件）

### 步骤 7：后端 - 移除 SET_TEST_AUDIO 相关代码
**文件**：
- `utils/enumerations.py`：删除 `SET_TEST_AUDIO = 63`
- `schemas/ws_messages/playback_schemas.py`：删除 `SetTestAudioData` 和 `SetTestAudioMessage`
- `handlers/audio_events_2x.py`：删除 `handle_set_test_audio` 函数

## 验证测试

### 测试场景 1：创建房间
1. 创建新房间
2. 验证：自动触发 test_audio 预下载
3. 验证：自动开始播放 test_audio
4. 验证：所有进入房间的玩家都能听到

### 测试场景 2：切换 test_audio
1. 在 /manage 页面选择新的 test_audio
2. 验证：新歌曲开始预下载
3. 验证：旧歌曲停止播放，新歌曲开始播放
4. 验证：所有客户端同步切换

### 测试场景 3：断线重连
1. 玩家断线后重连
2. 验证：通过 ROOM_STATE 恢复 test_audio 播放状态
3. 验证：如果歌曲未缓存，自动触发预下载

### 测试场景 4：窗口失焦
1. 播放 test_audio 时切换窗口
2. 验证：浏览器自动暂停（无需手动处理）
3. 验证：回到窗口后自动恢复播放

## 时间估算
- 步骤 1-3（后端）：30 分钟
- 步骤 4-6（前端）：30 分钟
- 步骤 7（清理代码）：15 分钟
- 测试验证：30 分钟
- **总计**：约 1.5-2 小时

## 风险与应对
1. **风险**：预下载任务可能失败
   - **应对**：添加错误处理和重试机制
   
2. **风险**：并发广播可能导致竞态条件
   - **应对**：使用 asyncio.gather 确保顺序
   
3. **风险**：前端状态清理不彻底
   - **应对**：仔细审查 RoomPage.tsx 中所有 test_audio 相关代码
