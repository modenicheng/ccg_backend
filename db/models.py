"""Model definitions for SQLAlchemy ORM."""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum
from typing import Any

from sqlalchemy import (
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    CheckConstraint,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

# pylint: disable=too-few-public-methods, not-callable


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""


class RoomStatusORM(IntEnum):
    """Enum for room status in the database."""

    WAITING = 0
    RUNNING = 1
    ENDED = 2


class User(Base):
    """用户表 users
    由于 user 是属于 room 的，所以 username 在 room 内唯一，但在全局范围内可能重复。因此不对 username 添加全局唯一约束，
    而是通过 room_id + username 的组合来确保在同一房间内用户名的唯一性。
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String, nullable=False)
    room_id: Mapped[str | None] = mapped_column(ForeignKey("rooms.id",
                                                           ondelete="CASCADE"),
                                                nullable=True)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp())
    token: Mapped[str] = mapped_column(String,
                                       nullable=False,
                                       unique=True,
                                       comment="用于用户身份验证的唯一令牌")
    online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index("idx_users_room_id", "room_id"),
        UniqueConstraint("room_id", "username", name="uq_users_room_id_username"),
    )

    room: Mapped[Room | None] = relationship(back_populates="users")
    judged_song_tags: Mapped[list[SongTagHistory]] = relationship(
        back_populates="judged_by_user")
    judged_song_descriptions: Mapped[list[SongDescriptionHistory]] = relationship(
        back_populates="judged_by_user")
    scores: Mapped[list[Score]] = relationship(back_populates="user")
    player_answers: Mapped[list[PlayerAnswer]] = relationship(back_populates="user")


class Songlist(Base):
    """歌单表 songlists"""

    __tablename__ = "songlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str | None] = mapped_column(String, nullable=True)
    platform_songlist_id: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    creator_name: Mapped[str | None] = mapped_column(String, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (UniqueConstraint("platform",
                                       "platform_songlist_id",
                                       name="uq_songlist_platform_id"),)

    songs: Mapped[list[SonglistSong]] = relationship(back_populates="songlist",
                                                     cascade="all, delete-orphan")


class SonglistSong(Base):
    """歌单歌曲关联表 songlist_songs"""

    __tablename__ = "songlist_songs"

    songlist_id: Mapped[int] = mapped_column(ForeignKey("songlists.id",
                                                        ondelete="CASCADE"),
                                             primary_key=True)
    song_id: Mapped[int] = mapped_column(ForeignKey("songs.id", ondelete="CASCADE"),
                                         primary_key=True)
    song_order: Mapped[int | None] = mapped_column(Integer, nullable=True)

    songlist: Mapped[Songlist] = relationship(back_populates="songs")
    song: Mapped[Song] = relationship(back_populates="songlists")


class Song(Base):
    """歌曲表 songs"""

    __tablename__ = "songs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str | None] = mapped_column(String, nullable=True)
    platform_song_id: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    subtitle: Mapped[str | None] = mapped_column(String, nullable=True)
    artist: Mapped[str | None] = mapped_column(String, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(String, nullable=True)
    # audio_url: Mapped[str | None] = mapped_column(String, nullable=True)
    cached_path: Mapped[str | None] = mapped_column(String, nullable=True)
    album_name: Mapped[str | None] = mapped_column(String, nullable=True)
    album_id: Mapped[int | None] = mapped_column(ForeignKey("albums.id",
                                                            ondelete="SET NULL"),
                                                 nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (UniqueConstraint("platform",
                                       "platform_song_id",
                                       name="uq_song_platform_id"),)

    rooms: Mapped[list[RoomSong]] = relationship(back_populates="song")
    album: Mapped[Album | None] = relationship(back_populates="songs", uselist=False)
    songlists: Mapped[list[SonglistSong]] = relationship(back_populates="song")
    song_tag_histories: Mapped[list[SongTagHistory]] = relationship(
        back_populates="song")
    song_description_histories: Mapped[list[SongDescriptionHistory]] = relationship(
        back_populates="song")
    player_answers: Mapped[list[PlayerAnswer]] = relationship(back_populates="song")


class Album(Base):
    """Album table."""

    __tablename__ = "albums"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str | None] = mapped_column(String, nullable=True)
    platform_album_id: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    artist: Mapped[str | None] = mapped_column(String, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (UniqueConstraint("platform",
                                       "platform_album_id",
                                       name="uq_album_platform_id"),)

    songs: Mapped[list[Song]] = relationship(back_populates="album")


class Room(Base):
    """房间表 rooms"""

    __tablename__ = "rooms"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[RoomStatusORM] = mapped_column(Integer,
                                                  default=RoomStatusORM.WAITING,
                                                  nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    song_start_range_percent: Mapped[float | None] = mapped_column(Float, nullable=True)

    current_song_index: Mapped[int | None] = mapped_column(Integer,
                                                           nullable=True,
                                                           default=None)

    round_state: Mapped[int | None] = mapped_column(Integer, nullable=True,
                                                    default=0)  # 0 for PENDING

    playback_state_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="持久化播放状态快照，cache miss 时用于恢复")

    __table_args__ = (
        CheckConstraint("status in (0, 1, 2)", name="ck_rooms_status"),
        CheckConstraint("round_state in (0, 1, 2, 3, 4)", name="ck_rooms_round_state"),
    )

    users: Mapped[list[User]] = relationship(back_populates="room",
                                             cascade="all, delete-orphan")
    room_songs: Mapped[list[RoomSong]] = relationship(back_populates="room")
    tag_groups: Mapped[list[TagGroup]] = relationship(back_populates="rooms",
                                                      secondary="tag_groups_rooms")
    scores: Mapped[list[Score]] = relationship(back_populates="room")
    player_answers: Mapped[list[PlayerAnswer]] = relationship(back_populates="room")
    song_tag_history: Mapped[list[SongTagHistory]] = relationship(back_populates="room")
    song_description_history: Mapped[list[SongDescriptionHistory]] = relationship(
        back_populates="room")


class RoomSong(Base):
    """房间歌曲关联表 room_songs"""

    __tablename__ = "room_songs"

    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"),
                                         primary_key=True)
    song_id: Mapped[int] = mapped_column(ForeignKey("songs.id", ondelete="CASCADE"),
                                         primary_key=True)

    # 这里用于记录歌曲在房间内的顺序，数值越小表示越靠前。可以为 null，表示没有特定顺序要求。
    song_order: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 临时播放 URL 及其过期时间，用于预下载和 URL 轮换管理
    temp_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    expire_at: Mapped[datetime | None] = mapped_column(DateTime,
                                                       nullable=True,
                                                       index=True)

    room: Mapped[Room] = relationship(back_populates="room_songs")
    song: Mapped[Song] = relationship(back_populates="rooms")


class TagGroup(Base):
    """标签组表 tag_groups"""

    __tablename__ = "tag_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp())

    tags: Mapped[list[Tag]] = relationship(secondary="tag_group_tags",
                                           back_populates="groups")
    rooms: Mapped[list[Room]] = relationship(back_populates="tag_groups",
                                             secondary="tag_groups_rooms")


class TagGroupRoom(Base):
    """标签组与房间关联表 tag_groups_rooms"""

    __tablename__ = "tag_groups_rooms"

    group_id: Mapped[int] = mapped_column(ForeignKey("tag_groups.id",
                                                     ondelete="CASCADE"),
                                          primary_key=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"),
                                         primary_key=True)


class TagGroupTag(Base):
    """标签组与标签关联表 tag_group_tags"""

    __tablename__ = "tag_group_tags"

    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"),
                                        primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("tag_groups.id",
                                                     ondelete="CASCADE"),
                                          primary_key=True)


class Tag(Base):
    """标签表 tags"""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)

    groups: Mapped[list[TagGroup]] = relationship(secondary="tag_group_tags",
                                                  back_populates="tags")
    song_history: Mapped[list[SongTagHistory]] = relationship(back_populates="tag")


class SongTagHistory(Base):
    """歌曲标签历史表 song_tag_history"""

    __tablename__ = "song_tag_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    song_id: Mapped[int] = mapped_column(ForeignKey("songs.id", ondelete="CASCADE"),
                                         nullable=False)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"),
                                        nullable=False)
    judged_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id",
                                                              ondelete="SET NULL"),
                                                   nullable=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="SET NULL"),
                                         nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp())

    song: Mapped[Song] = relationship(back_populates="song_tag_histories")
    tag: Mapped[Tag] = relationship(back_populates="song_history")
    judged_by_user: Mapped[User] = relationship(back_populates="judged_song_tags")
    room: Mapped[Room] = relationship(back_populates="song_tag_history")


class SongDescriptionHistory(Base):
    """精准描述历史表 song_description_history"""

    __tablename__ = "song_description_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    song_id: Mapped[int] = mapped_column(ForeignKey("songs.id", ondelete="CASCADE"),
                                         nullable=False)
    description_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    judged_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id",
                                                              ondelete="SET NULL"),
                                                   nullable=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="SET NULL"),
                                         nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp())

    song: Mapped[Song] = relationship(back_populates="song_description_histories")
    judged_by_user: Mapped[User] = relationship(
        back_populates="judged_song_descriptions")
    room: Mapped[Room] = relationship(back_populates="song_description_history")


class Score(Base):
    """积分记录表 scores"""

    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"),
                                         nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         nullable=False)
    round_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_delta: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp())

    room: Mapped[Room] = relationship(back_populates="scores")
    user: Mapped[User] = relationship(back_populates="scores")


class PlayerAnswer(Base):
    """玩家答案记录表 player_answers"""

    __tablename__ = "player_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"),
                                         nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         nullable=False)
    song_id: Mapped[int] = mapped_column(ForeignKey("songs.id", ondelete="CASCADE"),
                                         nullable=False)
    round_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected_tag_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    description_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp())

    room: Mapped[Room] = relationship(back_populates="player_answers")
    user: Mapped[User] = relationship(back_populates="player_answers")
    song: Mapped[Song] = relationship(back_populates="player_answers")


class Tasks(Base):
    """任务表 tasks"""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    task_name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp())
