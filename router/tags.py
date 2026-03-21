"""Tag management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import Tag, TagGroup, TagGroupRoom, TagGroupTag
from db.session import get_db
from schemas.common import RoomStateTagGroupItem
from schemas.tag import (
    TagGroupCreate,
    TagGroupPatch,
    TagGroupResponse,
    TagListResponse,
    TagPatch,
    TagResponse,
    TagsCreateRequest,
)
from schemas.ws_messages.room_schemas import (
    TagGroupData,
    TagGroupMessage,
    TagGroupsUpdateData,
    TagGroupsUpdateMessage,
    TagsUpdateData,
    TagsUpdateMessage,
)
from utils import get_logger

logger = get_logger(__name__)
tag_router = APIRouter(prefix="/api/tags", tags=["tags"])


def _get_connected_room_ids(request: Request) -> set[str]:
    """Get currently connected room IDs from websocket client manager."""
    client_manager = request.app.state.clients
    return set(client_manager.get_room_snapshot().keys())


async def _get_related_group_ids_by_tag_ids(session: AsyncSession,
                                            tag_ids: list[int]) -> set[int]:
    """Get tag group IDs related to given tag IDs."""
    if not tag_ids:
        return set()

    result = await session.execute(
        select(TagGroupTag.group_id).where(TagGroupTag.tag_id.in_(tag_ids)))
    return set(result.scalars().all())


async def _get_related_room_ids_by_group_ids(session: AsyncSession, room_ids: set[str],
                                             group_ids: set[int]) -> set[str]:
    """Find rooms that selected any of the given tag group IDs."""
    if not room_ids or not group_ids:
        return set()

    result = await session.execute(
        select(TagGroupRoom.room_id).where(
            TagGroupRoom.room_id.in_(room_ids),
            TagGroupRoom.group_id.in_(group_ids),
        ))
    return set(result.scalars().all())


async def _broadcast_room_tag_groups_to_rooms(request: Request, session: AsyncSession,
                                              room_ids: set[str]) -> None:
    """Broadcast room selected tag_groups only (TAG_GROUP event)."""
    if not room_ids:
        return

    client_manager = request.app.state.clients
    result = await session.execute(
        select(TagGroupRoom.room_id, TagGroup).join(
            TagGroup,
            TagGroup.id == TagGroupRoom.group_id,
        ).where(TagGroupRoom.room_id.in_(room_ids)).options(selectinload(
            TagGroup.tags),))
    rows = result.all()

    room_group_map: dict[str, list[TagGroup]] = {rid: [] for rid in room_ids}
    for rid, group in rows:
        room_group_map.setdefault(rid, []).append(group)

    for rid, groups in room_group_map.items():
        try:
            message = TagGroupMessage(data=TagGroupData(
                room_id=rid,
                tag_groups=[
                    RoomStateTagGroupItem.model_validate(group) for group in groups
                ],
            ))
            await client_manager.broadcast(rid, message.model_dump())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Failed to broadcast TAG_GROUP to room %s: %s", rid, exc)


async def _broadcast_tags_update(
    request: Request,
    session: AsyncSession,
    added_tags: list[Tag] | None = None,
    updated_tags: list[Tag] | None = None,
    deleted_tag_ids: list[int] | None = None,
    impacted_group_ids: set[int] | None = None,
    target_room_ids: set[str] | None = None,
) -> set[str]:
    """Broadcast tags update to related connected rooms only."""
    client_manager = request.app.state.clients
    connected_room_ids = _get_connected_room_ids(request)

    if target_room_ids is None:
        target_room_ids = await _get_related_room_ids_by_group_ids(
            session,
            connected_room_ids,
            impacted_group_ids or set(),
        )

    if not target_room_ids:
        return set()

    message = TagsUpdateMessage(data=TagsUpdateData(
        added_tags=[TagResponse.model_validate(tag) for tag in (added_tags or [])],
        updated_tags=[TagResponse.model_validate(tag) for tag in (updated_tags or [])],
        deleted_tag_ids=deleted_tag_ids or [],
    ))

    for room_id in sorted(target_room_ids):
        try:
            await client_manager.broadcast(room_id, message.model_dump())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Failed to broadcast tags update to room %s: %s", room_id, exc)

    return set(target_room_ids)


async def _broadcast_tag_groups_update(
    request: Request,
    session: AsyncSession,
    added_groups: list[TagGroup] | None = None,
    updated_groups: list[TagGroup] | None = None,
    deleted_group_ids: list[int] | None = None,
    impacted_group_ids: set[int] | None = None,
    target_room_ids: set[str] | None = None,
) -> set[str]:
    """Broadcast taggroups update to related connected rooms only."""
    client_manager = request.app.state.clients
    connected_room_ids = _get_connected_room_ids(request)

    if target_room_ids is None:
        target_room_ids = await _get_related_room_ids_by_group_ids(
            session,
            connected_room_ids,
            impacted_group_ids or set(),
        )

    if not target_room_ids:
        return set()

    message = TagGroupsUpdateMessage(data=TagGroupsUpdateData(
        added_tag_groups=[
            TagGroupResponse.model_validate(group) for group in (added_groups or [])
        ],
        updated_tag_groups=[
            TagGroupResponse.model_validate(group) for group in (updated_groups or [])
        ],
        deleted_tag_group_ids=deleted_group_ids or [],
    ))

    for room_id in sorted(target_room_ids):
        try:
            await client_manager.broadcast(room_id, message.model_dump())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Failed to broadcast tag groups update to room %s: %s",
                         room_id, exc)

    return set(target_room_ids)


async def _create_tags(tag_names: list[str], session: AsyncSession) -> list[Tag]:
    # 提取所有标签名并去重（避免同一请求中的重复）
    tag_name_list = list(set(tag_names))
    if not tag_name_list:
        return []

    # 批量插入新标签，忽略已存在的标签（使用 ON CONFLICT DO NOTHING）
    stmt = insert(Tag).values([{"name": name} for name in tag_name_list])
    stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
    await session.execute(stmt)

    # 查询所有标签（包括刚插入的和已存在的）
    result = await session.execute(select(Tag).where(Tag.name.in_(tag_name_list)))
    tags = result.scalars().all()
    return list(tags)


async def _get_existing_tags_by_ids(tag_ids: list[int],
                                    session: AsyncSession) -> list[Tag]:
    if not tag_ids:
        return []

    unique_tag_ids = list(dict.fromkeys(tag_ids))
    result = await session.execute(select(Tag).where(Tag.id.in_(unique_tag_ids)))
    existing_tags = list(result.scalars().all())

    found_ids = {tag.id for tag in existing_tags}
    missing_ids = [tag_id for tag_id in unique_tag_ids if tag_id not in found_ids]
    if missing_ids:
        raise HTTPException(status_code=404,
                            detail=f"Tags with IDs {missing_ids} not found")

    return existing_tags


@tag_router.post("/", response_model=TagListResponse)
async def create_tags(
        tag_names: TagsCreateRequest,
        request: Request,
        session: AsyncSession = Depends(get_db),
) -> TagListResponse:
    """Create new tags."""
    tags = await _create_tags(tag_names.tags, session)
    await session.commit()

    # 新建 tag 尚未关联到任何房间已选择的 taggroup，不做全量广播。
    _ = request

    return TagListResponse(tags=[TagResponse.model_validate(tag) for tag in tags])


@tag_router.get("/")
async def get_tags(
        session: AsyncSession = Depends(get_db),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
) -> TagListResponse:
    """Get paginated list of tags."""
    result = await session.execute(select(Tag).limit(limit).offset(offset))
    tags = result.scalars().all()
    return TagListResponse(tags=[TagResponse.model_validate(tag) for tag in tags])


@tag_router.patch("/{tag_id}", response_model=TagResponse)
async def patch_tag(
        tag_id: int,
        data: TagPatch,
        request: Request,
        session: AsyncSession = Depends(get_db),
) -> TagResponse:
    """Update a tag."""
    result = await session.execute(select(Tag).where(Tag.id == tag_id))
    tag = result.scalar_one_or_none()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    new_name = data.name.strip()
    if not new_name:
        raise HTTPException(status_code=422, detail="name cannot be empty")

    impacted_group_ids = await _get_related_group_ids_by_tag_ids(session, [tag_id])

    tag.name = new_name
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Tag name already exists") from exc

    related_room_ids = await _broadcast_tags_update(
        request,
        session,
        updated_tags=[tag],
        impacted_group_ids=impacted_group_ids,
    )
    await _broadcast_room_tag_groups_to_rooms(request, session, related_room_ids)

    return TagResponse.model_validate(tag)


@tag_router.delete("/{tag_id}", status_code=204)
async def delete_tag(
        tag_id: int,
        request: Request,
        session: AsyncSession = Depends(get_db),
) -> None:
    """Delete a tag."""
    result = await session.execute(select(Tag).where(Tag.id == tag_id))
    tag = result.scalar_one_or_none()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    impacted_group_ids = await _get_related_group_ids_by_tag_ids(session, [tag_id])

    await session.delete(tag)
    await session.commit()

    related_room_ids = await _broadcast_tags_update(
        request,
        session,
        deleted_tag_ids=[tag_id],
        impacted_group_ids=impacted_group_ids,
    )
    await _broadcast_room_tag_groups_to_rooms(request, session, related_room_ids)


@tag_router.get("/groups/", response_model=list[TagGroupResponse])
async def get_tag_groups(
        session: AsyncSession = Depends(get_db),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
) -> list[TagGroupResponse]:
    """Get paginated list of tag groups."""
    result = await session.execute(
        select(TagGroup).options(selectinload(
            TagGroup.tags)).limit(limit).offset(offset))
    groups = result.scalars().all()
    return [TagGroupResponse.model_validate(group) for group in groups]


@tag_router.post("/groups/", response_model=TagGroupResponse)
async def create_tag_group(
        data: TagGroupCreate,
        request: Request,
        session: AsyncSession = Depends(get_db),
) -> TagGroupResponse:
    """Create a new tag group."""
    tags_to_associate: list[Tag] = []

    if data.tags:
        new_tags = await _create_tags(data.tags, session)
        tags_to_associate.extend(new_tags)

    if data.existing_tag_ids:
        existing_tags = await _get_existing_tags_by_ids(data.existing_tag_ids, session)
        tags_to_associate.extend(existing_tags)

    unique_tags = {tag.id: tag for tag in tags_to_associate}

    tag_group = TagGroup(name=data.name,
                         description=data.description,
                         tags=list(unique_tags.values()))
    session.add(tag_group)

    await session.commit()

    result = await session.execute(
        select(TagGroup).where(TagGroup.id == tag_group.id).options(
            selectinload(TagGroup.tags)))
    tag_group = result.scalar_one()

    impacted_group_ids = {tag_group.id}
    related_room_ids = await _broadcast_tag_groups_update(
        request,
        session,
        added_groups=[tag_group],
        impacted_group_ids=impacted_group_ids,
    )
    await _broadcast_room_tag_groups_to_rooms(request, session, related_room_ids)

    return TagGroupResponse.model_validate(tag_group)


@tag_router.patch("/groups/", response_model=TagGroupResponse)
async def patch_tag_group(
        data: TagGroupPatch,
        request: Request,
        session: AsyncSession = Depends(get_db),
) -> TagGroupResponse:
    """Update a tag group."""
    group_id = data.id

    result = await session.execute(
        select(TagGroup).where(TagGroup.id == group_id).options(
            selectinload(TagGroup.tags)))
    tag_group = result.scalar_one_or_none()
    if not tag_group:
        raise HTTPException(status_code=404, detail="Tag group not found")

    if "name" in data.model_fields_set:
        if data.name is None:
            raise HTTPException(status_code=422, detail="name cannot be null")
        tag_group.name = data.name
    if "description" in data.model_fields_set:
        tag_group.description = data.description

    if "remove_tag_ids" in data.model_fields_set and data.remove_tag_ids:
        remove_ids_set = set(data.remove_tag_ids)
        tag_group.tags = [tag for tag in tag_group.tags if tag.id not in remove_ids_set]

    existing_tag_ids = {tag.id for tag in tag_group.tags}

    tags_to_add: list[Tag] = []
    if "add_tags" in data.model_fields_set:
        new_tag_names = [tag.name for tag in data.add_tags]
        new_tags = await _create_tags(new_tag_names, session)
        tags_to_add.extend(new_tags)

    if "add_existing_tag_ids" in data.model_fields_set and data.add_existing_tag_ids:
        existing_tags = await _get_existing_tags_by_ids(data.add_existing_tag_ids,
                                                        session)
        tags_to_add.extend(existing_tags)

    unique_tags_to_add = [
        tag for tag_id, tag in {
            tag.id: tag for tag in tags_to_add
        }.items() if tag_id not in existing_tag_ids
    ]

    if unique_tags_to_add:
        tag_group.tags.extend(unique_tags_to_add)

    await session.commit()

    result = await session.execute(
        select(TagGroup).where(TagGroup.id == group_id).options(
            selectinload(TagGroup.tags)))
    tag_group = result.scalar_one()

    impacted_group_ids = {group_id}
    related_room_ids = await _broadcast_tag_groups_update(
        request,
        session,
        updated_groups=[tag_group],
        impacted_group_ids=impacted_group_ids,
    )
    await _broadcast_room_tag_groups_to_rooms(request, session, related_room_ids)

    return TagGroupResponse.model_validate(tag_group)


@tag_router.delete("/groups/{group_id}", status_code=204)
async def delete_tag_group(
        group_id: int,
        request: Request,
        session: AsyncSession = Depends(get_db),
) -> None:
    """Delete a tag group."""
    result = await session.execute(select(TagGroup).where(TagGroup.id == group_id))
    tag_group = result.scalar_one_or_none()
    if not tag_group:
        raise HTTPException(status_code=404, detail="Tag group not found")

    connected_room_ids = _get_connected_room_ids(request)
    related_room_ids = await _get_related_room_ids_by_group_ids(
        session,
        connected_room_ids,
        {group_id},
    )

    await session.delete(tag_group)
    await session.commit()

    await _broadcast_tag_groups_update(
        request,
        session,
        deleted_group_ids=[group_id],
        impacted_group_ids={group_id},
        target_room_ids=related_room_ids,
    )
    await _broadcast_room_tag_groups_to_rooms(request, session, related_room_ids)
