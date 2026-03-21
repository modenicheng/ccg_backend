# QQMusic Cookie 自动轮换实现

## 概述

本实现为 CCG 后端提供了完整的 QQMusic Cookie 自动轮换系统，支持：

- ✅ 多 Cookie 配置管理（单一 → 列表 → 数据库）
- ✅ 轮流轮换策略（Round-Robin）
- ✅ 故障自动转移（失败3次标记失效）
- ✅ 自动刷新机制（基于 `Credential.refresh()`）
- ✅ Redis 实时状态跟踪
- ✅ 数据库审计日志
- ✅ 完整的向后兼容性

## 核心组件

### 1. Cookie 池管理 (`utils/cookie_pool.py`)

**功能**：
- 从多个源加载 Cookies（数据库 > YAML > 环境变量 > 后备）
- 维护 `CookieEntry` 对象，包含凭证和健康状态
- 支持异步刷新和过期检查

**关键类**：
- `CookieEntry`: 单个 Cookie 的元数据（ID、凭证、失败计数、健康状态）
- `CookiePoolManager`: 池管理器（加载、查询、列出 Cookies）

### 2. 轮换管理器 (`cache/cookie_rotation.py`)

**功能**：
- 实现 Round-Robin 轮换策略
- 故障自动转移（标记失效 → 轮换到下一个）
- 维护轮换状态（当前索引、轮换计数、时间戳）

**关键类**：
- `CookieRotationManager`: 轮换管理（获取当前 → 标记失效 → 轮换）
- `RotationState`: 轮换状态数据类
- `RotationStrategy` & `FailurePolicy`:  Enums

### 3. 刷新服务 (`mq/cookie_refresh_service.py`)

**功能**：
- 检查所有 Cookies 的过期状态
- 调用 `Credential.refresh()` 自动更新
- 记录刷新结果到数据库

**关键方法**：
- `check_and_refresh_expired()`: 检查和刷新
- `get_recent_logs()`: 获取刷新历史记录

### 4. 数据库模型扩展 (`db/models.py`)

**新增三张表**：

1. **`cookie_configs`** - 存储 Cookie 配置
   - `cookie_hash`: SHA256 Hash（唯一）
   - `cookie_content`: 完整 Cookie 字符串
   - `source`: 来源标记（yaml/env/manual）
   - `is_active`: 是否活跃

2. **`cookie_rotation_logs`** - 轮换审计日志
   - `from_cookie_id` / `to_cookie_id`: 轮换来源和目标
   - `reason`: 轮换原因（expiry/failure/manual/startup）
   - `context_json`: 额外上下文信息

3. **`cookie_refresh_logs`** - 刷新记录
   - `cookie_id`: 刷新的 Cookie
   - `status`: success / failed
   - `old_expired_at` / `new_expired_at`: 过期时间变更
   - `error_message`: 失败原因

### 5. 配置系统扩展 (`config/settings.py`)

**新增配置项**：
```python
qq_music_cookies: list[str]  # 多个 Cookies
cookie_rotation_enabled: bool  # 是否启用轮换
cookie_rotation_strategy: str  # 轮换策略
cookie_failure_max_retries: int  # 失败重试次数
cookie_refresh_enabled: bool  # 是否启用刷新
cookie_refresh_check_interval: int  # 刷新检查间隔（秒）
```

### 6. 任务集成 (`mq/tasks.py`)

**集成点**：
- `_initialize_cookie_system()`: 启动时初始化 Cookie 系统
- `get_current_cookie_entry()`: 获取当前活跃 Cookie
- `_fetch_songlist_impl()`: 修改以使用轮换的 Cookie，失败时自动转移

## 部署步骤

### 1. 数据库迁移（已完成）

```bash
cd ccg_backend
uv run alembic upgrade head
```

创建了三张新表：`cookie_configs`, `cookie_rotation_logs`, `cookie_refresh_logs`

### 2. 配置 Cookies

**选项 A: 环境变量（单一 Cookie - 现有方式）**
```bash
CCG_QQ_MUSIC_COOKIE="pgv_pvid=...; fqm_pvqid=...; ..."
```

**选项 B: 环境变量（多个 Cookies - JSON 数组）**
```bash
CCG_QQ_MUSIC_COOKIES='["cookie1", "cookie2", "cookie3"]'
```

**选项 C: YAML 配置（推荐 - 更易读）**
```yaml
# config.yaml
ccg:
  qq_music:
    cookie: ""  # 保持为空，使用列表代替
    cookies:
      - "pgv_pvid=...; fqm_pvqid=...; ..."
      - "pgv_pvid=...; fqm_pvqid=...; ..."
      - "pgv_pvid=...; fqm_pvqid=...; ..."
    rotation:
      enabled: true
      strategy: round_robin
      failure_max_retries: 3
    refresh:
      enabled: true
      check_interval_seconds: 1800  # 30分钟
```

### 3. 配置选项

**轮换配置**：
- `CCG_COOKIE_ROTATION_ENABLED`: 启用/禁用轮换（默认 true）
- `CCG_COOKIE_ROTATION_STRATEGY`: 轮换策略（默认 round_robin）
- `CCG_COOKIE_FAILURE_MAX_RETRIES`: 失败多少次后标记不健康（默认 3）

**刷新配置**：
- `CCG_COOKIE_REFRESH_ENABLED`: 启用/禁用自动刷新（默认 true）
- `CCG_COOKIE_REFRESH_CHECK_INTERVAL`: 检查间隔（默认 1800秒 = 30分钟）

### 4. 启动应用

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

系统会在启动时：
1. 从多个源加载 Cookies
2. 初始化 Cookie 池和轮换管理器
3. 设置全局会话凭证为主 Cookie
4. 启动定时刷新任务

## 使用方式

### 1. 自动轮换（无需代码修改）

当 API 调用失败时，系统会自动：
1. 标记当前 Cookie 为失效
2. 检查是否应该轮换（失败3次后自动转移）
3. 轮换到下一个健康的 Cookie
4. 记录审计日志

### 2. 手动调用

```python
# 获取当前活跃 Cookie
current = await get_current_cookie_entry()
print(f"Using cookie: {current.cookie_id}")

# 手动触发轮换
if COOKIE_ROTATION_MANAGER:
    COOKIE_ROTATION_MANAGER.rotate(reason="manual_test")

# 检查和刷新所有 Cookies
if COOKIE_REFRESH_SERVICE:
    result = await COOKIE_REFRESH_SERVICE.check_and_refresh_expired()
    print(f"Refresh result: {result}")
```

### 3. 查询状态

```python
# 获取轮换状态
state = COOKIE_ROTATION_MANAGER.get_state_dict()
print(f"Current rotation: {state}")

# 获取刷新日志
logs = await COOKIE_REFRESH_SERVICE.get_recent_logs()
print(f"Refresh logs: {logs}")

# 获取 Cookie 池状态
pool_status = COOKIE_POOL_MANAGER.to_dict()
print(f"Pool: {pool_status}")
```

## 故障转移流程

```
API 调用失败（例如：KeyError，无效响应）
  ↓
标记当前 Cookie 失效 (failed_count++)
  ↓
失败3次?
  ├─ 是 → 标记为不健康 (is_healthy = False)
  │       轮换到下一个 Cookie
  │       重新尝试 API
  │
  └─ 否 → 返回错误
```

**示例**：
- Cookie A 首次失败 → 记录但继续使用
- Cookie A 第二次失败 → 记录继续
- Cookie A 第三次失败 → 标记失效，自动轮换到 Cookie B
- 记录审计日志（FROM: A, TO: B, reason: "failure"）

## 测试

### 快速测试

```bash
# 测试导入和基本初始化
uv run python test_cookie_system.py
```

输出示例：
```
============================================================
Testing Cookie Rotation System
============================================================

[Test 1] Cookie Pool Status
  - Pool initialized: True
  - Cookies in pool: 2
  - Pool details: {...}

[Test 2] Cookie Rotation Manager
  - Manager initialized: True
  - Current cookie: abc123def456
  - Rotation state: {...}

...
```

### 集成测试

可在现有的歌单获取流程中测试：
1. 配置多个 Cookies
2. 触发 songlist 获取
3. 观察日志中的 Cookie 使用和轮换
4. 检查数据库中的审计日志

## 监控和维护

### Redis 状态监控

```bash
# 查看轮换状态
redis-cli GET "ccg:cookie:rotation:state"

# 查看每个 Cookie 健康状态
redis-cli HGETALL "ccg:cookie:health"
```

### 数据库审计

```sql
-- 查看最近的轮换历史
SELECT * FROM cookie_rotation_logs ORDER BY timestamp DESC LIMIT 10;

-- 查看
 刷新日志
SELECT * FROM cookie_refresh_logs ORDER BY timestamp DESC LIMIT 10;

-- Cookie 失败统计
SELECT
  to_cookie_id,
  COUNT(*) as rotation_count,
  MAX(timestamp) as last_rotation
FROM cookie_rotation_logs
GROUP BY to_cookie_id;
```

### 日志追踪

观察应用日志中的关键事件：
```
[INFO] Loaded 2 cookies into pool
[INFO] Initialized cookie rotation system with primary cookie
[INFO] Using rotated cookie abc123 for fetching songlist 12345
[WARNING] Marked cookie xyz789 as failed: Connection timeout
[INFO] Rotated cookie to abc123 (reason: failure)
```

## 向后兼容性

- 如果没有配置多个 Cookies，系统会降级到单一 Cookie 模式
- 所有 Cookies 失效时，会降级到无凭证会话（当前行为）
- 现有的 `_fetch_songlist_impl()` 调用完全兼容（自动使用轮换）

## 性能考虑

- **Cookie 池加载**：启动时一次性加载，内存占用 < 1MB
- **轮换开销**：无额外开销（仅更新索引指针）
- **刷新任务**：默认每30分钟运行一次，异步非阻塞
- **数据库日志**：审计日志仅在轮换和刷新时写入

## 已知限制与后续优化

### 当前版本限制
- 仅支持 Round-Robin 轮换（后续可加入优先级策略）
- 刷新间隔固定（后述可支持动态调整）
- 不支持从外部 API 动态获取新 Cookie

### 后续优化方向
1. 基于历史成功率的智能优先级调整
2. 从外部 API 动态获取新 Cookie
3. 按 Cookie 来源/地区分组管理
4. 预加热机制（应用启动前预检测）
5. Webhook 通知系统（Cookie 失效时）

## 故障排查

### Cookie 系统未初始化

```
错误: "No cookies loaded from any source"
```

**解决**：确认已配置至少一个 Cookie（env 或 YAML）

### API 调用持续失败

```
错误: "All cookies marked unhealthy"
```

**检查**：
1. 检查 Cookie 有效性：访问 QQ Music 官网验证
2. 查看 `cookie_refresh_logs` 了解刷新失败原因
3. 检查网络连接和代理设置

### 轮换不生效

```
症状: 同一 Cookie 不断重试，未进行轮换
```

**检查**：
1. 确认 `CCG_COOKIE_ROTATION_ENABLED=true`
2. 至少配置 2 个 Cookies
3. 查看应用日志中的轮换日志

## 参考资源

- QQMusic API 凭证文档: https://l-1124.github.io/QQMusicApi/api/utils/credential/
- Alembic 迁移指南: https://alembic.sqlalchemy.org/
- SQLAlchemy 异步支持: https://docs.sqlalchemy.org/asyncio/

## 联系与支持

如发现问题或需要功能扩展，请参考项目文档或联系团队。
