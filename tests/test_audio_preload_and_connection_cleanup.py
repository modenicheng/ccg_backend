from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.websockets import WebSocketState

from client_manager import ClientManager
from handlers import audio_common, connection_lifespan
from handlers.registe_manager import _handlers
from schemas.ws_messages import playback_schemas
from utils.enumerations import GameEventType


class _FakeResult:

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:

    def __init__(self, results):
        self._results = iter(results)

    async def execute(self, _statement):
        return next(self._results)


class _FakeWebSocket:

    def __init__(self, send_json):
        self.client_state = WebSocketState.CONNECTED
        self.send_json = send_json


class _FakeClient:

    def __init__(self, user, websocket):
        self.user = user
        self.ws = websocket
        self.is_spectator = False


class _FakeClientRoomState:

    @classmethod
    def model_validate(cls, _room):
        return SimpleNamespace(
            round_state=0,
            playback_status=None,
            answer_queue=[],
            answer_queue_tail_player_id=None,
            answer_deadline=None,
            round_answers=[],
            round_scored=False,
        )


class _FakeRoomStateMessage:

    def __init__(self, data):
        self.data = data

    def model_dump(self):
        return {"event": GameEventType.ROOM_STATE.value, "data": {}}


def _configure_initial_room_state(monkeypatch):
    monkeypatch.setattr(connection_lifespan.RoomSchema, "ClientRoomState",
                        _FakeClientRoomState)
    monkeypatch.setattr(connection_lifespan.RoomSchema, "RoomStateMessage",
                        _FakeRoomStateMessage)
    monkeypatch.setattr(connection_lifespan.room_cache, "get_room_playback_state",
                        AsyncMock(return_value=None))
    monkeypatch.setattr(connection_lifespan.room_cache, "get_answer_queue",
                        AsyncMock(return_value=[]))
    monkeypatch.setattr(connection_lifespan.room_cache,
                        "get_answer_queue_tail_player_id", AsyncMock(return_value=None))
    monkeypatch.setattr(connection_lifespan.room_cache, "get_answer_deadline",
                        AsyncMock(return_value=None))
    monkeypatch.setattr(connection_lifespan.crud, "get_current_song_info",
                        AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(connection_lifespan, "init_heartbeat_state", AsyncMock())


def test_preload_audio_message_when_serialized_matches_browser_contract():
    # Given
    message = playback_schemas.PreloadAudioMessage(
        data=playback_schemas.PreloadAudioData(
            audio_url="/api/songs/stream/next-token"))

    # When
    payload = message.model_dump(exclude_none=True)

    # Then
    assert payload["event"] == 23
    assert payload["data"] == {"audio_url": "/api/songs/stream/next-token"}
    assert isinstance(payload["ts"], int)


def test_preload_audio_when_registered_has_no_inbound_handler():
    assert GameEventType.PRELOAD_AUDIO.name not in _handlers


@pytest.mark.asyncio
async def test_prepare_and_broadcast_next_audio_when_next_song_exists_emits_stream_url(
        monkeypatch):
    # Given
    clients = SimpleNamespace(broadcast=AsyncMock())
    session = SimpleNamespace()
    get_token = AsyncMock(return_value="next-token")
    monkeypatch.setattr(audio_common, "get_or_create_audio_token", get_token)

    # When
    await audio_common.prepare_and_broadcast_next_audio(
        clients=clients,
        session=session,
        room_id="room-1",
        song_queue=[101, 202],
        current_index=0,
    )

    # Then
    get_token.assert_awaited_once_with(session, "room-1", 202)
    clients.broadcast.assert_awaited_once()
    room_id, payload = clients.broadcast.await_args.args
    assert room_id == "room-1"
    assert payload["event"] == 23
    assert payload["data"] == {"audio_url": "/api/songs/stream/next-token"}
    assert isinstance(payload["ts"], int)


@pytest.mark.asyncio
async def test_prepare_and_broadcast_next_audio_when_current_song_is_last_skips_emission(
        monkeypatch):
    # Given
    clients = SimpleNamespace(broadcast=AsyncMock())
    get_token = AsyncMock()
    monkeypatch.setattr(audio_common, "get_or_create_audio_token", get_token)

    # When
    await audio_common.prepare_and_broadcast_next_audio(
        clients=clients,
        session=SimpleNamespace(),
        room_id="room-1",
        song_queue=[101],
        current_index=0,
    )

    # Then
    get_token.assert_not_awaited()
    clients.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_connect_when_initial_room_state_send_fails_removes_only_failing_client(
        monkeypatch):
    # Given
    _configure_initial_room_state(monkeypatch)
    room_id = "room-1"
    user = SimpleNamespace(id=101, username="player", online=False)
    room = SimpleNamespace(show_answer=False, round_state=0)
    existing_client = _FakeClient(user, _FakeWebSocket(AsyncMock()))
    failing_client = _FakeClient(
        user,
        _FakeWebSocket(AsyncMock(side_effect=RuntimeError("connection lost"))),
    )
    clients = ClientManager()
    clients.push(room_id, existing_client)
    clients.push(room_id, failing_client)
    session = _FakeSession([_FakeResult(room), _FakeResult(user)])

    # When
    await connection_lifespan.on_connect(session, failing_client, clients, room_id)

    # Then
    assert clients.get_clients(room_id) == {existing_client}
    assert clients.has_user_connection(room_id, user.id)


@pytest.mark.asyncio
async def test_on_connect_when_initial_room_state_send_fails_leaves_no_duplicate_session(
        monkeypatch):
    # Given
    _configure_initial_room_state(monkeypatch)
    room_id = "room-1"
    user = SimpleNamespace(id=101, username="player", online=False)
    room = SimpleNamespace(show_answer=False, round_state=0)
    client = _FakeClient(
        user,
        _FakeWebSocket(AsyncMock(side_effect=RuntimeError("connection lost"))),
    )
    clients = ClientManager()
    clients.push(room_id, client)
    session = _FakeSession([_FakeResult(room), _FakeResult(user)])

    # When
    await connection_lifespan.on_connect(session, client, clients, room_id)

    # Then
    assert not clients.has_user_connection(room_id, user.id)


@pytest.mark.asyncio
async def test_on_connect_when_initial_room_state_send_succeeds_retains_registered_client(
        monkeypatch):
    # Given
    _configure_initial_room_state(monkeypatch)
    room_id = "room-1"
    user = SimpleNamespace(id=101, username="player", online=False)
    room = SimpleNamespace(show_answer=False, round_state=0)
    client = _FakeClient(user, _FakeWebSocket(AsyncMock()))
    clients = ClientManager()
    clients.push(room_id, client)
    session = _FakeSession([_FakeResult(room), _FakeResult(user)])

    # When
    await connection_lifespan.on_connect(session, client, clients, room_id)

    # Then
    assert clients.get_clients(room_id) == {client}
