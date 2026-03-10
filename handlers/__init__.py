"""WebSocket event handler registration and dispatch."""

# Import handler modules to trigger decorator registration.
from utils.enumerations import GameEventType

from . import (
    heartbeats,
    audio_events_2x,
    round_events_3x,
    judge_events_4x,
    round_state_events,
    connection_lifespan,
)  # noqa: E402,F401  # pylint: disable=wrong-import-position

# from . import game_events  # noqa: E402,F401
# from . import judge_events  # noqa: E402,F401

from .registe_manager import regist

# 注册回合状态更新事件处理器

regist(GameEventType.ROUND_STATE_UPDATE)(round_state_events.handle_round_state_update)
