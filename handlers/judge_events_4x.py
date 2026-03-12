"""WebSocket judge event handlers."""

from __future__ import annotations

import random

from sqlalchemy import select

import cache.schemas as cache_schemas
from cache import room_cache
from cache.room_state_manager import RoundStateManager
from client_manager import ClientManager, Client
from db import models
from db.crud import (
    fetch_room_object,
    get_current_song_info,
    get_or_create_audio_token,
    get_player_answers_for_judging,
    get_room_song_queue,
    get_tag_group_map,
    save_score_record,
    update_player_answer_order,
    update_room_current_song_index,
)
from db.session import session_scope
from handlers.audio_common import preload_songs_for_round_start
from handlers.registe_manager import regist
from handlers.round_state_events import build_round_state_update_message
from schemas.ws_messages.judge_schemas import (
    JudgeSubmitMessage,
    JudgingData,
    JudgingMessage,
    ScoreEntry,
    ScoreUpdateData,
    ScoreUpdateMessage,
    SongInfo,
)
from schemas.ws_messages.round_event_schemas import (
    RoundEndMessage,
    RoundStartData,
    RoundStartMessage,
)
from utils import get_logger
from utils.audio_token import get_audio_stream_url
from utils.calculate import calculate_player_scores
from utils.enumerations import GameEventType, RoundState

logger = get_logger(__name__)


@regist(GameEventType.JUDGING, JudgingMessage)
async def handle_judging(data: JudgingMessage, clients: ClientManager, client: Client,
                         room_id: str, **kwargs) -> None:
    """处理进入判分环节事件"""
    # pylint: disable=unused-argument,too-many-locals
    try:
        async with session_scope() as db:
            # 获取当前歌曲信息
            song_id, song_index = await get_current_song_info(db, room_id)
            if song_id is None or song_index is None:
                await client.send_error(GameEventType.JUDGING,
                                        "Cannot determine current song")
                return

            # 获取当前歌曲
            song_stmt = select(models.Song).where(models.Song.id == song_id)
            song_result = await db.execute(song_stmt)
            song = song_result.scalar_one_or_none()
            if not song:
                await client.send_error(GameEventType.JUDGING, "Song not found")
                return

            # 获取历史标签
            tag_history_stmt = select(models.SongTagHistory.tag_id).where(
                models.SongTagHistory.song_id == song_id)
            tag_history_result = await db.execute(tag_history_stmt)
            history_tag_ids = [tag_id[0] for tag_id in tag_history_result.all()]

            # 获取参考精确描述（正确答案）
            description_history_stmt = select(
                models.SongDescriptionHistory.description_text).where(
                    models.SongDescriptionHistory.song_id == song_id,
                    models.SongDescriptionHistory.is_correct == True,  # pylint: disable=singleton-comparison
                )
            description_history_result = await db.execute(description_history_stmt)
            reference_descriptions = [
                desc[0] for desc in description_history_result.all()
            ]

            # 随机选择一个或多个参考描述
            if reference_descriptions:
                # 随机选择1-3个描述
                num_descriptions = min(random.randint(1, 3),
                                       len(reference_descriptions))
                reference_descriptions = random.sample(reference_descriptions,
                                                       num_descriptions)

            # 获取玩家答案（用于显示抢答者的精确描述）
            player_answers = await get_player_answers_for_judging(
                db, room_id, song_id, song_index)

            if not player_answers:
                logger.warning(
                    "No player answers found for judging in room %s, song %s",
                    room_id,
                    song_id,
                )

            # 构建玩家描述列表
            player_descriptions = []
            for user_id, answer_data in player_answers.items():
                if answer_data["description_text"]:
                    # 获取用户名
                    user_stmt = select(
                        models.User.username).where(models.User.id == user_id)
                    user_result = await db.execute(user_stmt)
                    username = user_result.scalar_one_or_none() or f"Player {user_id}"

                    player_descriptions.append({
                        "id": user_id,
                        "username": username,
                        "description": answer_data["description_text"],
                    })

            # 构建歌曲信息
            song_info = SongInfo(
                title=song.title,
                artist=song.artist,
                album=song.album_name,
                cover_url=song.cover_url,
                platform_url=song.metadata_json.get("platform_url")
                if song.metadata_json else None,
            )

            # 构建JUDGING事件数据
            judging_data = JudgingData(
                song=song_info,
                history_tag_ids=history_tag_ids,
                reference_descriptions=reference_descriptions,
                player_descriptions=player_descriptions,
            )

            # 触发状态转换到 JUDGING
            success = await RoundStateManager.transition_round_state(
                room_id=room_id,
                target=RoundState.JUDGING,
                session=db,
            )
            if success:
                await clients.broadcast(
                    room_id,
                    build_round_state_update_message(RoundState.JUDGING).model_dump(),
                )
                logger.info("Room %s round state transitioned to JUDGING", room_id)

            # 广播JUDGING事件给所有客户端
            judging_message = JudgingMessage(data=judging_data)
            await clients.broadcast(room_id, judging_message.model_dump())

            logger.info("Sent JUDGING event for room %s, song %s", room_id, song.title)

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error during JUDGING event: %s", e)
        await client.send_error(GameEventType.JUDGING,
                                f"Internal server error: {str(e)}")


@regist(GameEventType.JUDGE_SUBMIT, JudgeSubmitMessage)
async def handle_judge_submit(  # pylint: disable=too-many-return-statements
    data: JudgeSubmitMessage,
    clients: ClientManager,
    client: Client,
    room_id: str,
    **kwargs,
) -> None:
    """处理房主提交正确答案事件"""
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements,unused-argument
    # 验证房主身份
    if not client.user.is_owner:
        await client.send_error(GameEventType.JUDGE_SUBMIT,
                                "Only owner can submit judge result")
        return

    # 如果选择跳过计分，直接结束
    if data.data.skip_scoring:
        logger.info("Skipping scoring for room %s", room_id)
        try:
            async with session_scope() as session:
                success = await RoundStateManager.transition_round_state(
                    room_id=room_id,
                    target=RoundState.COMPLETED,
                    session=session,
                )
                if success:
                    await clients.broadcast(
                        room_id,
                        build_round_state_update_message(
                            RoundState.COMPLETED).model_dump(),
                    )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Failed to transition to COMPLETED when skip scoring: %s", e)
        # 广播ROUND_END事件
        round_end_message = RoundEndMessage()
        await clients.broadcast(room_id, round_end_message.model_dump())
        return

    # 使用数据库会话获取数据
    try:
        async with session_scope() as db:
            # 检查房间是否存在
            room = await fetch_room_object(db, room_id)
            if not room:
                await client.send_error(GameEventType.JUDGE_SUBMIT, "Room not found")
                return

            # 获取当前歌曲信息
            song_id, song_index = await get_current_song_info(db, room_id)
            if song_id is None or song_index is None:
                await client.send_error(GameEventType.JUDGE_SUBMIT,
                                        "Cannot determine current song")
                return

            # 存储标签历史
            for tag_id in data.data.correct_tags:
                # 检查是否已存在
                existing_stmt = select(models.SongTagHistory).where(
                    models.SongTagHistory.song_id == song_id,
                    models.SongTagHistory.tag_id == tag_id,
                )
                existing_result = await db.execute(existing_stmt)
                existing = existing_result.scalar_one_or_none()

                if not existing:
                    # 创建新记录
                    tag_history = models.SongTagHistory(
                        song_id=song_id,
                        tag_id=tag_id,
                        judged_by_user_id=client.user.id,
                        room_id=room_id,
                    )
                    db.add(tag_history)

            # 存储描述历史
            for description_id in data.data.correct_description_ids:
                # 获取玩家答案
                answer_stmt = select(models.PlayerAnswer).where(
                    models.PlayerAnswer.room_id == room_id,
                    models.PlayerAnswer.song_id == song_id,
                    models.PlayerAnswer.round_index == song_index,
                    models.PlayerAnswer.user_id == description_id,
                )
                answer_result = await db.execute(answer_stmt)
                answer = answer_result.scalar_one_or_none()

                if answer and answer.description_text:
                    # 创建新记录
                    description_history = models.SongDescriptionHistory(
                        song_id=song_id,
                        description_text=answer.description_text,
                        is_correct=True,
                        judged_by_user_id=client.user.id,
                        room_id=room_id,
                    )
                    db.add(description_history)

            # 处理新的正确描述
            for description_text in data.data.new_correct_descriptions:
                if description_text.strip():
                    description_history = models.SongDescriptionHistory(
                        song_id=song_id,
                        description_text=description_text.strip(),
                        is_correct=True,
                        judged_by_user_id=client.user.id,
                        room_id=room_id,
                    )
                    db.add(description_history)

            # 获取房间玩家
            players = [{
                "id": str(user.id),
                "username": user.username
            } for user in room.users]

            # 获取抢答队列（从Redis暂时获取，后续可能需要移到数据库）
            answer_queue_items = await room_cache.get_answer_queue(
                room_id)  # 已排序的 AnswerQueueItem 列表
            answer_queue = [str(item.player_id) for item in answer_queue_items
                            ]  # 转换为玩家ID字符串列表

            if not answer_queue:
                logger.warning("Empty answer queue for judging in room %s", room_id)

            # 从数据库获取玩家答案
            player_answers_raw = await get_player_answers_for_judging(
                db, room_id, song_id, song_index)

            if not player_answers_raw:
                logger.warning(
                    "No player answers found for judging in room %s, song %s",
                    room_id,
                    song_id,
                )

            # 转换格式以便与answer_queue匹配（answer_queue中的player_id是字符串）
            player_answers = {}
            for user_id_int, answer_data in player_answers_raw.items():
                player_id_str = str(user_id_int)
                player_answers[player_id_str] = {
                    "selected_tag_ids": answer_data["selected_tag_ids"],
                    "description_text": answer_data["description_text"],
                    "answer_order": answer_data["answer_order"],
                }

            # 获取标签组映射
            tag_group_map = await get_tag_group_map(db, room_id)

            # 计算玩家得分
            player_scores = calculate_player_scores(
                answer_queue=answer_queue,
                player_answers=player_answers,
                tag_group_map=tag_group_map,
                correct_tags=data.data.correct_tags.copy(),
                correct_description_ids=data.data.correct_description_ids,
            )
            # 确保所有房间玩家都有得分记录（即使为0）
            for player in players:
                if player["id"] not in player_scores:
                    player_scores[player["id"]] = 0

            # 更新数据库中的抢答顺序（answer_order）
            updated_count = await update_player_answer_order(db, room_id, song_id,
                                                             song_index, answer_queue)
            logger.info("Updated answer_order for %d players in room %s", updated_count,
                        room_id)

            # 保存得分记录到数据库
            for player_id_str, score_delta in player_scores.items():
                if score_delta > 0:
                    try:
                        user_id = int(player_id_str)
                        await save_score_record(db, room_id, user_id, song_index,
                                                score_delta)
                    except (ValueError, Exception) as e:  # pylint: disable=broad-exception-caught
                        logger.error("Failed to save score for player %s: %s",
                                     player_id_str, e)

            logger.info("Saved scoring results to database for room %s", room_id)

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error during judge submission for room %s: %s", room_id, e)
        await client.send_error(GameEventType.JUDGE_SUBMIT.value,
                                f"Internal server error: {str(e)}")
        return

    # 计分完成，清空抢答队列（Redis部分）
    await room_cache.clear_answer_queue(room_id)

    # 构建得分更新消息
    score_entries = [
        ScoreEntry(
            player_id=player_id,
            username=next(
                (p["username"] for p in players if p["id"] == player_id),
                f"Player {player_id}",
            ),
            score=player_scores.get(player_id, 0),
        ) for player_id in player_scores
    ]

    score_update_data = ScoreUpdateData(scores=score_entries)
    score_update_message = ScoreUpdateMessage(data=score_update_data)

    # 广播得分更新事件
    await clients.broadcast(room_id, score_update_message.model_dump())

    # 触发状态转换到 COMPLETED
    try:
        async with session_scope() as session:
            success = await RoundStateManager.transition_round_state(
                room_id=room_id,
                target=RoundState.COMPLETED,
                session=session,
            )
            if success:
                await clients.broadcast(
                    room_id,
                    build_round_state_update_message(RoundState.COMPLETED).model_dump(),
                )
                logger.info("Room %s round state transitioned to COMPLETED", room_id)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Failed to transition to COMPLETED: %s", e)

    # 广播回合结束事件
    round_end_message = RoundEndMessage()
    await clients.broadcast(room_id, round_end_message.model_dump())

    # 检查并开始下一轮
    try:
        async with session_scope() as session:
            # 获取房间和歌曲队列
            room_stmt = select(models.Room).where(models.Room.id == room_id)
            room_result = await session.execute(room_stmt)
            room = room_result.scalar_one_or_none()

            if not room:
                logger.warning("Cannot start next round: room %s not found", room_id)
                return

            # 获取歌曲队列
            song_queue = await get_room_song_queue(session, room_id)
            if not song_queue:
                logger.info("No songs in room %s, game ended", room_id)
                return

            current_index = room.current_song_index or 0
            next_index = current_index + 1

            # 检查是否还有更多歌曲
            if next_index >= len(song_queue):
                logger.info("No more songs in room %s, game completed", room_id)
                return

            # 更新当前歌曲索引
            await update_room_current_song_index(session, room_id, next_index)

            # 触发预下载和预加载逻辑
            # 预下载：下载 i+3 歌曲（i = next_index）
            # 预加载：广播 PRELOAD_AUDIO 给 i+1 歌曲
            await preload_songs_for_round_start(
                clients=clients,
                session=session,
                room_id=room_id,
                song_queue=song_queue,
                current_index=next_index,
            )

            # 为当前轮次歌曲获取或创建token
            current_song_id = song_queue[next_index]
            audio_token = await get_or_create_audio_token(session, room_id,
                                                          current_song_id)
            audio_url = get_audio_stream_url(audio_token)

            # 强制新回合播放状态为 playing（避免沿用上一轮暂停状态）
            playback_state = cache_schemas.PlaybackState(
                play_state="playing",
                progress_ms=0,
                offset_ts=0,
                audio_url=audio_url,
            )
            await room_cache.set_room_playback_state(room_id, playback_state)

            # 转换状态流：COMPLETED -> PENDING -> PLAYING_AUDIO
            reset_success = await RoundStateManager.transition_round_state(
                room_id=room_id,
                target=RoundState.PENDING,
                session=session,
            )
            if reset_success:
                await clients.broadcast(
                    room_id,
                    build_round_state_update_message(RoundState.PENDING).model_dump(),
                )

            playing_success = await RoundStateManager.transition_round_state(
                room_id=room_id,
                target=RoundState.PLAYING_AUDIO,
                session=session,
            )
            if playing_success:
                await clients.broadcast(
                    room_id,
                    build_round_state_update_message(
                        RoundState.PLAYING_AUDIO).model_dump(),
                )

            # 广播新一轮开始事件
            round_start_data = RoundStartData(
                round_index=next_index,
                audio_url=audio_url,
                start_percent=0.0,
            )
            round_start_message = RoundStartMessage(data=round_start_data)
            await clients.broadcast(room_id, round_start_message.model_dump())

            logger.info("Started next round %s in room %s", next_index, room_id)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Failed to start next round for room %s: %s", room_id, e)

    logger.info("Scoring completed for room %s", room_id)
