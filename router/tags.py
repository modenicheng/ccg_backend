from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Tag, TagGroup
from db.session import get_db
from schemas.tag import TagsCreateRequest, TagResponse, TagListResponse

tag_router = APIRouter(prefix="/api/tags", tags=["tags"])


@tag_router.post("/", response_model=TagListResponse)
async def create_tags(tag_names: TagsCreateRequest,
                      session: AsyncSession = Depends(get_db)):
    # 提取所有标签名并去重（避免同一请求中的重复）
    tag_name_list = list(set(tag_names.tags))
    if not tag_name_list:
        return {"tags": []}

    # 批量插入新标签，忽略已存在的标签（使用 ON CONFLICT DO NOTHING）
    # 注意：这里使用 PostgreSQL 的 INSERT ... ON CONFLICT 语法
    stmt = insert(Tag).values([{"name": name} for name in tag_name_list])
    stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
    await session.execute(stmt)

    # 查询所有标签（包括刚插入的和已存在的）
    result = await session.execute(
        select(Tag).where(Tag.name.in_(tag_name_list)))
    tags = result.scalars().all()

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


@tag_router.post("/groups/")
async def create_tag_groups():
    pass
