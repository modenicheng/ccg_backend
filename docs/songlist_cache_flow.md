# 歌单入库与首曲缓存流程说明

本文档说明「从歌单 ID 到入库，再到缓存第一首曲目」的完整链路实现、配置项、设计思路与排障建议。

> 适用入口：`examples/songlist_to_cache_flow.py`

## 快速开始（30 秒）

1. 在 `.env` 中确认至少包含以下项：
   - `CCG_DATABASE_URL`
   - `CCG_QQ_MUSIC_COOKIE`（建议填写，避免返回空歌单）
   - `CCG_AUDIO_DOWNLOAD_DIR`（可选，默认 `assets/audio`）
2. 在项目根目录执行：`uv run .\examples\songlist_to_cache_flow.py`
3. 看到 `✅ End-to-end flow completed` 即表示跑通。

如果需要改歌单 ID，可编辑脚本末尾：`asyncio.run(run(84621365))`。

---

## 1. 目标与范围

目标：给定 QQ 歌单 ID，自动完成以下步骤：

1. 拉取歌单详情（含分页歌曲）
2. 将歌单与歌曲写入数据库
3. 选择第一首歌曲（`platform_song_id` 作为 `mid`）
4. 下载音频文件到本地缓存目录
5. 回写 `songs.cached_path`
6. 验证数据库与磁盘文件一致性

当前默认示例 ID：`84621365`。

---

## 2. 关键入口与函数职责

### 2.1 示例脚本（端到端）

文件：`examples/songlist_to_cache_flow.py`

主流程：

- `run(songlist_id=84621365)`
- 调用 `_fetch_songlist_impl(songlist_id)` 完成「拉取 + 入库」
- 取第一首 `mid` 后调用 `_download_and_cache_song_impl(mid)`
- 回查数据库 `songs.cached_path` 并检查文件是否存在

### 2.2 任务模块

文件：`mq/tasks.py`

核心函数：

- `_fetch_songlist_impl(songlist_id)`：歌单抓取与数据库入库实现
- `_download_and_cache_song_impl(mid, save_path=None)`：下载并回写缓存路径
- `_download_audio_file_impl(url, save_path=None, mid=None)`：真实下载实现
- `_get_song_url(mid, filetype=OGG_320)`：获取歌曲下载 URL
- `_with_retry(...)`：统一异步重试与退避

Huey 包装函数（用于任务队列场景）：

- `fetch_songlist(...)`
- `download_and_cache_song(...)`
- `download_audio_file(...)`

这些包装函数内部委托到 `*_impl`，避免“任务内再调任务”导致语义混乱。

### 2.3 数据写入层

文件：`db/crud.py`

- `create_or_update_songlist(...)`
- `create_or_update_songs(...)`
- `update_song_cached_path(...)`

注意：`create_or_update_songlist` 对 `platform_songlist_id` 做了字符串归一化，避免 PostgreSQL 下 `varchar = integer` 的类型比较错误。

---

## 3. 配置项

建议在 `.env` 中配置（`.env.template` 已提供模板）：

- `CCG_QQ_MUSIC_COOKIE`：QQ 音乐 Cookie（部分歌单/歌曲需要）
- `CCG_SONGLIST_FETCH_CONCURRENCY`：分页抓取并发上限
- `CCG_SONGLIST_FETCH_RETRIES`：歌单抓取重试次数
- `CCG_SONGLIST_FETCH_BACKOFF_SECONDS`：歌单抓取退避基数
- `CCG_AUDIO_DOWNLOAD_RETRIES`：下载重试次数
- `CCG_AUDIO_DOWNLOAD_BACKOFF_SECONDS`：下载退避基数
- `CCG_SONG_URL_RETRIES`：歌曲 URL 获取重试次数
- `CCG_SONG_URL_BACKOFF_SECONDS`：歌曲 URL 获取退避基数
- `CCG_AUDIO_DOWNLOAD_DIR`：默认下载目录（默认 `assets/audio`）

默认下载路径规则（未传 `save_path` 时）：

- `assets/audio/<mid>.<ext>`

其中 `<ext>` 优先从 URL 推断，推断失败回退为 `.ogg`。

---

## 4. 设计思路（为什么这么设计）

### 4.1 实现层与任务层分离

**做法**：将业务实现放在 `*_impl`，Huey 任务函数仅做包装调用。

**原因**：

- 业务代码可在脚本、测试、服务内直接复用
- 避免任务函数彼此调用时出现“入队 vs 立即执行”语义冲突
- 调试更直接（可以 `await _xxx_impl`）

**优势**：

- 可测试性更高
- 扩展任务编排更灵活
- 线上行为与本地验证一致性更好

### 4.2 重试策略统一收敛

**做法**：用 `_with_retry` 包装歌单抓取、URL 获取、音频下载。

**原因**：

- 外部 API 与网络请求存在瞬时失败
- 统一重试策略比“散落 try/except”更可控

**优势**：

- 降低偶发失败率
- 日志格式一致，排障更快
- 参数可配置，便于不同环境调优

### 4.3 路径策略配置化

**做法**：通过 `CCG_AUDIO_DOWNLOAD_DIR` 控制缓存目录。

**原因**：

- 本地开发、容器部署、生产环境目录结构不同
- 不应硬编码路径

**优势**：

- 部署迁移成本低
- 支持挂载卷、NAS 或对象存储落地前置目录

### 4.4 缓存回写最小更新

**做法**：`update_song_cached_path` 只更新 `cached_path` 字段，不覆盖其他元数据。

**原因**：

- 下载过程是“后置增强”，不应污染歌曲基础信息

**优势**：

- 降低误更新风险
- 事务边界清晰

---

## 5. 执行方式

在项目根目录执行：

- `uv run .\examples\songlist_to_cache_flow.py`

成功后将输出：

- `songlist_id`
- `songlist_db_id`
- `first_mid`
- `cached_path`

并确保：

- `songs.cached_path` 已更新
- 本地文件存在

---

## 6. 常见问题与排障

### 6.1 `ModuleNotFoundError: No module named 'db'`

已在示例脚本中加入项目根路径注入。若仍出现，请确认运行位置是项目根目录。

### 6.2 歌单入库成功但歌曲为空

常见原因：

- `CCG_QQ_MUSIC_COOKIE` 为空或已过期
- 歌单权限/可见性限制

建议：

- 更新 Cookie 后重试
- 更换公开歌单 ID 验证链路

### 6.3 PostgreSQL 类型比较报错（`varchar = integer`）

已在 CRUD 层归一化 `platform_songlist_id` 为字符串。若历史代码分支仍报错，请同步最新实现。

### 6.4 下载失败或文件为空

排查顺序：

1. 检查 `CCG_SONG_URL_*` 与 `CCG_AUDIO_DOWNLOAD_*` 重试参数
2. 检查目标目录写权限
3. 查看 `mq/tasks.py` 日志（已包含堆栈 `exc_info=True`）

---

## 7. 当前方案优势总结

- 端到端链路可脚本化回归
- 任务队列与直接执行两种模式并存
- 重试、并发、路径均可配置
- 日志结构统一，故障可观测性更好
- 数据库更新粒度小，风险可控
