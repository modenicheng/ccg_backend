from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import cache.schemas as cache_schemas
from handlers import round_events_3x
from schemas.ws_messages.round_event_schemas import (
    AttemptAnswerData,
    AttemptAnswerMessage,
    SubmitAnswerData,
    SubmitAnswerMessage,
)
from schemas.ws_messages.room_schemas import AnswerQueueItem


class _DummySession:

    def add(self, _obj) -> None:
        return None


class _DummySessionScope:

    async def __aenter__(self):
        return _DummySession()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _DummyClient:

    def __init__(self, user):
        self.user = user
        self.send_error = AsyncMock()

    __hash__ = object.__hash__


@pytest.mark.asyncio
async def test_attempt_answer_only_enqueue_when_someone_answering(monkeypatch):
    room_id = "room-1"
    user = SimpleNamespace(id=1001, username="u1", is_owner=False)
    client = _DummyClient(user)

    clients = SimpleNamespace(
        broadcast=AsyncMock(),
        get_clients=Mock(return_value=[]),
    )

    attempt = AttemptAnswerMessage(data=AttemptAnswerData(
        offset_ts=100,
        progress_ms=100,
        user_id=user.id,
    ))

    monkeypatch.setattr(round_events_3x, "get_ts_ms", lambda: 2000)
    monkeypatch.setattr(round_events_3x.room_cache, "append_attempt_answer_player",
                        AsyncMock(return_value=1))
    monkeypatch.setattr(
        round_events_3x.room_cache,
        "get_room_playback_state",
        AsyncMock(return_value=cache_schemas.PlaybackState(
            play_state="paused",
            progress_ms=100,
            offset_ts=1000,
            audio_url="http://example.com/a.mp3",
        )),
    )
    monkeypatch.setattr(round_events_3x.room_cache, "get_room_current_answerer",
                        AsyncMock(return_value="2002"))
    set_current_answerer = AsyncMock()
    sync_answering = AsyncMock()
    monkeypatch.setattr(round_events_3x.room_cache, "set_room_current_answerer",
                        set_current_answerer)
    monkeypatch.setattr(round_events_3x.room_cache, "sync_answer_queue_is_answering",
                        sync_answering)

    await round_events_3x.handle_attempt_answer(
        data=attempt,
        clients=clients,
        client=client,
        room_id=room_id,
    )

    # 只入队，不切换当前作答者
    assert set_current_answerer.await_count == 0
    assert sync_answering.await_count == 0

    # 仍会转发 ATTEMPT_ANSWER 给其他客户端用于队列展示
    assert clients.broadcast.await_count == 1


@pytest.mark.asyncio
async def test_submit_answer_switches_to_next_answerer(monkeypatch):
    room_id = "room-2"
    user = SimpleNamespace(id=1001, username="u1", is_owner=False)
    client = _DummyClient(user)

    clients = SimpleNamespace(
        broadcast=AsyncMock(),
        get_clients=Mock(return_value=[]),
    )

    submit = SubmitAnswerMessage(data=SubmitAnswerData(
        selected_tag_ids=[1, 2],
        description_text="desc",
    ))

    monkeypatch.setattr(round_events_3x, "session_scope", lambda: _DummySessionScope())
    monkeypatch.setattr(round_events_3x.crud, "get_current_song_info",
                        AsyncMock(return_value=(999, 0)))
    monkeypatch.setattr(round_events_3x.room_cache, "get_room_current_answerer",
                        AsyncMock(return_value="1001"))

    queue_with_two_players = [
        AnswerQueueItem(player_id=1001,
                        order=1,
                        offset_ts=100,
                        server_ts=200,
                        is_answering=True),
        AnswerQueueItem(player_id=1002,
                        order=2,
                        offset_ts=110,
                        server_ts=210,
                        is_answering=False),
    ]

    monkeypatch.setattr(
        round_events_3x.room_cache,
        "get_answer_queue",
        AsyncMock(side_effect=[queue_with_two_players, queue_with_two_players]),
    )

    set_current_answerer = AsyncMock()
    sync_answering = AsyncMock()
    monkeypatch.setattr(round_events_3x.room_cache, "set_room_current_answerer",
                        set_current_answerer)
    monkeypatch.setattr(round_events_3x.room_cache, "sync_answer_queue_is_answering",
                        sync_answering)
    monkeypatch.setattr(round_events_3x.room_cache, "clear_room_current_answerer",
                        AsyncMock())

    await round_events_3x.handle_submit_answer(
        data=submit,
        clients=clients,
        client=client,
        room_id=room_id,
    )

    # 当前回答者提交后，切换到下一位
    set_current_answerer.assert_awaited_once_with(room_id, 1002)
    sync_answering.assert_any_await(room_id, 1002)

    # 校验 YOUR_TURN 广播确实指向下一位
    your_turn_payloads = [
        call.args[1]
        for call in clients.broadcast.await_args_list
        if isinstance(call.args[1], dict) and call.args[1].get("event") == 34
    ]
    assert len(your_turn_payloads) == 1
    assert your_turn_payloads[0]["data"]["user_id"] == 1002


@pytest.mark.asyncio
async def test_attempt_answer_selects_queue_head_when_no_current_answerer(monkeypatch):
    room_id = "room-3"
    user = SimpleNamespace(id=1001, username="u1", is_owner=False)
    client = _DummyClient(user)

    clients = SimpleNamespace(
        broadcast=AsyncMock(),
        get_clients=Mock(return_value=[]),
    )

    attempt = AttemptAnswerMessage(data=AttemptAnswerData(
        offset_ts=120,
        progress_ms=120,
        user_id=user.id,
    ))

    monkeypatch.setattr(round_events_3x, "get_ts_ms", lambda: 3000)
    monkeypatch.setattr(round_events_3x.room_cache, "append_attempt_answer_player",
                        AsyncMock(return_value=1))
    monkeypatch.setattr(
        round_events_3x.room_cache,
        "get_room_playback_state",
        AsyncMock(return_value=cache_schemas.PlaybackState(
            play_state="paused",
            progress_ms=120,
            offset_ts=2000,
            audio_url="http://example.com/a.mp3",
        )),
    )
    monkeypatch.setattr(round_events_3x.room_cache, "get_room_current_answerer",
                        AsyncMock(return_value=None))

    # 模拟并发场景：请求者入队后，队列头是更早抢答的另一位玩家
    queue_items = [
        AnswerQueueItem(player_id=2002,
                        order=1,
                        offset_ts=90,
                        server_ts=2800,
                        is_answering=False),
        AnswerQueueItem(player_id=1001,
                        order=2,
                        offset_ts=120,
                        server_ts=3000,
                        is_answering=False),
    ]
    monkeypatch.setattr(round_events_3x.room_cache, "get_answer_queue",
                        AsyncMock(return_value=queue_items))

    set_current_answerer = AsyncMock()
    sync_answering = AsyncMock()
    monkeypatch.setattr(round_events_3x.room_cache, "set_room_current_answerer",
                        set_current_answerer)
    monkeypatch.setattr(round_events_3x.room_cache, "sync_answer_queue_is_answering",
                        sync_answering)

    await round_events_3x.handle_attempt_answer(
        data=attempt,
        clients=clients,
        client=client,
        room_id=room_id,
    )

    # 应选中队列头，而不是当前请求者
    set_current_answerer.assert_awaited_once_with(room_id, 2002)
    sync_answering.assert_awaited_once_with(room_id, 2002)

    your_turn_payloads = [
        call.args[1]
        for call in clients.broadcast.await_args_list
        if isinstance(call.args[1], dict) and call.args[1].get("event") == 34
    ]
    assert len(your_turn_payloads) == 1
    assert your_turn_payloads[0]["data"]["user_id"] == 2002
