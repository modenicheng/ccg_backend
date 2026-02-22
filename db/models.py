from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, CheckConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""
    pass

class User(Base):
    """用户表 users"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    room_id: Mapped[str | None] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), nullable=True)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    room: Mapped[Room | None] = relationship(back_populates="users")


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

    __table_args__ = (
        UniqueConstraint("platform", "platform_songlist_id", name="uq_songlist_platform_id"),
    )

    songs: Mapped[list[SonglistSong]] = relationship(back_populates="songlist", cascade="all, delete-orphan")

class SonglistSong(Base):
    """歌单歌曲关联表 songlist_songs"""

    __tablename__ = "songlist_songs"

    songlist_id: Mapped[int] = mapped_column(ForeignKey("songlists.id", ondelete="CASCADE"), primary_key=True)
    song_id: Mapped[int] = mapped_column(ForeignKey("songs.id"), primary_key=True)
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
    audio_url: Mapped[str | None] = mapped_column(String, nullable=True)
    cached_path: Mapped[str | None] = mapped_column(String, nullable=True)
    album_name: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("platform", "platform_song_id", name="uq_song_platform_id"),
    )

    rooms: Mapped[list[RoomSong]] = relationship(back_populates="song")
    songlists: Mapped[list[SonglistSong]] = relationship(back_populates="song")


class Room(Base):
    """房间表 rooms"""

    __tablename__ = "rooms"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    playlist_id: Mapped[str | None] = mapped_column(String, nullable=True)
    tag_groups_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    users: Mapped[list[User]] = relationship(back_populates="room", cascade="all, delete-orphan")
    room_songs: Mapped[list[RoomSong]] = relationship(back_populates="room")
    tag_groups: Mapped[list[TagGroup]] = relationship(back_populates="room")
    scores: Mapped[list[Score]] = relationship(back_populates="room")
    player_answers: Mapped[list[PlayerAnswer]] = relationship(back_populates="room")
    song_tag_history: Mapped[list[SongTagHistory]] = relationship(back_populates="room")
    song_description_history: Mapped[list[SongDescriptionHistory]] = relationship(back_populates="room")


class RoomSong(Base):
    """房间歌曲关联表 room_songs"""

    __tablename__ = "room_songs"

    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True)
    song_id: Mapped[int] = mapped_column(ForeignKey("songs.id"), primary_key=True)
    song_order: Mapped[int | None] = mapped_column(Integer, nullable=True)

    room: Mapped[Room] = relationship(back_populates="room_songs")
    song: Mapped[Song] = relationship(back_populates="rooms")


class TagGroup(Base):
    """标签组表 tag_groups"""

    __tablename__ = "tag_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    room_id: Mapped[str | None] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    room: Mapped[Room | None] = relationship(back_populates="tag_groups")
    tags: Mapped[list[Tag]] = relationship(back_populates="group", cascade="all, delete-orphan")


class Tag(Base):
    """标签表 tags"""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("tag_groups.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("group_id", "name", name="uq_tag_group_name"),
    )

    group: Mapped[TagGroup] = relationship(back_populates="tags")
    song_history: Mapped[list[SongTagHistory]] = relationship(back_populates="tag")


class SongTagHistory(Base):
    """歌曲标签历史表 song_tag_history"""

    __tablename__ = "song_tag_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    song_id: Mapped[int] = mapped_column(ForeignKey("songs.id"), nullable=False)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"), nullable=False)
    judged_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    song: Mapped[Song] = relationship()
    tag: Mapped[Tag] = relationship(back_populates="song_history")
    judged_by_user: Mapped[User] = relationship()
    room: Mapped[Room] = relationship(back_populates="song_tag_history")


class SongDescriptionHistory(Base):
    """精准描述历史表 song_description_history"""

    __tablename__ = "song_description_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    song_id: Mapped[int] = mapped_column(ForeignKey("songs.id"), nullable=False)
    description_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    judged_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    song: Mapped[Song] = relationship()
    judged_by_user: Mapped[User] = relationship()
    room: Mapped[Room] = relationship(back_populates="song_description_history")


class Score(Base):
    """积分记录表 scores"""

    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    round_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_delta: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    room: Mapped[Room] = relationship(back_populates="scores")
    user: Mapped[User] = relationship()


class PlayerAnswer(Base):
    """玩家答案记录表 player_answers"""

    __tablename__ = "player_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    song_id: Mapped[int] = mapped_column(ForeignKey("songs.id"), nullable=False)
    round_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected_tag_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    description_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())

    room: Mapped[Room] = relationship(back_populates="player_answers")
    user: Mapped[User] = relationship()
    song: Mapped[Song] = relationship()
