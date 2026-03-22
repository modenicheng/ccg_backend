# Credential刷新 - 故障排查指南

## 快速检查清单

### 1. 确认定时任务是否正常执行

```bash
# 查看Huey消费者日志
tail -f logs/huey.log

# 应该看到每30分钟一次的刷新任务启动日志
# 2026-03-22 10:30:00 - huey - INFO - Starting periodic credential refresh task
```

### 2. 检查最近的刷新事件

```sql
-- 在数据库中查询最近的刷新日志
SELECT * FROM cookie_refresh_logs
ORDER BY timestamp DESC
LIMIT 20;

-- 统计成功/失败比例
SELECT status, COUNT(*) as count
FROM cookie_refresh_logs
WHERE timestamp > datetime('now', '-1 day')
GROUP BY status;
```

### 3. 查看当前credentials状态

```python
# 在Python交互式终端查看
from cache.credential_refresh import COOKIE_POOL_MANAGER

if COOKIE_POOL_MANAGER:
    for cookie_id, entry in COOKIE_POOL_MANAGER.pool.items():
        print(f"{cookie_id}: healthy={entry.health}")
```

---

## 常见问题排查

### 问题1: 日志中没看到 "Starting periodic credential refresh task"

**原因诊断：**
- [ ] Huey消费者未启动
- [ ] 定时任务未被正确注册
- [ ] Redis连接失败

**解决步骤：**

```bash
# 1. 检查Huey消费者是否运行
ps aux | grep huey_consumer

# 2. 如果未运行，启动它
uv run huey_consumer.py mq.tasks.huey

# 3. 检查Redis连接
redis-cli ping
# 应该返回 PONG

# 4. 查看Huey日志获取更多信息
uv run huey_consumer.py mq.tasks.huey 2>&1 | head -50
```

---

### 问题2: 所有credentials都显示无法刷新

**原因诊断：**
- [ ] refresh_token已过期无法恢复
- [ ] QQMusic API返回错误
- [ ] 网络连接问题

**解决步骤：**

```bash
# 1. 查看详细日志
grep "cannot be refreshed\|✗ Failed\|Error refreshing" logs/app.log

# 2. 检查refresh_token的情况
python -c "
from cache.credential_refresh import COOKIE_POOL_MANAGER
for cid, entry in COOKIE_POOL_MANAGER.pool.items():
    cred = entry.credential
    print(f'{cid}:')
    print(f'  has_refresh_token: {bool(cred.refresh_token)}')
    print(f'  is_expired: {await cred.is_expired()}')
    print(f'  can_refresh: {await cred.can_refresh()}')
"

# 3. 如果refresh_token确实过期，需要手动更新
# 从QQMusic重新获取新的token并更新到数据库
```

---

### 问题3: 刷新成功但credentials仍然被标记为不健康

**原因诊断：**
- [ ] entry.reset_health() 未被调用
- [ ] credentials在刷新后又立即出错

**解决步骤：**

```bash
# 1. 检查最近的刷新日志中是否有 "✓ Refreshed"
grep "✓ Refreshed" logs/app.log | tail -5

# 2. 如果成功了但仍不健康，检查之后是否又有错误
grep -A2 "✓ Refreshed" logs/app.log | grep -i "error"

# 3. 手动重置健康状态（临时措施）
python << 'EOF'
from cache.credential_refresh import COOKIE_POOL_MANAGER
for cid, entry in COOKIE_POOL_MANAGER.pool.items():
    if entry.health < 0:
        entry.reset_health()
        print(f"Reset health for {cid}")
EOF
```

---

### 问题4: 数据库中没有refresh_log记录

**原因诊断：**
- [ ] 数据库连接失败
- [ ] CookieRefreshLog表不存在
- [ ] 日志记录被异常中断

**解决步骤：**

```bash
# 1. 检查表是否存在
sqlite3 ccg.db ".tables" | grep cookie_refresh

# 2. 如果表不存在，运行数据库迁移
uv run alembic upgrade head

# 3. 查看日志中是否有数据库相关错误
grep "log_refresh_event\|database\|session" logs/app.log | grep -i error

# 4. 手动插入一条测试记录
sqlite3 ccg.db "
INSERT INTO cookie_refresh_logs
(timestamp, cookie_id, status, old_expired_at, new_expired_at)
VALUES (datetime('now'), 'test_cookie', 'success', 1000000, 2000000);
"
```

---

### 问题5: Redis中credentials被反复刷新

**原因诊断：**
- [ ] 刷新成功但新的expired_at仍在过期范围内（自动重试）
- [ ] 系统循环刷新

**解决步骤：**

```bash
# 1. 检查refresh_time间隔
python << 'EOF'
from cache.credential_refresh import COOKIE_POOL_MANAGER
import asyncio

async def check():
    for cid, entry in COOKIE_POOL_MANAGER.pool.items():
        cred = entry.credential
        is_exp = await cred.is_expired()
        print(f"{cid}: expired={is_exp}, expired_at={cred.expired_at}")

asyncio.run(check())
EOF

# 2. 如果expired_at已经是未来时间但仍显示已过期，
#    可能是时间戳单位问题（秒 vs 毫秒）
# 3. 检查credential.py中的时间戳处理逻辑
```

---

## 性能调优

### 降低CPU使用率

```python
# 增加刷新间隔（从30分钟改为1小时）
# 在 mq/tasks.py 中修改
@huey.periodic_task(crontab(minute='0'))  # 每小时执行
def refresh_credentials_periodic(): ...
```

### 降低API调用频次

```python
# 在刷新前检查pool大小
async def _refresh_credentials_impl():
    if len(COOKIE_POOL_MANAGER.pool) > 100:
        logger.warning("Too many credentials, skipping refresh this round")
        return
```

### 批量处理优化

```python
# 使用asyncio.gather并发检查多个credentials
import asyncio

async def _refresh_credentials_impl():
    # 并发检查所有credentials的过期状态
    check_tasks = [
        entry.credential.is_expired()
        for entry in COOKIE_POOL_MANAGER.pool.values()
    ]
    results = await asyncio.gather(*check_tasks)

    # 然后逐个刷新过期的credentials
    for (cid, entry), is_expired in zip(COOKIE_POOL_MANAGER.pool.items(), results):
        if is_expired:
            # ... refresh logic
```

---

## 监控告警建议

### 需要告警的情况

```
1. 刷新失败率 > 20% (可能表示API问题)
2. 连续3次刷新任务未执行 (可能Huey崩溃)
3. 所有credentials都无法刷新 (可能需要重新授权)
4. 单个credential刷新超过10秒 (性能问题)
```

### 监控查询

```sql
-- 每小时刷新失败率
SELECT
  strftime('%Y-%m-%d %H:00', timestamp) as hour,
  COUNT(*) as total,
  SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed,
  ROUND(100.0 * SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) / COUNT(*), 2) as fail_rate
FROM cookie_refresh_logs
WHERE timestamp > datetime('now', '-7 days')
GROUP BY strftime('%Y-%m-%d %H:00', timestamp)
ORDER BY hour DESC;

-- 识别问题credentials
SELECT
  cookie_id,
  COUNT(*) as attempt_count,
  SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as fail_count,
  ROUND(100.0 * SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) / COUNT(*), 2) as fail_rate
FROM cookie_refresh_logs
WHERE timestamp > datetime('now', '-24 hours')
GROUP BY cookie_id
HAVING fail_count > 2
ORDER BY fail_rate DESC;
```

---

## 紧急措施

### 临时禁用自动刷新

```python
# 在 mq/tasks.py 中注释掉定时任务
# @huey.periodic_task(crontab(minute='*/30'))
# def refresh_credentials_periodic(): ...
```

### 手动触发刷新

```python
# 直接调用实现函数
from mq.tasks import _refresh_credentials_impl
import asyncio

asyncio.run(_refresh_credentials_impl())
```

### 清除所有故障标记

```python
from cache.credential_refresh import COOKIE_POOL_MANAGER

for cid, entry in COOKIE_POOL_MANAGER.pool.items():
    entry.reset_health()
    print(f"Reset {cid}")
```

---

## 相关日志位置

- 主应用日志: `logs/app.log`
- Huey任务日志: `logs/huey.log` (如果配置)
- 系统错误: `logs/error.log`

## 相关代码位置

- 定时任务: `mq/tasks.py` (搜索 `refresh_credentials_periodic`)
- 刷新服务: `cache/credential_refresh.py`
- 数据库日志: `db/crud/cookie_crud.py` (搜索 `log_refresh_event`)
- 数据库模型: `db/models.py` (搜索 `CookieRefreshLog`)
