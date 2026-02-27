from fastapi import WebSocket
from pydantic import ValidationError
from typing import Any

from cache.utils import room_manager
from client_manager import ClientManager
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


def _to_int_if_number(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return int(round(value))
    return value


def _normalize_play_control_payload(data: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = dict(data)
    normalized["ts"] = _to_int_if_number(normalized.get("ts"))

    raw_data = normalized.get("data")
    if isinstance(raw_data, dict):
        normalized_data: dict[str, Any] = dict(raw_data)
        normalized_data["offset_ts"] = _to_int_if_number(
            normalized_data.get("offset_ts"))
        normalized_data["progress_ms"] = _to_int_if_number(
            normalized_data.get("progress_ms"))
        normalized["data"] = normalized_data

    return normalized


async def _safe_send_error(
    clients: ClientManager | None,
    websocket: WebSocket | None,
    event: GameEventType,
    reason: str,
):
    if clients is None or websocket is None:
        logger.warning("Skip send error: clients/websocket missing, event=%s",
                       event.name)
        return
    await clients.send(websocket, _build_error(event, reason))


async def _safe_broadcast(
    clients: ClientManager | None,
    room_id: str,
    payload: dict[str, Any],
    websocket: WebSocket,
):
    if clients is None:
        logger.warning("Skip broadcast: clients missing, room=%s", room_id)
        return
    await clients.broadcast(
        room_id,
        payload,
        except_clients={websocket},
    )


async def _handle_play_control(
    event: GameEventType,
    data: dict,
    clients: ClientManager | None = None,
    websocket: WebSocket | None = None,
    room_id: str | None = None,
):
    if websocket is None or room_id is None:
        return

    if not _ensure_owner(websocket):
        await _safe_send_error(clients, websocket, event,
                               "Only owner can control playback")
        return

    normalized_data = _normalize_play_control_payload(data)

    try:
        if event == GameEventType.PLAY:
            payload = PlayMessage.model_validate(normalized_data)
            round_state = "playing"
        elif event == GameEventType.PAUSE:
            payload = PauseMessage.model_validate(normalized_data)
            round_state = "paused"
        else:
            payload = SeekMessage.model_validate(normalized_data)
            round_state = "seeking"
    except ValidationError as exc:
        logger.warning("Invalid %s payload: %s", event.name, exc)
        await _safe_send_error(clients, websocket, event,
                               f"Invalid payload: {exc.errors()}")
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

    await _safe_broadcast(clients, room_id, payload.model_dump(), websocket)


@regist(GameEventType.PLAY)
async def handle_play(data,
                      clients=None,
                      websocket=None,
                      room_id=None,
                      **kwargs):
    if not isinstance(data, dict):
        await _safe_send_error(clients, websocket, GameEventType.PLAY,
                               "Expected JSON object")
        return
    await _handle_play_control(GameEventType.PLAY,
                               data,
                               clients=clients,
                               websocket=websocket,
                               room_id=room_id)


@regist(GameEventType.PAUSE)
async def handle_pause(data,
                       clients=None,
                       websocket=None,
                       room_id=None,
                       **kwargs):
    if not isinstance(data, dict):
        await _safe_send_error(clients, websocket, GameEventType.PAUSE,
                               "Expected JSON object")
        return
    await _handle_play_control(GameEventType.PAUSE,
                               data,
                               clients=clients,
                               websocket=websocket,
                               room_id=room_id)


@regist(GameEventType.SEEK)
async def handle_seek(data,
                      clients=None,
                      websocket=None,
                      room_id=None,
                      **kwargs):
    if not isinstance(data, dict):
        await _safe_send_error(clients, websocket, GameEventType.SEEK,
                               "Expected JSON object")
        return
    await _handle_play_control(GameEventType.SEEK,
                               data,
                               clients=clients,
                               websocket=websocket,
                               room_id=room_id)
