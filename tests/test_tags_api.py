from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.models import Base, Tag, TagGroup, TagGroupTag
from db.session import get_db
from main import app


@pytest_asyncio.fixture
async def session(db_session) -> AsyncIterator[AsyncSession]:
    """使用共享的数据库会话fixture"""
    yield db_session


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[TestClient]:
    """Create a test client with overridden database dependency."""

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_patch_tag_group_basic(client: TestClient, session: AsyncSession):
    """Test PATCH /api/tags/groups/{group_id} with basic fields."""
    # First create a tag group
    tag_group = TagGroup(name="Test Group", description="Original description")
    session.add(tag_group)
    await session.commit()
    await session.refresh(tag_group)

    # Patch the name
    response = client.patch(f"/api/tags/groups/",
                            json={
                                "id": tag_group.id,
                                "name": "Updated Group"
                            })
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == tag_group.id
    assert data["name"] == "Updated Group"
    assert data["description"] == "Original description"
    assert data["tags"] == []

    # Verify in database
    result = await session.execute(select(TagGroup).where(TagGroup.id == tag_group.id))
    updated_group = result.scalar_one()
    assert updated_group.name == "Updated Group"


@pytest.mark.asyncio
async def test_patch_tag_group_description_null(client: TestClient,
                                                session: AsyncSession):
    """Test setting description to null."""
    tag_group = TagGroup(name="Test Group", description="Original description")
    session.add(tag_group)
    await session.commit()
    await session.refresh(tag_group)

    response = client.patch(f"/api/tags/groups/",
                            json={
                                "id": tag_group.id,
                                "description": None
                            })
    assert response.status_code == 200
    data = response.json()
    assert data["description"] is None

    result = await session.execute(select(TagGroup).where(TagGroup.id == tag_group.id))
    updated_group = result.scalar_one()
    assert updated_group.description is None


@pytest.mark.asyncio
async def test_patch_tag_group_add_tags(client: TestClient, session: AsyncSession):
    """Test adding new tags and existing tags."""
    # Create a tag group
    tag_group = TagGroup(name="Test Group")
    session.add(tag_group)
    await session.commit()
    await session.refresh(tag_group)

    # Create some existing tags
    tag1 = Tag(name="Existing Tag 1")
    tag2 = Tag(name="Existing Tag 2")
    session.add_all([tag1, tag2])
    await session.commit()
    await session.refresh(tag1)
    await session.refresh(tag2)

    # Patch: add new tags and existing tags
    response = client.patch(
        f"/api/tags/groups/",
        json={
            "id": tag_group.id,
            "add_tags": [{
                "name": "New Tag 1"
            }, {
                "name": "New Tag 2"
            }],
            "add_existing_tag_ids": [tag1.id, tag2.id],
        },
    )
    print(response.json())
    assert response.status_code == 200
    data = response.json()
    assert len(data["tags"]) == 4
    tag_names = {tag["name"] for tag in data["tags"]}
    assert tag_names == {"Existing Tag 1", "Existing Tag 2", "New Tag 1", "New Tag 2"}

    # Verify associations in database
    result = await session.execute(
        select(TagGroupTag).where(TagGroupTag.group_id == tag_group.id))
    associations = result.scalars().all()
    assert len(associations) == 4


@pytest.mark.asyncio
async def test_patch_tag_group_remove_tags(client: TestClient, session: AsyncSession):
    """Test removing tags from a group."""
    # Create tags
    tag1 = Tag(name="Tag 1")
    tag2 = Tag(name="Tag 2")
    tag3 = Tag(name="Tag 3")
    session.add_all([tag1, tag2, tag3])
    await session.commit()
    await session.refresh(tag1)
    await session.refresh(tag2)
    await session.refresh(tag3)

    # Create a tag group with associations
    tag_group = TagGroup(name="Test Group")
    session.add(tag_group)
    await session.flush()  # get id
    associations = [
        TagGroupTag(group_id=tag_group.id, tag_id=tag1.id),
        TagGroupTag(group_id=tag_group.id, tag_id=tag2.id),
        TagGroupTag(group_id=tag_group.id, tag_id=tag3.id),
    ]
    session.add_all(associations)
    await session.commit()
    await session.refresh(tag_group)

    # Remove two tags
    response = client.patch(
        f"/api/tags/groups/",
        json={
            "id": tag_group.id,
            "remove_tag_ids": [tag1.id, tag2.id]
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["tags"]) == 1
    assert data["tags"][0]["name"] == "Tag 3"

    # Verify associations
    result = await session.execute(
        select(TagGroupTag).where(TagGroupTag.group_id == tag_group.id))
    remaining = result.scalars().all()
    assert len(remaining) == 1
    assert remaining[0].tag_id == tag3.id


@pytest.mark.asyncio
async def test_patch_tag_group_add_and_remove(client: TestClient,
                                              session: AsyncSession):
    """Test adding and removing tags in the same request."""
    # Create existing tags
    tag1 = Tag(name="Tag 1")
    tag2 = Tag(name="Tag 2")
    session.add_all([tag1, tag2])
    await session.commit()
    await session.refresh(tag1)
    await session.refresh(tag2)

    # Create group with tag1
    tag_group = TagGroup(name="Test Group")
    session.add(tag_group)
    await session.flush()
    session.add(TagGroupTag(group_id=tag_group.id, tag_id=tag1.id))
    await session.commit()
    await session.refresh(tag_group)

    # Remove tag1, add tag2 and a new tag
    response = client.patch(
        f"/api/tags/groups/",
        json={
            "id": tag_group.id,
            "remove_tag_ids": [tag1.id],
            "add_existing_tag_ids": [tag2.id],
            "add_tags": [{
                "name": "New Tag"
            }],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["tags"]) == 2
    tag_names = {tag["name"] for tag in data["tags"]}
    assert tag_names == {"Tag 2", "New Tag"}


@pytest.mark.asyncio
async def test_patch_tag_group_not_found(client: TestClient):
    """Test PATCH with non-existent group ID."""
    response = client.patch("/api/tags/groups/", json={"id": 9999, "name": "Updated"})
    assert response.status_code == 404
    assert response.json()["detail"] == "Tag group not found"


@pytest.mark.asyncio
async def test_patch_tag_group_invalid_existing_tag(client: TestClient,
                                                    session: AsyncSession):
    """Test adding non-existent existing tag IDs."""
    tag_group = TagGroup(name="Test Group")
    session.add(tag_group)
    await session.commit()
    await session.refresh(tag_group)

    response = client.patch(
        f"/api/tags/groups/",
        json={
            "id": tag_group.id,
            "add_existing_tag_ids": [999, 1000]
        },
    )
    assert response.status_code == 404
    assert "Tags with IDs" in response.json()["detail"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
