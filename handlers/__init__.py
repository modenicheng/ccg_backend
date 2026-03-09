"""WebSocket event handler registration and dispatch."""
# Import handler modules to trigger decorator registration.
from . import heartbeats, audio_events_2x, round_events_3x, round_state_events, connection_lifespan  # noqa: E402,F401  # pylint: disable=wrong-import-position

# from . import game_events  # noqa: E402,F401
# from . import judge_events  # noqa: E402,F401

# 注册回合状态更新事件处理器
from .registe_manager import regist
from utils.enumerations import GameEventType

regist(GameEventType.ROUND_STATE_UPDATE)(round_state_events.handle_round_state_update)
