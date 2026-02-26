from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from db.models import Tag, TagGroup
from db.session import get_db
from schemas.tag import TagGroupCreate, TagGroupPatch, TagGroupResponse, TagsCreateRequest, TagResponse, TagListResponse, TagPatch

tag_router = APIRouter(prefix="/api/tags", tags=["tags"])


async def _create_tags(tag_names: list[str], session: AsyncSession):
    # 提取所有标签名并去重（避免同一请求中的重复）
    tag_name_list = list(set(tag_names))
    if not tag_name_list:
        return []

    # 批量插入新标签，忽略已存在的标签（使用 ON CONFLICT DO NOTHING）
    # 注意：这里使用 PostgreSQL 的 INSERT ... ON CONFLICT 语法
    stmt = insert(Tag).values([{"name": name} for name in tag_name_list])
    stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
    await session.execute(stmt)

    # 查询所有标签（包括刚插入的和已存在的）
    result = await session.execute(
        select(Tag).where(Tag.name.in_(tag_name_list)))
    tags = result.scalars().all()
    return tags


async def _get_existing_tags_by_ids(tag_ids: list[int], session: AsyncSession) -> list[Tag]:
    if not tag_ids:
        return []

    # 去重并保持输入顺序，避免重复校验/查询
    unique_tag_ids = list(dict.fromkeys(tag_ids))
    result = await session.execute(
        select(Tag).where(Tag.id.in_(unique_tag_ids))
    )
    existing_tags = list(result.scalars().all())

    found_ids = {tag.id for tag in existing_tags}
    missing_ids = [tag_id for tag_id in unique_tag_ids if tag_id not in found_ids]
    if missing_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Tags with IDs {missing_ids} not found"
        )

    return existing_tags


@tag_router.post("/", response_model=TagListResponse)
async def create_tags(tag_names: TagsCreateRequest,
                      session: AsyncSession = Depends(get_db)):
    tags = await _create_tags(tag_names.tags, session)
    await session.commit()
    return TagListResponse(
        tags=[TagResponse.model_validate(tag) for tag in tags])


@tag_router.get("/")
async def get_tags(
        session: AsyncSession = Depends(get_db),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
):
    result = await session.execute(select(Tag).limit(limit).offset(offset))
    tags = result.scalars().all()
    return TagListResponse(
        tags=[TagResponse.model_validate(tag) for tag in tags])


@tag_router.patch("/{tag_id}", response_model=TagResponse)
async def patch_tag(
    tag_id: int,
    data: TagPatch,
    session: AsyncSession = Depends(get_db)
):
    result = await session.execute(select(Tag).where(Tag.id == tag_id))
    tag = result.scalar_one_or_none()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    new_name = data.name.strip()
    if not new_name:
        raise HTTPException(status_code=422, detail="name cannot be empty")

    tag.name = new_name
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Tag name already exists")

    return TagResponse.model_validate(tag)


@tag_router.delete("/{tag_id}", status_code=204)
async def delete_tag(
    tag_id: int,
    session: AsyncSession = Depends(get_db)
):
    result = await session.execute(select(Tag).where(Tag.id == tag_id))
    tag = result.scalar_one_or_none()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    await session.delete(tag)
    await session.commit()


@tag_router.get("/groups/", response_model=list[TagGroupResponse])
async def get_tag_groups(
        session: AsyncSession = Depends(get_db),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
):
    result = await session.execute(
        select(TagGroup)
        .options(selectinload(TagGroup.tags))
        .limit(limit)
        .offset(offset)
    )
    groups = result.scalars().all()
    return [TagGroupResponse.model_validate(group) for group in groups]

@tag_router.post("/groups/", response_model=TagGroupResponse)
async def create_tag_group(data: TagGroupCreate,
                            session: AsyncSession = Depends(get_db)):
    # 收集所有要关联的标签
    tags_to_associate: list[Tag] = []

    # 处理新标签
    new_tag_names = data.tags
    if new_tag_names:
        new_tags = await _create_tags(new_tag_names, session)
        tags_to_associate.extend(new_tags)

    # 处理现有标签ID
    if data.existing_tag_ids:
        existing_tags = await _get_existing_tags_by_ids(
            data.existing_tag_ids,
            session
        )
        tags_to_associate.extend(existing_tags)

    # 去重：基于标签ID去除重复
    unique_tags = {}
    for tag in tags_to_associate:
        unique_tags[tag.id] = tag

    # 创建标签组并通过 ORM 关系一次性绑定标签
    # 使用构造赋值可避免 AsyncSession 下访问未加载关系触发懒加载导致 MissingGreenlet
    tag_group = TagGroup(
        name=data.name,
        description=data.description,
        tags=list(unique_tags.values())
    )
    session.add(tag_group)

    await session.commit()

    # 重新查询完整的标签组及其关联的标签
    result = await session.execute(
        select(TagGroup)
        .where(TagGroup.id == tag_group.id)
        .options(selectinload(TagGroup.tags))
    )
    tag_group = result.scalar_one()

    return TagGroupResponse.model_validate(tag_group)

@tag_router.patch("/groups/", response_model=TagGroupResponse)
async def patch_tag_group(
    data: TagGroupPatch,
    session: AsyncSession = Depends(get_db)
):
    group_id = data.id
    # 1. 获取标签组
    result = await session.execute(
        select(TagGroup)
        .where(TagGroup.id == group_id)
        .options(selectinload(TagGroup.tags))
    )
    tag_group = result.scalar_one_or_none()
    if not tag_group:
        raise HTTPException(status_code=404, detail="Tag group not found")

    # 2. 更新基本字段（仅更新请求中提供的字段）
    if "name" in data.model_fields_set:
        if data.name is None:
            raise HTTPException(status_code=422, detail="name cannot be null")
        tag_group.name = data.name
    if "description" in data.model_fields_set:
        tag_group.description = data.description

    # 3. 处理要移除的标签ID（通过 ORM 关系维护中间表）
    if "remove_tag_ids" in data.model_fields_set and data.remove_tag_ids:
        remove_ids_set = set(data.remove_tag_ids)
        tag_group.tags = [
            tag for tag in tag_group.tags
            if tag.id not in remove_ids_set
        ]

    existing_tag_ids = {tag.id for tag in tag_group.tags}

    # 5. 处理新增标签（新标签名称）
    tags_to_add = []
    if "add_tags" in data.model_fields_set:
        # 提取标签名称列表
        new_tag_names = [tag.name for tag in data.add_tags]
        new_tags = await _create_tags(new_tag_names, session)
        tags_to_add.extend(new_tags)

    # 6. 处理新增的已有标签ID
    if "add_existing_tag_ids" in data.model_fields_set and data.add_existing_tag_ids:
        existing_tags = await _get_existing_tags_by_ids(
            data.add_existing_tag_ids,
            session
        )
        tags_to_add.extend(existing_tags)

    # 7. 去重：基于标签ID去除重复（包括已关联的标签）
    unique_tags_to_add = [
        tag
        for tag_id, tag in {tag.id: tag for tag in tags_to_add}.items()
        if tag_id not in existing_tag_ids
    ]

    # 8. 添加关联
    if unique_tags_to_add:
        tag_group.tags.extend(unique_tags_to_add)

    await session.commit()

    # 9. 重新查询完整的标签组及其关联的标签
    result = await session.execute(
        select(TagGroup)
        .where(TagGroup.id == group_id)
        .options(selectinload(TagGroup.tags))
    )
    tag_group = result.scalar_one()

    return TagGroupResponse.model_validate(tag_group)


@tag_router.delete("/groups/{group_id}", status_code=204)
async def delete_tag_group(
    group_id: int,
    session: AsyncSession = Depends(get_db)
):
    result = await session.execute(
        select(TagGroup).where(TagGroup.id == group_id)
    )
    tag_group = result.scalar_one_or_none()
    if not tag_group:
        raise HTTPException(status_code=404, detail="Tag group not found")

    await session.delete(tag_group)
    await session.commit()