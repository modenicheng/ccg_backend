"""WebSocket event handler registration and dispatch."""

# Import handler modules to trigger decorator registration.
from utils.enumerations import GameEventType
from .registe_manager import regist

from . import (
    heartbeats as _heartbeats,
    audio_events_2x as _audio_events_2x,
    round_events_3x as _round_events_3x,
    judge_events_4x as _judge_events_4x,
    round_state_events,
    connection_lifespan as _connection_lifespan,
)  # noqa: E402,F401  # pylint: disable=wrong-import-position

# Keep module references alive so static analyzers do not flag the registration-only imports as unused.
_HANDLER_MODULES = (
    _heartbeats,
    _audio_events_2x,
    _round_events_3x,
    _judge_events_4x,
    _connection_lifespan,
)

# from . import game_events  # noqa: E402,F401
# from . import judge_events  # noqa: E402,F401

# 注册回合状态更新事件处理器

regist(GameEventType.ROUND_STATE_UPDATE)(round_state_events.handle_round_state_update)
