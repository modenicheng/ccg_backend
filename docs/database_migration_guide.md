# 数据库迁移指南（Alembic + uv）

本文档面向本项目开发者，目标是让你在不踩坑的前提下完成：

- 新增/修改表结构
- 生成迁移脚本
- 执行升级/回滚
- 在 SQLite 与 PostgreSQL 之间保持可迁移性

---

## 1. 当前项目约定

- ORM 模型：`db/models.py`
- 会话与引擎：`db/session.py`
- Alembic 配置：`alembic.ini` + `alembic/env.py`
- 迁移脚本目录：`alembic/versions/`
- 数据库连接来源：`.env` 的 `DATABASE_URL`

默认开发库：

- `sqlite+aiosqlite:///data/game.db`

可切 PostgreSQL（预留）：

- `postgresql+asyncpg://user:password@host:5432/dbname`

> `alembic/env.py` 会读取 `.env` 并设置 URL，同时接入 `db.models.Base.metadata`，因此 autogenerate 能识别模型变化。

---

## 2. 常用命令（全部通过 uv）

### 查看当前迁移状态

- `uv run alembic current`

### 生成迁移（自动对比模型）

- `uv run alembic revision --autogenerate -m "add_score_index"`

### 升级到最新版本

- `uv run alembic upgrade head`

### 回滚一个版本

- `uv run alembic downgrade -1`

### 查看迁移历史

- `uv run alembic history`

---

## 3. 标准开发流程（推荐）

1. 修改 `db/models.py`（新增字段、表、约束等）
2. 执行 autogenerate 生成迁移文件
3. **人工检查迁移脚本**（非常关键）
4. 本地升级并验证业务
5. 提交代码时，**模型文件 + 迁移文件必须同时提交**

为什么第 3 步必须人工检查？

- 自动生成无法完全理解业务语义
- 某些数据库行为（如字段重命名）会被识别成“删旧建新”，可能导致数据丢失

---

## 4. 迁移脚本审核清单

每次生成 `alembic/versions/*.py` 后，至少检查：

1. 是否有误删（`drop_column` / `drop_table`）
2. 是否需要数据迁移（例如把旧字段数据写入新字段）
3. 是否补齐索引/唯一约束/外键约束
4. `downgrade()` 是否可用（至少在开发环境可回滚）
5. 命名是否清晰（revision message 建议短且语义明确）

---

## 5. SQLite 与 PostgreSQL 的注意事项

虽然 ORM 一套模型可以跑双库，但迁移细节会有差异：

- SQLite 对部分 `ALTER TABLE` 支持弱，复杂变更可能需要“新建临时表 + 搬数据”
- PostgreSQL 类型能力更强，但对约束和类型转换更严格

建议：

- 开发初期用 SQLite 快速迭代
- 在切到 PostgreSQL 前，至少用一遍 PG 环境执行 `upgrade head` 验证

---

## 6. 常见问题排查

### Q1: `alembic revision --autogenerate` 没检测到变更

排查顺序：

1. 模型是否挂在 `Base` 下（`db/models.py`）
2. `alembic/env.py` 的 `target_metadata` 是否是 `Base.metadata`
3. 是否真的改到了 schema（仅改 Python 逻辑不会触发迁移）

### Q2: Windows 下 Alembic 读取 `alembic.ini` 编码报错

- 避免在 `alembic.ini` 写非 ASCII 注释
- 或统一用 UTF-8 且确保运行环境编码匹配

### Q3: 执行升级时报 URL 驱动错误

- 检查 `.env` 的 `DATABASE_URL`
- 确认依赖已同步：`uv sync`
- PostgreSQL 场景确认 `asyncpg` 已安装

---

## 7. 团队协作建议（避免迁移冲突）

1. 一个功能分支尽量只做一类 schema 变更
2. 合并前执行一次 `uv run alembic upgrade head`
3. 如果多人同时改模型，优先 rebase 后重新生成迁移
4. 不要手动改其他人已发布环境使用过的 revision ID

---

## 8. 你最可能用到的“速查模板”

### 新增字段

1. 改模型
2. `uv run alembic revision --autogenerate -m "add_xxx_field"`
3. 检查脚本是否为 `op.add_column(...)`
4. `uv run alembic upgrade head`

### 字段重命名（高风险）

- autogenerate 常会识别为 drop + add
- 建议手工改迁移为“重命名”或“复制数据后删除旧列”

### 回滚验证

1. `uv run alembic downgrade -1`
2. `uv run alembic upgrade head`

如果这两步都能走通，迁移健壮性通常更好。

---

## 9. 一句话总结

> 迁移的正确姿势是：**模型先改、迁移后生、脚本必审、升级必跑、模型与迁移一起提交**。
