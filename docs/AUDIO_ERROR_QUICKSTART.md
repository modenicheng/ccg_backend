# 音频错误处理功能 - 快速启动指南

## ⚡ 5分钟快速开始

### 1. 前置检查

在使用本功能前，确保已完成：

- ✅ 前端音频播放控制清理已完成
- ✅ 后端环境已配置
- ✅ QQ音乐API凭证已设置

### 2. 启动后端服务

```bash
# 终端1: 启动主应用
cd ccg_backend
uv run uvicorn main:app --reload --port 8000

# 终端2: 启动任务队列消费者
cd ccg_backend
uv run huey_consumer mq.tasks.huey --workers=4
```

### 3. 前端会自动集成

前端代码已包含 `reportAudioError` 函数，当以下情况发生时会自动调用：

- 音频加载失败（3次重试后）
- 音频元素出错
- 前后端状态不同步

**无需前端额外配置，自动生效！**

### 4. 验证功能工作

打开后端日志，观察以下启动消息：

```
✓ WebSocket connected
✓ Event handler registered: EventType.ERROR (255)
✓ audio_error_handler module loaded
```

## 🧪 测试功能

### 方法1: 模拟前端错误事件

使用WebSocket客户端（如 curl 的WebSocket支持或在线工具）发送：

```json
{
  "event": 255,
  "ts": 1710000000000,
  "data": {
    "error_type": "load_failed",
    "reason": "Simulated: Network timeout during preload",
    "audio_url": "https://example.com/song.mp3"
  }
}
```

### 方法2: 人工触发测试

运行测试套件：

```bash
uv run pytest tests/test_audio_error_handler.py -v
```

### 方法3: 集成测试

1. 在浏览器中打开游戏界面
2. 加入一个房间
3. 通过浏览器DevTools（Network→Throttle）模拟网络问题
4. 观察前端是否报告错误
5. 检查后端日志中的 `[AUDIO_ERROR]` 消息

## 📊 日志解释

| 日志模式 | 含义 | 处理 |
|--------|------|------|
| `[AUDIO_ERROR] ... reported load_failed` | 前端音频加载失败 | 后端重新下载 |
| `[AUDIO_ERROR] Attempting to re-download` | 后端开始重下载 | 正常处理中 |
| `[AUDIO_ERROR] Re-download task triggered` | 下载任务已提交 | 后台下载运行中 |
| `[AUDIO_ERROR] Re-broadcasted PRELOAD_AUDIO` | 消息已重新广播 | 等待前端重试 |
| `[AUDIO_ERROR] does not support re-download` | 非QQ歌曲 | 无法重新下载，记录错误 |

## 🐛 常见问题排查

### Q: 事件没有被处理

**检查清单：**
```bash
# 1. 检查handlers/__init__.py中是否导入了audio_error_handler
grep "audio_error_handler" ccg_backend/handlers/__init__.py

# 2. 查看后端日志是否有导入错误
grep "audio_error_handler" logs/app.log
```

### Q: 看不到[AUDIO_ERROR]日志

**检查清单：**
```bash
# 1. 验证事件格式是否正确 (event: 255)
# 2. 验证WebSocket连接状态是否正常
# 3. 检查后端日志级别是否设置为DEBUG
export CCG_LOG_LEVEL=DEBUG
```

### Q: 重新下载任务未执行

**检查清单：**
```bash
# 1. 确保huey消费者正在运行
ps aux | grep huey

# 2. 检查QQ音乐API凭证是否有效
echo $CCG_QQ_MUSIC_COOKIE

# 3. 检查音频下载目录权限
ls -la $CCG_AUDIO_DOWNLOAD_DIR
```

### Q: PRELOAD_AUDIO消息未广播

**检查清单：**
```bash
# 1. 验证Redis连接
redis-cli ping

# 2. 检查房间数据是否在缓存中
redis-cli KEYS "room:*"
```

## 📈 性能监控

### 关键指标

- **事件处理时间**: 应 < 100ms（包含日志）
- **重新下载时间**: 取决于网络和文件大小
- **消息广播时间**: 应 < 50ms
- **内存占用**: 每个事件 < 1KB

### 常用命令

```bash
# 查看最近的错误事件数
grep "[AUDIO_ERROR]" logs/app.log | wc -l

# 查看错误统计
grep "[AUDIO_ERROR]" logs/app.log | grep -o "error_type: [^,]*" | sort | uniq -c

# 查看最常见的错误原因
grep "[AUDIO_ERROR]" logs/app.log | tail -100 | grep -o "reason: [^,]*" | sort | uniq -c
```

## 🔄 工作流程快速参考

```
前端加载失败（3次重试）
        ↓
reportAudioError("load_failed", reason)
        ↓
WebSocket发送 event: 255
        ↓
后端接收并验证AudioErrorMessage
        ↓
查询Song表获取MID
        ↓
触发download_and_cache_song任务
        ↓
broadcast_preload_audio_for_index
        ↓
发送新PRELOAD_AUDIO消息给前端
        ↓
前端收到新URL和token
        ↓
前端使用新URL重试加载
        ↓
✓ 成功或记录最终失败
```

## 📖 更多资源

- **完整文档**: [audio_error_handling.md](./docs/audio_error_handling.md)
- **实现总结**: [AUDIO_ERROR_IMPLEMENTATION.md](./AUDIO_ERROR_IMPLEMENTATION.md)
- **测试用例**: [tests/test_audio_error_handler.py](./tests/test_audio_error_handler.py)
- **错误处理器**: [handlers/audio_error_handler.py](./handlers/audio_error_handler.py)
- **创建的Schema**: [schemas/ws_messages/playback_schemas.py](./schemas/ws_messages/playback_schemas.py)

## 💡 其他建议

### 开发时启用详细日志

```bash
export CCG_LOG_LEVEL=DEBUG
export PYTHONASYNCDEBUG=1
```

### 监控音频错误趋势

建议定期检查日志统计：

```bash
# 每小时查看一次错误统计
watch -n 3600 'grep "[AUDIO_ERROR]" logs/app.log | tail -50'
```

### 集成监控系统

考虑将 `[AUDIO_ERROR]` 日志集成到：
- ELK Stack (Elasticsearch, Logstash, Kibana)
- Splunk
- DataDog
- 自定义监控仪表板

## ✅ 验证清单

部署前确保：

- [ ] 后端成功启动
- [ ] 任务队列消费者正在运行
- [ ] 前端正确连接到后端
- [ ] 可以看到WebSocket心跳消息
- [ ] QQ音乐API凭证有效
- [ ] 日志中显示模块已加载
- [ ] 运行了集成测试并通过

## 🎉 完成!

功能已完全集成！现在可以：

1. ✅ 前端自动报告音频错误
2. ✅ 后端自动重新下载文件
3. ✅ 重新广播消息给所有客户端
4. ✅ 无需用户干预的自动恢复

---

**问题?** 查看完整文档或检查日志中的 `[AUDIO_ERROR]` 前缀消息。

**反馈?** 在AUDIO_ERROR_IMPLEMENTATION.md中记录改进建议。
