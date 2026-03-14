# 房主确认正确答案实现设计

## 1. 概述

本文档详细阐述房主确认正确答案的实现逻辑，包括前端弹窗设计和后端处理流程。由于后端正在进行状态管理重构，本文档仅提供设计方案，不包含实际代码修改。

## 2. 前端设计

### 2.1 弹窗触发时机
- 当游戏进入评分环节（JUDGING事件）时，向房主展示确认正确答案的弹窗
- 其他玩家不显示该弹窗

### 2.2 弹窗内容

#### 2.2.1 曲目信息
- 显示曲目名称、专辑图片等信息（与主房间展示一致）
- 点击曲目信息的任意位置可打开指向曲目详情页的平台URL

#### 2.2.2 TagGroup选择框
- 与房间主页内的结构一致，包含房间设置的若干taggroup
- 实现组内互斥、组间不互斥的逻辑
- 后端传入song_tag_history作为参考（以tag_id形式）
- 对于被展示在前端的tag，如果其位于后端传入的参考tag中，字体加粗显示
- 如果存在一个taggroup有且仅有一个tag被标注了，那么把它预置为勾选

#### 2.2.3 参考精确描述
- 后端传入song_description_history作为参考精确描述展示
- 仅在有数据时显示

#### 2.2.4 抢答者精确描述
- 显示所有抢答者的"精确描述"复选框
- 允许多选

#### 2.2.5 操作按钮
- 跳过本题按钮：向后端传回跳过事件
- 确认答案按钮：向后端传回确认事件和数据内容
- 两个按钮均需要二次确认弹窗

### 2.3 弹窗行为
- 允许房主暂时隐藏弹窗（即回到主房间）
- 弹窗时其余部分变暗显示

## 3. 后端设计

### 3.1 数据模型

#### 3.1.1 SongTagHistory
```python
class SongTagHistory(Base):
    __tablename__ = "song_tag_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    song_id: Mapped[int] = mapped_column(ForeignKey("songs.id", ondelete="CASCADE"), nullable=False)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), nullable=False)
    judged_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
```

#### 3.1.2 SongDescriptionHistory
```python
class SongDescriptionHistory(Base):
    __tablename__ = "song_description_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    song_id: Mapped[int] = mapped_column(ForeignKey("songs.id", ondelete="CASCADE"), nullable=False)
    description_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    judged_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
```

### 3.2 事件处理

#### 3.2.1 JUDGING事件
- 触发时机：游戏进入评分环节
- 处理逻辑：
  1. 从数据库获取当前歌曲的所有历史标签（song_tag_history）
  2. 从数据库获取当前歌曲的正确描述（song_description_history，is_correct=True）
  3. 向前端发送JUDGING事件，包含：
     - 曲目信息
     - 历史标签ID列表
     - 参考精确描述列表（随机一个或多个正确答案）
     - 抢答者的精确描述列表

#### 3.2.2 JUDGE_SUBMIT事件
- 触发时机：房主点击确认答案按钮
- 处理逻辑：
  1. 验证房主身份
  2. 解析前端传入的答案数据：
     - 选中的标签ID列表
     - 选中的精确描述列表
  3. 存储标签历史：
     - 对每个选中的标签ID，检查是否已存在于song_tag_history中
     - 若不存在，则创建新记录
     - 若存在，则跳过
  4. 存储描述历史：
     - 对每个选中的精确描述，创建song_description_history记录
     - 设置is_correct=True
  5. 执行评分逻辑（已有实现，需接入）
  6. 广播SCORE_UPDATE事件

#### 3.2.3 JUDGE_SKIP事件
- 触发时机：房主点击跳过本题按钮
- 处理逻辑：
  1. 验证房主身份
  2. 记录跳过操作
  3. 广播ROUND_END事件

## 4. 数据流程

### 4.1 进入评分环节
1. 后端发送JUDGING事件
2. 前端接收到事件后，显示确认正确答案弹窗
3. 弹窗加载曲目信息、历史标签参考、参考精确描述和抢答者精确描述

### 4.2 确认答案
1. 房主在弹窗中选择正确答案
2. 点击确认按钮，，前端发送JUDGE_SUBMIT事件
3. 后端处理事件，存储答案历史，执行评分
4. 后端广播SCORE_UPDATE事件
5. 前端更新排行榜

### 4.3 跳过本题
1. 房主点击跳过按钮，，前端发送JUDGE_SKIP事件
2. 后端处理事件，结束本轮
3. 后端广播ROUND_END事件
4. 前端进入下一轮或游戏结束

## 5. 注意事项

1. **数据验证**：后端需要验证前端传入的标签ID和描述是否合法
2. **并发处理**：需要处理多用户同时操作的情况
3. **错误处理**：需要处理存储失败等异常情况
4. **性能优化**：对于历史数据查询，需要考虑性能优化
5. **安全性**：需要验证房主身份，防止非房主操作

## 6. 实现建议

1. **前端**：使用React的状态管理（如Zustand）管理弹窗状态和答案数据
2. **后端**：使用异步数据库操作，提高性能
3. **数据结构**：前端和后端之间的数据传输使用JSON格式，确保数据一致性
4. **测试**：需要测试各种边界情况，如空答案、重复答案等

## 7. 后续扩展

1. **答案统计**：可以统计每个标签和描述的被选中次数，用于优化推荐
2. **自动评分**：基于历史数据，实现自动评分功能
3. **答案审核**：对于有争议的答案，可以引入审核机制

本设计方案确保了房主确认正确答案的流程清晰、数据存储完整，同时与现有的评分逻辑无缝对接。
