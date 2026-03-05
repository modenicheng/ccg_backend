from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List


# ---------- Tag 相关 ----------
class TagBase(BaseModel):
    name: str


class TagCreate(TagBase):
    pass


class TagPatch(BaseModel):
    name: str = Field(..., description="更新后的标签名称")


class TagResponse(TagBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


class TagsCreateRequest(BaseModel):
    tags: List[str] = Field(..., description="标签名称列表")


class TagListResponse(BaseModel):
    tags: List[TagResponse]


# ---------- TagGroup 相关 ----------
class TagGroupBase(BaseModel):
    name: str
    description: Optional[str] = None


# 创建 TagGroup 时，可选的 tags 列表
class TagGroupCreate(TagGroupBase):
    tags: List[str] = []  # 允许传入纯新标签（无 id）
    existing_tag_ids: List[int] = []  # 允许传入已有标签的 id


# 更新 TagGroup 时，全量替换 tags（PUT）
class TagGroupUpdate(TagGroupBase):
    tags: List[TagCreate] = []  # 新标签
    existing_tag_ids: List[int] = []  # 已有标签 id


# 部分更新 TagGroup（PATCH），例如只改名称，或增量修改 tags
class TagGroupPatch(BaseModel):
    id: int
    name: Optional[str] = None
    description: Optional[str] = None
    add_tags: List[TagCreate] = []  # 新增的标签（可能包含新标签名）
    add_existing_tag_ids: List[int] = []  # 关联已有标签 id
    remove_tag_ids: List[int] = []  # 解除关联的标签 id


# TagGroup 响应模型（包含关联的 tags）
class TagGroupResponse(TagGroupBase):
    id: int
    tags: List[TagResponse] = []
    model_config = ConfigDict(from_attributes=True)
