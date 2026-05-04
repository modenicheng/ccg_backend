"""WebSocket event handlers for audio playback events (play, pause, seek)."""
from __future__ import annotations

import asyncio
from typing import Literal

from cache.schemas import PlaybackState
from cache.room_cache import (
    get_room_playback_state,
    set_room_playback_state,
)
from client_manager import ClientManager, Client
from schemas.ws_messages import playback_schemas
from utils import get_logger
from utils.enumerations import GameEventType
from .registe_manager import regist

logger = get_logger(__name__)


async def _build_playback_state_from_control(
    room_id: str,
    control_data: playback_schemas.PlayControlData,
    event_ts: int,
    play_state: Literal["playing", "paused"],
) -> PlaybackState:
    """Merge incoming control payload with cached playback state.

    保证 playback_status 在 Redis 中始终为完整状态：
    - audio_url 缺省时沿用缓存
    - current_order 缺省时沿用缓存（避免 test audio -1 被默认值覆盖）
    """
    existing = await get_room_playback_state(room_id)
    has_current_order = "current_order" in control_data.model_fields_set
    resolved_offset_ts = (int(control_data.offset_ts)
                          if control_data.offset_ts is not None else event_ts)
    resolved_current_order = (control_data.current_order if has_current_order else
                              (existing.current_order if existing is not None else 0))
    resolved_audio_url = (control_data.audio_url if control_data.audio_url is not None
                          else (existing.audio_url if existing is not None else None))

    resolved_show_answer = existing.show_answer if existing is not None else False

    return PlaybackState(
        progress_ms=control_data.progress_ms,
        updated_at=event_ts,
        offset_ts=resolved_offset_ts,
        play_state=play_state,
        current_order=resolved_current_order,
        audio_url=resolved_audio_url,
        show_answer=resolved_show_answer,
    )


@regist(GameEventType.PLAY, data_validator=playback_schemas.PlayMessage)
async def handle_play(
    data: playback_schemas.PlayMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
) -> None:
    """Handle PLAY event: update playback state to playing and broadcast."""
    state = await _build_playback_state_from_control(
        room_id,
        data.data,
        data.ts,
        "playing",
    )
    broadcast_payload = data.model_dump()
    broadcast_payload["data"]["show_answer"] = state.show_answer
    res = await asyncio.gather(
        set_room_playback_state(room_id, state),
        clients.broadcast(room_id, broadcast_payload, excluded_clients={client}),
        return_exceptions=True,
    )
    for i, r in enumerate(res):
        if isinstance(r, Exception):
            logger.error(
                "Exception occurred in asyncio.gather task %d for PLAY event in room %s: %s",
                i,
                room_id,
                r,
                exc_info=r,
            )

    # Start binary audio push
    from handlers.audio_push_task import audio_push_manager, resolve_audio_path  # pylint: disable=import-outside-toplevel
    path_info = await resolve_audio_path(room_id)
    if path_info:
        cached_path, token = path_info
        start_ms = max(0, state.progress_ms)
        await audio_push_manager.start_push(
            room_id,
            token,
            cached_path,
            start_ms,
            clients,
        )


@regist(GameEventType.PAUSE, data_validator=playback_schemas.PauseMessage)
async def handle_pause(
    data: playback_schemas.PauseMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
) -> None:
    """Handle PAUSE event: update playback state to paused and broadcast."""
    state = await _build_playback_state_from_control(
        room_id,
        data.data,
        data.ts,
        "paused",
    )
    broadcast_payload = data.model_dump()
    broadcast_payload["data"]["show_answer"] = state.show_answer
    res = await asyncio.gather(
        set_room_playback_state(room_id, state),
        clients.broadcast(room_id, broadcast_payload, excluded_clients={client}),
        return_exceptions=True,
    )
    for i, r in enumerate(res):
        if isinstance(r, Exception):
            logger.error(
                "Exception occurred in asyncio.gather task %d for PAUSE event in room %s: %s",
                i,
                room_id,
                r,
                exc_info=r,
            )

    # Stop binary audio push
    from handlers.audio_push_task import audio_push_manager  # pylint: disable=import-outside-toplevel
    await audio_push_manager.stop_push(room_id)


@regist(GameEventType.SEEK, data_validator=playback_schemas.SeekMessage)
async def handle_seek(
    data: playback_schemas.SeekMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
) -> None:
    """Handle SEEK event: update playback progress and broadcast."""
    # 如果前端没有传 offset_ts，就用当前时间戳
    data.data.offset_ts = (int(data.data.offset_ts)
                           if data.data.offset_ts is not None else data.ts)

    has_current_order = "current_order" in data.data.model_fields_set
    existing_state = await get_room_playback_state(room_id)
    if existing_state is not None:
        updated_state = existing_state.model_copy(
            update={
                "progress_ms":
                    data.data.progress_ms,
                "offset_ts":
                    data.data.offset_ts,
                "updated_at":
                    data.ts,
                **({
                    "audio_url": data.data.audio_url
                } if data.data.audio_url is not None else {}),
                **({
                    "current_order": data.data.current_order
                } if has_current_order else {}),
            })
    else:
        updated_state = PlaybackState(
            progress_ms=data.data.progress_ms,
            updated_at=data.ts,
            offset_ts=data.data.offset_ts,
            play_state="paused",
            current_order=data.data.current_order if has_current_order else 0,
            audio_url=data.data.audio_url,
        )

    broadcast_payload = data.model_dump()
    broadcast_payload["data"]["show_answer"] = updated_state.show_answer
    res = await asyncio.gather(
        set_room_playback_state(room_id, updated_state),
        clients.broadcast(room_id, broadcast_payload, excluded_clients={client}),
        return_exceptions=True,
    )
    for i, r in enumerate(res):
        if isinstance(r, Exception):
            logger.error(
                "Exception occurred in asyncio.gather task %d for SEEK event in room %s: %s",
                i,
                room_id,
                r,
                exc_info=r,
            )

    # Restart binary audio push from new position
    from handlers.audio_push_task import audio_push_manager, resolve_audio_path  # pylint: disable=import-outside-toplevel
    await audio_push_manager.stop_push(room_id)
    if updated_state.play_state == "playing":
        path_info = await resolve_audio_path(room_id)
        if path_info:
            cached_path, token = path_info
            await audio_push_manager.start_push(
                room_id,
                token,
                cached_path,
                data.data.progress_ms,
                clients,
            )
