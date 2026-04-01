from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers import judge_events_4x
from schemas.ws_messages.judge_schemas import JudgeSubmitData, JudgeSubmitMessage
from utils.enumerations import RoundState


class _DummySessionScope:

    async def __aenter__(self):
        return SimpleNamespace(add=lambda _obj: None)

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _DummyClient:

    def __init__(self, user):
        self.user = user
        self.send_error = AsyncMock()


@pytest.mark.asyncio
async def test_judge_submit_transitions_round_to_completed_after_scoring(monkeypatch):
    room_id = "room-judge-1"
    owner = SimpleNamespace(id=1, username="owner", is_owner=True)
    client = _DummyClient(owner)

    clients = SimpleNamespace(broadcast=AsyncMock())

    message = JudgeSubmitMessage(data=JudgeSubmitData(
        correct_tags=[],
        correct_description_ids=[],
        new_correct_descriptions=[],
        skip_scoring=False,
    ))

    monkeypatch.setattr(judge_events_4x, "session_scope", lambda: _DummySessionScope())

    room_obj = SimpleNamespace(
        users=[
            SimpleNamespace(id=1, username="owner"),
            SimpleNamespace(id=2, username="p2"),
        ],
        current_song_index=0,
    )

    fetch_room_object = AsyncMock(side_effect=[room_obj, room_obj])
    monkeypatch.setattr(judge_events_4x, "fetch_room_object", fetch_room_object)
    monkeypatch.setattr(judge_events_4x, "get_current_song_info",
                        AsyncMock(return_value=(100, 0)))
    monkeypatch.setattr(judge_events_4x.room_cache, "get_answer_queue",
                        AsyncMock(return_value=[]))
    monkeypatch.setattr(judge_events_4x, "get_player_answers_for_judging",
                        AsyncMock(return_value={}))
    monkeypatch.setattr(judge_events_4x, "get_tag_group_map",
                        AsyncMock(return_value={}))
    monkeypatch.setattr(judge_events_4x, "update_player_answer_order",
                        AsyncMock(return_value=0))
    monkeypatch.setattr(judge_events_4x, "save_score_record", AsyncMock())
    monkeypatch.setattr(judge_events_4x.room_cache, "clear_answer_queue", AsyncMock())
    monkeypatch.setattr(judge_events_4x, "get_room_song_queue",
                        AsyncMock(return_value=[100, 101]))

    end_game = AsyncMock(return_value={"success": True, "final_scores": []})
    monkeypatch.setattr(judge_events_4x.RoomStateManager, "end_game", end_game)

    transition_round_state = AsyncMock(return_value=True)
    monkeypatch.setattr(judge_events_4x.RoundStateManager, "transition_round_state",
                        transition_round_state)

    await judge_events_4x.handle_judge_submit(
        data=message,
        clients=clients,
        client=client,
        room_id=room_id,
    )

    transition_round_state.assert_awaited_once()
    assert transition_round_state.await_args.kwargs["room_id"] == room_id
    assert transition_round_state.await_args.kwargs["target"] == RoundState.COMPLETED

    # 不应触发游戏结束（因为当前不是最后一首）
    assert end_game.await_count == 0

    # 应广播 ROUND_STATE_UPDATE(COMPLETED)
    state_update_payloads = [
        call.args[1]
        for call in clients.broadcast.await_args_list
        if isinstance(call.args[1], dict) and call.args[1].get("event") == 45
    ]
    assert len(state_update_payloads) == 1
    assert state_update_payloads[0]["data"]["round_state_name"] == "COMPLETED"
