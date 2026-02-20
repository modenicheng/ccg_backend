# CCG Backend 现有功能文档

本文档仅描述当前代码已经实现的功能与接口，不包含规划功能。

## 项目概览

该服务基于 FastAPI 提供一个 WebSocket 入口，用于接收二进制帧并按事件类型分发处理。当前已实现心跳事件解析与日志输出，
并提供音频帧与心跳帧的二进制编解码能力。

## 运行与监听

- 服务入口：`main.py`
- 监听地址：`0.0.0.0:3200`
- WebSocket 路径：`/ws/`
- CORS：允许所有来源（`allow_origins=["*"]`）

## WebSocket 接口

### 连接与数据流

客户端连接 `/ws/` 后，服务端会接受连接并持续读取二进制数据帧：

- 每次读取：`receive()`
- 判断是不是二进制消息
- 根据首字节解析事件类型：`get_event_type(data)` / 发送到 JSON 事件处理器
- 分发给对应事件处理器：`handlers.handle(event, data)`

### 事件类型

事件类型定义在 `utils/enumerations.py`：

- `OMIT = 0`
- `AUDIO_FRAME = 1`
- `META_DATA = 2`
- `HEARTBEAT = 3`

> 事件类型由帧首字节指定，必须与枚举值一致，否则会抛出 `ValueError` 或枚举转换错误。

## 数据帧协议

### 公共约定

所有帧均包含以下字段：

- 1 字节：事件类型（`EventType`）
- 8 字节：时间戳（`uint64`，毫秒）

### 音频帧 `AudioFrame` **Deprecated** 已弃用

实现位置：`utils/dataframe.py`

二进制格式（网络字节序）：

- 1 字节：事件类型（`EventType.AUDIO_FRAME`）
- 8 字节：时间戳（`uint64`，毫秒）
- 2 字节：采样率（`uint16`）
- 4 字节：采样点数（`uint32`）
- 1 字节：声道数（`uint8`）
- 4 字节：音频数据长度（`uint32`）
- 1 字节：编码类型（`AudioEncoding`）
- N 字节：音频数据

编码类型定义在 `utils/enumerations.py`：

- `UNKNOWN = 0`
- `OPUS = 1`
- `PCM = 2`

相关能力：

- `AudioFrame.dump()`：序列化为二进制
- `AudioFrame.load(data)`：从二进制反序列化
- `AudioFrame.to_dict()`：返回可读的字典表示

注意事项：

- 采样率与采样点数应在 `uint32` 范围内
- 若音频数据为空会记录警告日志

### 心跳帧 `HeartbeatFrame`

实现位置：`utils/dataframe.py`

二进制格式（网络字节序）：

- 1 字节：事件类型（`EventType.HEARTBEAT`）
- 8 字节：时间戳（`uint64`，毫秒）
- 16 字节：UID（随机字符串，用于标识心跳来源）

相关能力：

- `HeartbeatFrame.dump()`：序列化为二进制
- `HeartbeatFrame.load(data)`：从二进制反序列化

## 事件处理

### 心跳事件

实现位置：`handlers/heartbeats.py`

处理逻辑：

- 使用 `HeartbeatFrame.load(data)` 解析心跳帧
- 输出调试日志：`uid` 与 `timestamp`

## 日志

日志模块位于 `utils/logger.py`，提供以下能力：

- Rich 控制台日志输出
- 旋转文件日志（默认路径 `logs/app.log`）
- `init_logging()` 初始化一次性配置
- `get_logger(name)` 获取命名日志器

## 测试

当前包含音频帧编解码测试：`tests/test_audio_frame_encoding.py`

- 验证 `AudioFrame.dump()` 与 `AudioFrame.load()` 的一致性
- 断言字段一致性并输出字典视图

## 目录结构速览

- `main.py`：FastAPI 服务入口与 WebSocket 路由
- `handlers/`：事件处理器
- `utils/`：数据帧、枚举与日志工具
- `tests/`：单元测试
- `logs/`：运行时日志输出目录

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
