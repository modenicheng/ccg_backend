# 音频错误处理和自动重新下载功能

## 功能概述

当前端预加载或播放音频文件失败时，会向后端发送错误报告。后端接收到错误事件后，会自动执行以下操作：

1. **记录错误信息** - 详细记录错误类型、原因和相关的URL
2. **触发重新下载任务** - 重新下载当前房间正在播放的歌曲音频文件
3. **刷新音频令牌** - 生成新的临时访问令牌
4. **重新广播预加载消息** - 向前端广播新的 PRELOAD_AUDIO 消息，包含新的URL和令牌

## 技术架构

### 前端到后端的通信

前端会发送以下格式的错误事件：

```json
{
  "event": 255,
  "ts": 1710000000000,
  "data": {
    "error_type": "load_failed" | "sync_failed",
    "reason": "错误原因描述",
    "audio_url": "当前尝试的音频URL或unknown"
  }
}
```

### 错误类型说明

- **load_failed**: 前端尝试加载音频文件失败（网络问题、文件不存在等）
- **sync_failed**: 前端与后端的音频状态同步失败

### 文件和代码位置

#### 新增/修改的文件：

1. **schemas/ws_messages/playback_schemas.py**
   - 新增 `AudioErrorData` 类 - 验证前端错误事件的数据结构
   - 新增 `AudioErrorMessage` 类 - 完整的错误事件消息schema

2. **handlers/audio_error_handler.py** （新增）
   - 处理事件ID 255的错误事件
   - 自动触发重新下载和消息重新广播

3. **handlers/__init__.py**
   - 导入新的 `audio_error_handler` 模块，激活事件处理器注册

## 工作流程

```
前端音频加载失败
        ↓
前端发送错误事件 (event: 255)
        ↓
后端接收错误事件
        ↓
验证房间和歌曲信息
        ↓
[并行执行]
├─ 触发 download_and_cache_song 任务队列任务
└─ 调用 broadcast_preload_audio_for_index 重新广播
        ↓
任务队列异步下载音频文件
        ↓
前端接收新的 PRELOAD_AUDIO 消息
        ↓
前端使用新的URL和token重新加载
        ↓
加载成功 ✓
```

## 日志输出示例

处理器会输出详细的日志记录：

```
[AUDIO_ERROR] Room abc123, Client socket_id_xxx reported load_failed error: Network timeout (URL: https://...)
[AUDIO_ERROR] Attempting to re-download song 42 (MID: 001234567) for room abc123
[AUDIO_ERROR] Re-download task triggered for song 42 (MID: 001234567) in room abc123
[AUDIO_ERROR] Re-broadcasted PRELOAD_AUDIO for song 42 in room abc123
```

## 配置和环保

### 环境变量相关

- `CCG_AUDIO_DOWNLOAD_DIR` - 音频文件存储目录
- `CCG_REDIS_URL` - Redis连接URL（用于缓存房间数据）
- `CCG_DATABASE_URL` - 数据库连接URL（用于查询歌曲信息）

### 任务队列相关

- `download_and_cache_song` 任务运行在Huey任务队列中
- 可在`mq/tasks.py`中查看下载配置（重试次数、退避时间等）

## 测试指南

### 单元测试

虽然此功能主要依赖于WebSocket集成，但核心逻辑可以通过以下方式测试：

```bash
# 测试事件处理器
uv run pytest tests/test_audio_error_handler.py -v

# 测试schema验证
uv run pytest tests/test_playback_schemas.py -v
```

### 集成测试

1. **启动后端服务**
   ```bash
   uv run uvicorn main:app --reload --port 8000
   ```

2. **启动任务队列消费者**
   ```bash
   uv run huey_consumer mq.tasks.huey
   ```

3. **连接到房间并触发错误**
   ```javascript
   // 在前端WebSocket连接中模拟错误
   const errorEvent = {
     event: 255,
     ts: Date.now(),
     data: {
       error_type: "load_failed",
       reason: "Test error: simulated network timeout",
       audio_url: "https://example.com/test.mp3"
     }
   };
   ws.send(JSON.stringify(errorEvent));
   ```

4. **观察后端日志验证**
   - 检查是否记录了错误信息
   - 检查是否触发了重新下载任务
   - 检查是否重新广播了 PRELOAD_AUDIO 消息

### 故障排查

**问题：错误事件没有被处理**
- 确保 `audio_error_handler.py` 已被正确导入到 `handlers/__init__.py`
- 检查后端日志是否有导入错误
- 验证EventType.ERROR是否正确定义为255

**问题：重新下载任务没有执行**
- 确保任务队列消费者正在运行
- 检查`CCG_AUDIO_DOWNLOAD_DIR`环境变量是否正确设置
- 验证QQ音乐API凭证是否有效

**问题：PRELOAD_AUDIO消息没有广播**
- 检查Redis连接是否正常
- 验证房间数据是否存在于Redis缓存中
- 检查歌曲是否是QQ平台的歌曲

## 性能考虑

- 错误处理器是异步的，不会阻塞WebSocket连接
- 重新下载任务被分配到后台任务队列处理
- 日志输出包含足够的上下文信息用于调试，但不会过度日志化

## 未来的改进

1. **错误统计** - 收集错误频率数据，识别常见问题
2. **适应性重试** - 根据错误类型调整重试策略
3. **错误通知** - 向前端推送详细的重试状态信息
4. **缓存预热** - 在后台预先缓存下一个待播放的歌曲

## 参考文档

- [前端音频播放控制清理](../../../ccg_frontend/src/pages/RoomPage.tsx)
- [后端任务队列](./mq/tasks.py)
- [音频通用处理](./handlers/audio_common.py)
- [预加载和令牌管理](./db/crud/audio_preload_and_token.py)
