"""WebSocket event handlers for audio playback events (play, pause, seek)."""
from __future__ import annotations

import asyncio

from cache.schemas import PlaybackState
from cache.room_cache import (
    set_room_playback_progress,
    set_room_playback_state,
)
from client_manager import ClientManager, Client
from schemas.ws_messages import playback_schemas
from utils import get_logger
from utils.enumerations import GameEventType
from .registe_manager import regist

logger = get_logger(__name__)


@regist(GameEventType.PLAY, data_validator=playback_schemas.PlayMessage)
async def handle_play(
    data: playback_schemas.PlayMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
) -> None:
    """Handle PLAY event: update playback state to playing and broadcast."""
    state = PlaybackState.model_validate(data.data)
    state.play_state = "playing"  # 确保状态是 playing
    res = await asyncio.gather(
        set_room_playback_state(room_id, state),
        clients.broadcast(room_id,
                          data.model_dump(),
                          excluded_clients={client}),
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


@regist(GameEventType.PAUSE, data_validator=playback_schemas.PauseMessage)
async def handle_pause(
    data: playback_schemas.PauseMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
) -> None:
    """Handle PAUSE event: update playback state to paused and broadcast."""
    state = PlaybackState.model_validate(data.data)
    state.play_state = "paused"  # 确保状态是 paused
    res = await asyncio.gather(
        set_room_playback_state(room_id, state),
        clients.broadcast(room_id,
                          data.model_dump(),
                          excluded_clients={client}),
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
    res = await asyncio.gather(
        set_room_playback_progress(room_id, data.data.progress_ms,
                                   data.data.offset_ts),
        clients.broadcast(room_id,
                          data.model_dump(),
                          excluded_clients={client}),
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
