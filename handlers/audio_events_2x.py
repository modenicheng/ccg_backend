import asyncio

from cache.schemas import PlaybackState
from client_manager import ClientManager, Client
from schemas.base_message import ErrorMessage, ErrorMessageData
from schemas.ws_messages import playback_schemas
from utils import get_logger
from cache.room_cache import set_room_playback_progress, set_room_playback_state, get_room_playback_state
from . import regist
from utils.enumerations import GameEventType


@regist(GameEventType.PLAY, data_validator=playback_schemas.PlayMessage)
async def handle_play(
    data: playback_schemas.PlayMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
):
    state = PlaybackState.model_validate(data.data)
    state.play_state = "playing"  # 确保状态是 playing
    await asyncio.gather(
        set_room_playback_state(room_id, state),
        clients.broadcast(room_id, data.model_dump()),
        return_exceptions=True,
    )


@regist(GameEventType.PAUSE, data_validator=playback_schemas.PauseMessage)
async def handle_pause(
    data: playback_schemas.PauseMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
):
    state = PlaybackState.model_validate(data.data)
    state.play_state = "paused"  # 确保状态是 paused
    await asyncio.gather(
        set_room_playback_state(room_id, state),
        clients.broadcast(room_id, data.model_dump()),
        return_exceptions=True,
    )


@regist(GameEventType.SEEK, data_validator=playback_schemas.SeekMessage)
async def handle_seek(
    data: playback_schemas.SeekMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
):

    # 如果前端没有传 offset_ts，就用当前时间戳
    data.data.offset_ts = int(
        data.data.offset_ts) if data.data.offset_ts is not None else data.ts
    await asyncio.gather(
        set_room_playback_progress(room_id, data.data.progress_ms,
                                   data.data.offset_ts),
        clients.broadcast(room_id, data.model_dump()),
        return_exceptions=True,
    )
