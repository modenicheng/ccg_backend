from fastapi import WebSocket
from pydantic import ValidationError
from typing import Any

from cache.utils import room_manager
from client_manager import ClientManager
from schemas.game_events import PauseMessage, PlayMessage, SeekMessage, JudgingMessage
from schemas.song import WebSocketErrorResponse
from utils import get_logger
from utils.enumerations import GameEventType

from . import regist

logger = get_logger(__name__)


def _ensure_owner(websocket: WebSocket | None) -> bool:
    if websocket is None:
        return False
    user = getattr(websocket.state, "user", None)
    return bool(user and getattr(user, "is_owner", False))


def _build_error(event: GameEventType, reason: str) -> WebSocketErrorResponse:
    return WebSocketErrorResponse(
        type="error",
        event=event.value,
        reason=reason,
    )


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
    await clients.send(websocket, _build_error(event, reason).model_dump())


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


@regist(GameEventType.JUDGING)
async def handle_judging(data,
                        clients=None,
                        websocket=None,
                        room_id=None,
                        **kwargs):
    if not isinstance(data, dict):
        await _safe_send_error(clients, websocket, GameEventType.JUDGING,
                               "Expected JSON object")
        return
    
    if websocket is None or room_id is None:
        return
    
    try:
        payload = JudgingMessage.model_validate(data)
    except ValidationError as exc:
        logger.warning("Invalid JUDGING payload: %s", exc)
        await _safe_send_error(clients, websocket, GameEventType.JUDGING,
                               f"Invalid payload: {exc.errors()}")
        return
    
    # 广播 JUDGING 事件给所有客户端
    await _safe_broadcast(clients, room_id, payload.model_dump(), websocket)


@regist(GameEventType.JUDGE_SUBMIT)
async def handle_judge_submit(data,
                             clients=None,
                             websocket=None,
                             room_id=None,
                             **kwargs):
    if not isinstance(data, dict):
        await _safe_send_error(clients, websocket, GameEventType.JUDGE_SUBMIT,
                               "Expected JSON object")
        return
    
    if not _ensure_owner(websocket):
        await _safe_send_error(clients, websocket, GameEventType.JUDGE_SUBMIT,
                               "Only owner can submit judge result")
        return
    
    try:
        payload = JudgeSubmitMessage.model_validate(data)
    except ValidationError as exc:
        logger.warning("Invalid JUDGE_SUBMIT payload: %s", exc)
        await _safe_send_error(clients, websocket, GameEventType.JUDGE_SUBMIT,
                               f"Invalid payload: {exc.errors()}")
        return
    
    # 获取房间信息和玩家答案
    room_info = await room_manager.get_room_info(room_id)
    if not room_info:
        await _safe_send_error(clients, websocket, GameEventType.JUDGE_SUBMIT,
                               "Room not found")
        return
    
    # 如果选择跳过计分，直接结束
    if payload.data.skip_scoring:
        logger.info("Skipping scoring for room %s", room_id)
        return
    
    # 模拟玩家答案和抢答顺序（实际应该从Redis或数据库中获取）
    # 这里需要根据实际的房间状态获取玩家答案和抢答顺序
    # 以下为示例数据
    players = room_info.get('players', [])
    answer_queue = room_info.get('answer_queue', [])
    player_answers = room_info.get('answers', {})
    
    # 初始化玩家得分
    player_scores = {player['id']: 0 for player in players}
    
    # 处理标签组计分
    correct_tags = payload.data.correct_tags
    tag_group_map = {}  # 假设这里有标签组映射
    
    # 按抢答顺序遍历玩家
    for player_id in answer_queue:
        if player_id not in player_answers:
            continue
        
        player_answer = player_answers[player_id]
        selected_tags = player_answer.get('tags', [])
        
        # 检查每个标签组
        for tag_group_id, group_tags in tag_group_map.items():
            # 找到该标签组的正确答案
            group_correct_tags = [tag for tag in correct_tags if tag in group_tags]
            if not group_correct_tags:
                continue
            
            # 检查玩家是否选择了该标签组的正确答案
            player_group_tags = [tag for tag in selected_tags if tag in group_tags]
            if player_group_tags == group_correct_tags:
                # 给玩家加1分
                player_scores[player_id] += 1
                # 从正确标签中移除该标签组的标签，避免重复计分
                for tag in group_correct_tags:
                    if tag in correct_tags:
                        correct_tags.remove(tag)
                # 终止当前标签组的遍历
                break
    
    # 处理精准描述计分
    correct_description_ids = payload.data.correct_description_ids
    if correct_description_ids:
        for player_id in answer_queue:
            if player_id not in player_answers:
                continue
            
            player_answer = player_answers[player_id]
            player_description_id = player_answer.get('description_id')
            
            if player_description_id in correct_description_ids:
                # 给玩家加1分
                player_scores[player_id] += 1
                # 终止精准描述的遍历
                break
    
    # 更新排行榜
    # 这里需要将得分更新到数据库或Redis中
    # 然后生成排行榜数据
    score_update_data = {
        "scores": [
            {
                "player_id": player_id,
                "username": next((p['username'] for p in players if p['id'] == player_id), f"Player {player_id}"),
                "score": player_scores.get(player_id, 0)
            }
            for player_id in player_scores
        ]
    }
    
    # 广播得分更新事件
    score_update_message = ScoreUpdateMessage(data=score_update_data)
    await _safe_broadcast(clients, room_id, score_update_message.model_dump(), websocket)
    
    logger.info("Scoring completed for room %s", room_id)
