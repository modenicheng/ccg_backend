"""WebSocket event handler registration and dispatch."""
# Import handler modules to trigger decorator registration.
from . import heartbeats, audio_events_2x, round_events_3x  # noqa: E402,F401  # pylint: disable=wrong-import-position

# from . import game_events  # noqa: E402,F401
# from . import judge_events  # noqa: E402,F401
