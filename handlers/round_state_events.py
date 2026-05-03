"""Round state related WebSocket handlers."""

from __future__ import annotations

from cache.room_state_manager import RoundStateManager
from client_manager import Client, ClientManager
from db.session import session_scope
from schemas.ws_messages import RoundStateUpdateMessage, RoundStateUpdateData
from utils.enumerations import GameEventType, RoundState
from utils import get_logger

logger = get_logger(__name__)


def build_round_state_update_message(
        target_round_state: RoundState) -> RoundStateUpdateMessage:
    """构建回合状态更新消息"""
    round_state_update_data = RoundStateUpdateData(
        round_state=target_round_state.value,
        round_state_name=target_round_state.name,
    )
    return RoundStateUpdateMessage(data=round_state_update_data)


async def handle_round_state_transition(
    clients: ClientManager,
    client: Client,
    room_id: str,
    target_round_state: RoundState,
) -> None:
    """处理回合状态转换

    Args:
        clients: 客户端管理器
        client: 发起状态转换的客户端
        room_id: 房间ID
        target_round_state: 目标回合状态
    """
    try:
        async with session_scope() as session:
            success = await RoundStateManager.transition_round_state(
                room_id=room_id,
                target=target_round_state,
                session=session,
            )

            if success:
                # 广播状态更新消息给所有客户端
                round_state_update_message = build_round_state_update_message(
                    target_round_state)
                await clients.broadcast(room_id,
                                        round_state_update_message.model_dump())

                logger.info(
                    "Room %s round state transitioned to %s",
                    room_id,
                    target_round_state.name,
                )
            else:
                logger.error("Failed to transition round state for room %s", room_id)
                # 发送错误消息给客户端
                await client.send_error(
                    GameEventType.ROUND_STATE_UPDATE,
                    f"Failed to transition to {target_round_state.name}",
                )
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error handling round state transition: %s", e, exc_info=True)
        # 发送错误消息给客户端
        await client.send_error(GameEventType.ROUND_STATE_UPDATE,
                                "Error during state transition")


async def handle_round_state_update(
    data: dict,
    clients: ClientManager,
    client: Client,
    room_id: str,
) -> None:
    """处理回合状态更新事件

    Args:
        data: 事件数据
        clients: 客户端管理器
        client: 客户端
        room_id: 房间ID
    """
    try:
        # 验证客户端是否为房主
        if not client.user.is_owner:
            logger.warning("Non-owner client %s tried to update round state",
                           client.user.username)
            await client.send_error(
                GameEventType.ROUND_STATE_UPDATE,
                "Only room owner can update round state",
            )
            return

        # 解析目标状态
        target_state_value = data.get("round_state")
        if target_state_value is None:
            await client.send_error(GameEventType.ROUND_STATE_UPDATE,
                                    "Missing round_state in request")
            return

        try:
            target_round_state = RoundState(target_state_value)
        except ValueError:
            await client.send_error(
                GameEventType.ROUND_STATE_UPDATE,
                f"Invalid round state: {target_state_value}",
            )
            return

        # 处理状态转换
        await handle_round_state_transition(clients, client, room_id,
                                            target_round_state)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error handling round state update: %s", e, exc_info=True)
        await client.send_error(
            GameEventType.ROUND_STATE_UPDATE,
            "Error handling round state update",
        )
