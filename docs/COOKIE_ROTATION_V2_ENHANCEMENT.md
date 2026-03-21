# QQMusic Cookie 轮换系统 v2 增强

## 日期

2026 年 3 月 21 日

## 改进内容

### 问题

在 v1 中，虽然理论上支持单 Cookie 的轮换，但有一个隐性限制：

- 错误处理条件：`if COOKIE_ROTATION_MANAGER and not cookie_str`
- 含义：只有当 `cookie_str` 为 None 时，才会进行轮换失效标记和重试

这导致如果通过任何方式传入了 custom `cookie_str`（即使是间接的），单 Cookie 场景也会跳过轮换处理。

### 改进方案

#### 1. 追踪轮换使用状态

添加 `used_rotation_manager` 标志，明确记录是否使用了轮换系统的 Cookie：

```python
# 在 _fetch_songlist_impl() 函数开始处
used_rotation_manager = False

if cookie_str:
    # 使用自定义 Cookie，不使用轮换系统
    ...
elif COOKIE_ROTATION_MANAGER:
    # 使用轮换系统的 Cookie
    used_rotation_manager = True  # ← 明确标记
    ...
```

#### 2. 改进错误处理

从基于 `cookie_str` 的条件改为基于使用状态的条件：

```python
# v1 (问题)
if COOKIE_ROTATION_MANAGER and not cookie_str:
    COOKIE_ROTATION_MANAGER.mark_current_failed(str(e))
    ...

# v2 (改进)
if COOKIE_ROTATION_MANAGER and used_rotation_manager:
    COOKIE_ROTATION_MANAGER.mark_current_failed(str(e))
    ...
```

## 改动详情

### 文件：mq/tasks.py

#### 变更 1：初始化标志（第 603 行）

```python
# Track if we're using rotation system for error handling
used_rotation_manager = False
```

#### 变更 2：标记使用状态（第 653 行）

```python
elif COOKIE_ROTATION_MANAGER:
    current = COOKIE_ROTATION_MANAGER.get_current()
    if current:
        qapi.get_session().credential = current.credential
        current.mark_used()
        used_rotation_manager = True  # ← 新增
        logger.info(
            "Using rotated cookie %s for fetching songlist %s",
            current.cookie_id,
            songlist_id,
        )
```

#### 变更 3：错误处理（第 776, 785 行）

```python
# KeyError 处理
if COOKIE_ROTATION_MANAGER and used_rotation_manager:  # ← 改为 used_rotation_manager
    COOKIE_ROTATION_MANAGER.mark_current_failed(str(e))
    if COOKIE_ROTATION_MANAGER.should_rotate_on_failure():
        COOKIE_ROTATION_MANAGER.rotate(reason="keyerror")

# 通用异常处理
if COOKIE_ROTATION_MANAGER and used_rotation_manager:  # ← 改为 used_rotation_manager
    COOKIE_ROTATION_MANAGER.mark_current_failed(str(e))
    if COOKIE_ROTATION_MANAGER.should_rotate_on_failure():
        COOKIE_ROTATION_MANAGER.rotate(reason="api_error")
```

### 文档：docs/COOKIE_ROTATION_IMPLEMENTATION.md

#### 变更 1：概述部分

- 更新第一条特性：强调支持**单 Cookie 和多 Cookie**
- 说明单 Cookie 也使用轮换系统

#### 变更 2：故障转移流程

- 新增"单 Cookie 场景"子章节
- 解释单 Cookie 时轮换会回到自己，会标记失效并触发异步刷新

#### 变更 3：向后兼容性

- 强调单 Cookie 配置现在也会通过轮换系统管理
- 标记为"✨ v2新增"

#### 变更 4：故障排查

- 更新"轮换不生效"部分
- 添加"单 Cookie 与多 Cookie 的行为差异"表格
- 提供推荐用法

## 效果与优势

| 方面 | v1 | v2 |
|------|-----|-----|
| **单 Cookie 支持** | 有限制 | ✅ 完全支持 |
| **错误处理一致性** | 取决于 cookie_str | ✅ 统一逻辑 |
| **可测试性** | 需要多 Cookie | ✅ 单 Cookie 可测试 |
| **升级路径** | 需要代码改动 | ✅ 无需改代码 |
| **自恢复能力** | 单 Cookie 弱 | ✅ 单 Cookie 也能自恢复 |

## 行为变化

### 单 Cookie 场景下的失败处理

**场景**：只配置了 1 个 Cookie，API 调用连续失败 3 次

```
第1次失败: 记录失败计数 (count=1) → 继续使用该 Cookie
第2次失败: 记录失败计数 (count=2) → 继续使用该 Cookie
第3次失败: 标记为不健康 → 轮换（回到自己）→ 异步刷新恢复
  └─ 审计日志：FROM: cookie_A, TO: cookie_A, reason: "api_error"
```

### 多 Cookie 场景下的失败处理（无改变）

```
第1次失败: 记录失败计数 (count=1) → 继续使用 Cookie A
第2次失败: 记录失败计数 (count=2) → 继续使用 Cookie A
第3次失败: 标记为不健康 → 轮换到 Cookie B
  └─ 审计日志：FROM: cookie_A, TO: cookie_B, reason: "api_error"
```

## 向后兼容性

✅ **完全兼容** - 所有现有代码无需改动

- 现有的单 Cookie 配置自动启用轮换系统（改进）
- 多 Cookie 配置行为不变
- 显式指定 `cookie_str` 的调用仍然不使用轮换

## 测试建议

### 1. 单 Cookie 场景验证

```bash
# 设置单个 Cookie
export CCG_QQ_MUSIC_COOKIE="pgv_pvid=...; fqm_pvqid=..."

# 观察日志中是否出现
# [INFO] Initialized cookie rotation system with primary cookie
# [INFO] Using rotated cookie xxxxx for fetching songlist
```

### 2. 失败转移验证

```bash
# 通过 Redis 或日志检查轮换是否被记录
# 数据库查询
SELECT * FROM cookie_rotation_logs ORDER BY timestamp DESC LIMIT 5;
```

### 3. 多 Cookie 场景验证（无改变）

```bash
# 配置多个 Cookie，验证轮换到不同 Cookie
# 应该看到多个不同的 cookie_id 在轮换
```

## 相关文档

- [Cookie 轮换实现完整指南](./COOKIE_ROTATION_IMPLEMENTATION.md)
- [Cookie 池管理](../utils/cookie_pool.py)
- [轮换管理器](../cache/cookie_rotation.py)

## 后续优化

此改进为后续功能铺平了道路：

1. 从单 Cookie 到多 Cookie 的无缝升级
2. Cookie 预热测试（启动前检测）
3. 智能优先级轮换（基于成功率）
4. 动态 Cookie 获取（外部 API）
