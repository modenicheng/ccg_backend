from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from handlers import round_events_3x
from schemas.ws_messages.judge_schemas import SkipRoundMessage
from schemas.ws_messages.round_event_schemas import GameStartData, GameStartMessage


class _FakeRoomResult:

    def __init__(self, room):
        self._room = room

    def scalar_one_or_none(self):
        return self._room


class _FakeSongsResult:

    def __init__(self, songs):
        self._songs = songs

    def scalars(self):
        return SimpleNamespace(all=lambda: self._songs)


class _FakeSession:

    def __init__(self, results):
        self._results = iter(results)

    async def execute(self, _statement):
        return next(self._results)


class _FakeSessionScope:

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False


@pytest.mark.asyncio
async def test_game_start_when_round_begins_schedules_browser_preload_after_server_warming(
        monkeypatch):
    # Given
    room_id = "room-1"
    clients = SimpleNamespace(broadcast=AsyncMock())
    client = SimpleNamespace(send_error=AsyncMock())
    room = SimpleNamespace(show_answer=True)
    session = _FakeSession([
        _FakeRoomResult(room),
        _FakeSongsResult([
            SimpleNamespace(id=101, platform_song_id=None),
            SimpleNamespace(id=202, platform_song_id=None),
        ]),
    ])
    server_warming = AsyncMock()
    browser_preload = AsyncMock()
    preloads = Mock()
    preloads.attach_mock(server_warming, "server_warming")
    preloads.attach_mock(browser_preload, "browser_preload")
    monkeypatch.setattr(round_events_3x, "session_scope",
                        lambda: _FakeSessionScope(session))
    monkeypatch.setattr(round_events_3x.crud, "get_room_song_queue",
                        AsyncMock(return_value=[101, 202]))
    monkeypatch.setattr(round_events_3x.RoomStateManager, "start_game",
                        AsyncMock(return_value=True))
    monkeypatch.setattr(round_events_3x.crud, "get_or_create_audio_token",
                        AsyncMock(return_value="first-song-token"))
    monkeypatch.setattr(round_events_3x.crud, "update_room_current_song_index",
                        AsyncMock())
    monkeypatch.setattr(round_events_3x, "get_audio_stream_url",
                        lambda _token: "/api/songs/stream/first-song-token")
    monkeypatch.setattr(round_events_3x, "get_ts_ms", lambda: 1000)
    monkeypatch.setattr(round_events_3x, "preload_songs_for_round_start",
                        server_warming)
    monkeypatch.setattr(round_events_3x, "prepare_and_broadcast_next_audio",
                        browser_preload)
    monkeypatch.setattr(round_events_3x.room_cache, "set_room_playback_state",
                        AsyncMock())
    monkeypatch.setattr(round_events_3x, "handle_round_state_transition", AsyncMock())

    # When
    await round_events_3x.handle_game_start(
        data=GameStartMessage(data=GameStartData()),
        clients=clients,
        client=client,
        room_id=room_id,
    )

    # Then
    assert preloads.mock_calls == [
        call.server_warming(
            session=session,
            room_id=room_id,
            song_queue=[101, 202],
            current_index=0,
        ),
        call.browser_preload(
            clients=clients,
            session=session,
            room_id=room_id,
            song_queue=[101, 202],
            current_index=0,
        ),
    ]


@pytest.mark.asyncio
async def test_skip_round_when_index_advances_schedules_browser_preload_after_server_warming(
        monkeypatch):
    # Given
    room_id = "room-1"
    clients = SimpleNamespace(broadcast=AsyncMock())
    client = SimpleNamespace(
        user=SimpleNamespace(id=101, username="owner", is_owner=True),
        send_error=AsyncMock(),
    )
    room = SimpleNamespace(current_song_index=0, show_answer=True)
    session = _FakeSession([_FakeRoomResult(room)])
    server_warming = AsyncMock()
    browser_preload = AsyncMock()
    preloads = Mock()
    preloads.attach_mock(server_warming, "server_warming")
    preloads.attach_mock(browser_preload, "browser_preload")
    monkeypatch.setattr(round_events_3x, "session_scope",
                        lambda: _FakeSessionScope(session))
    monkeypatch.setattr(round_events_3x.crud, "get_room_song_queue",
                        AsyncMock(return_value=[101, 202, 303]))
    monkeypatch.setattr(round_events_3x.crud, "update_room_current_song_index",
                        AsyncMock())
    monkeypatch.setattr(round_events_3x.crud, "get_or_create_audio_token",
                        AsyncMock(return_value="next-song-token"))
    monkeypatch.setattr(round_events_3x, "get_audio_stream_url",
                        lambda _token: "/api/songs/stream/next-song-token")
    monkeypatch.setattr(round_events_3x, "get_ts_ms", lambda: 1000)
    monkeypatch.setattr(round_events_3x, "preload_songs_for_round_start",
                        server_warming)
    monkeypatch.setattr(round_events_3x, "prepare_and_broadcast_next_audio",
                        browser_preload)
    monkeypatch.setattr(round_events_3x.room_cache, "clear_answer_queue", AsyncMock())
    monkeypatch.setattr(round_events_3x.room_cache, "clear_room_current_answerer",
                        AsyncMock())
    monkeypatch.setattr(round_events_3x.room_cache, "clear_answer_deadline",
                        AsyncMock())
    monkeypatch.setattr(round_events_3x.room_cache, "set_room_playback_state",
                        AsyncMock())
    monkeypatch.setattr(round_events_3x, "handle_round_state_transition", AsyncMock())

    # When
    await round_events_3x.handle_skip_round(
        data=SkipRoundMessage(),
        clients=clients,
        client=client,
        room_id=room_id,
    )

    # Then
    assert preloads.mock_calls == [
        call.server_warming(
            session=session,
            room_id=room_id,
            song_queue=[101, 202, 303],
            current_index=1,
        ),
        call.browser_preload(
            clients=clients,
            session=session,
            room_id=room_id,
            song_queue=[101, 202, 303],
            current_index=1,
        ),
    ]
