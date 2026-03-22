# Cookie 刷新系统 - 需求澄清与实现说明

## 我的错误理解

我最初误解了需求，逐步演变为实现一个完整的**多 Cookie 轮换系统**，包括：

- Cookie 池管理
- 轮换策略（Round-Robin）
- 故障转移机制
- 多源加载优先级...

但这完全不是你需要的！

## 真正的需求

你需要的很简单：

### **单 Cookie 的自动刷新**

```python
# 利用 qqmusic_api 提供的 API
credential = qapi.Credential.from_cookies_dict(cookies)

# 检查是否过期
is_expired = await credential.is_expired()

# 刷新凭证
success = await credential.refresh()
```

参考：<https://l-1124.github.io/QQMusicApi/api/utils/credential/#utils.credential.Credential.refresh>

## 现在的正确实现

### 1. **CredentialRefreshService** (`cache/credential_refresh.py`)

```python
class CredentialRefreshService:
    """定期刷新 credential 的服务"""

    async def check_and_refresh_all(self, credentials):
        """
        检查所有 credentials 是否过期
        过期的则调用 credential.refresh() 来刷新
        """
        for cred in credentials:
            is_expired = await cred.is_expired()
            if is_expired:
                can_refresh = await cred.can_refresh()
                if can_refresh:
                    success = await cred.refresh()
                    # 记录到数据库
                    log_refresh_event(...)
```

### 2. **定期任务** (`mq/tasks.py`)

```python
@huey.periodic_task(crontab(minute='*/60'))
def refresh_credentials_periodic():
    """每小时检查一次并刷新过期的凭证"""
    if COOKIE_REFRESH_SERVICE:
        result = COOKIE_REFRESH_SERVICE.check_and_refresh_all(credentials)
```

### 3. **工作流程**

```
环境变量 cookie
    ↓
加载到内存 pool
    ↓
持久化到 CookieConfig 表
    ↓
定期任务 (每小时)
    ↓
检查 credential.is_expired()
    ↓
如果过期 → credential.refresh()
    ↓
记录结果到 CookieRefreshLog 表
```

## 关键代码片段

### credential_refresh.py

```python
async def check_and_refresh_all(self, credentials):
    for cred in credentials:
        is_expired = await cred.is_expired()  # ← 检查
        if is_expired:
            can_refresh = await cred.can_refresh()
            if can_refresh:
                success = await cred.refresh()  # ← 刷新！
                if success:
                    # 记录成功
                    await log_refresh_event(db_session, "success", ...)
```

### Huey 定期任务

```python
@huey.periodic_task(crontab(minute='*/60'))
def refresh_credentials_periodic():
    result = COOKIE_REFRESH_SERVICE.check_and_refresh_all(credentials)
    # result = {
    #     "checked_count": 1,
    #     "refreshed_count": 1,
    #     "failed_count": 0
    # }
```

## 文件清单

### 新创建的文件

- ✅ `cache/credential_refresh.py` - 刷新服务实现

### 修改的文件

- ✅ `mq/tasks.py` - 添加定期刷新任务
- ✅ `db/crud/cookie_crud.py` - 记录刷新日志

## 为什么之前的实现是错的

1. **过度设计** - 实现了多 cookie 轮换系统，但你只需要单 cookie 定期刷新
2. **复杂性过高** - 添加了不必要的 CookiePoolManager、CookieRotationManager 等
3. **误解了架构** - 假设需要"故障转移"和"轮换"机制，实际上只需要"刷新"机制

## 正确的架构优势

✅ **简单**：直接调用 `credential.refresh()`
✅ **标准**：使用 qqmusic_api 官方提供的方法
✅ **灵活**：可以支持任何数量的 credentials（1个或多个）
✅ **可靠**：定期自动刷新，记录所有操作日志

## 使用示例

### 单 Cookie 场景

```bash
export CCG_QQ_MUSIC_COOKIE="uin=123; token=abc..."

# Huey 消费者会自动：
# 1. 加载这个 cookie
# 2. 持久化到数据库
# 3. 每小时检查是否过期
# 4. 过期后自动刷新
# 5. 记录每次刷新的结果
```

### 多 Cookie 场景（未来）

只需将多个 cookies 放入 credentials 列表，使用相同的刷新逻辑即可。

## 总结

我之前走错了方向，非常抱歉。现在的实现才是正确的：

- **单文件服务**：`CredentialRefreshService`
- **单定期任务**：`refresh_credentials_periodic()`
- **标准 API**：使用 `credential.refresh()`
- **简洁高效**：专注于什么是必须的
