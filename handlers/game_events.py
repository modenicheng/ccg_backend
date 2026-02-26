from fastapi import WebSocket
from pydantic import ValidationError

from cache.utils import room_manager
from schemas.game_events import PauseMessage, PlayMessage, SeekMessage
from utils import get_logger
from utils.enumerations import GameEventType

from . import regist

logger = get_logger(__name__)


def _ensure_owner(websocket: WebSocket | None) -> bool:
    if websocket is None:
        return False
    user = getattr(websocket.state, "user", None)
    return bool(user and getattr(user, "is_owner", False))


def _build_error(event: GameEventType, reason: str):
    return {
        "type": "error",
        "event": event.value,
        "reason": reason,
    }


async def _handle_play_control(
    event: GameEventType,
    data: dict,
    clients=None,
    websocket: WebSocket | None = None,
    room_id: str | None = None,
):
    if websocket is None or room_id is None:
        return

    if not _ensure_owner(websocket):
        await clients.send(websocket, _build_error(event, "Only owner can control playback"))
        return

    try:
        if event == GameEventType.PLAY:
            payload = PlayMessage.model_validate(data)
            round_state = "playing"
        elif event == GameEventType.PAUSE:
            payload = PauseMessage.model_validate(data)
            round_state = "paused"
        else:
            payload = SeekMessage.model_validate(data)
            round_state = "seeking"
    except ValidationError as exc:
        logger.warning("Invalid %s payload: %s", event.name, exc)
        await clients.send(websocket, _build_error(event, f"Invalid payload: {exc.errors()}"))
        return

    await room_manager.update_playback_state(
        room_id=room_id,
        round_state=round_state,
        progress_ms=payload.data.progress_ms,
        offset_ts=payload.data.offset_ts,
        audio_url=payload.data.audio_url,
        event_ts=payload.ts,
        event_name=event.name,
    )

    await clients.broadcast(
        room_id,
        payload.model_dump(),
        except_clients={websocket},
    )


@regist(GameEventType.PLAY)
async def handle_play(data, clients=None, websocket=None, room_id=None, **kwargs):
    if not isinstance(data, dict):
        await clients.send(websocket, _build_error(GameEventType.PLAY, "Expected JSON object"))
        return
    await _handle_play_control(GameEventType.PLAY,
                               data,
                               clients=clients,
                               websocket=websocket,
                               room_id=room_id)


@regist(GameEventType.PAUSE)
async def handle_pause(data, clients=None, websocket=None, room_id=None, **kwargs):
    if not isinstance(data, dict):
        await clients.send(websocket,
                           _build_error(GameEventType.PAUSE, "Expected JSON object"))
        return
    await _handle_play_control(GameEventType.PAUSE,
                               data,
                               clients=clients,
                               websocket=websocket,
                               room_id=room_id)


@regist(GameEventType.SEEK)
async def handle_seek(data, clients=None, websocket=None, room_id=None, **kwargs):
    if not isinstance(data, dict):
        await clients.send(websocket, _build_error(GameEventType.SEEK, "Expected JSON object"))
        return
    await _handle_play_control(GameEventType.SEEK,
                               data,
                               clients=clients,
                               websocket=websocket,
                               room_id=room_id)
