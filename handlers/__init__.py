from typing import Any, Callable, TypeAlias, cast
import inspect

from pydantic import BaseModel

from client_manager import Client, ClientManager
from utils import get_logger
from utils.enumerations import EventType, GameEventType

DataValidator: TypeAlias = type[BaseModel] | type[bytes]
_handlers: dict[str, tuple[Callable[..., Any], DataValidator]] = {}
logger = get_logger(__name__)


def regist(event: EventType | GameEventType,
           data_validator: DataValidator = bytes):

    def decorator(func: Callable[..., Any]):
        _handlers[event.name] = (func, data_validator)
        return func

    return decorator


async def handle(event: EventType | GameEventType,
                 data: bytes | dict | str,
                 clients: ClientManager,
                 client: Client,
                 room_id: str | None = None,
                 **kwargs):
    logger.debug("Handling event: %s with data type: %s", event.name,
                 type(data).__name__)
    if event.name in _handlers:
        handler, data_validator = _handlers[event.name]
        parsed_data: Any = data
        if data_validator is not bytes:
            try:
                parsed_data = cast(type[BaseModel],
                                   data_validator).model_validate(data)
            except Exception:
                logger.warning("Failed to parse data for event %s", event.name)
                raise ValueError(
                    f"Failed to parse data for event {event.name}")
        result = handler(parsed_data,
                         clients=clients,
                         client=client,
                         room_id=room_id,
                         **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
    else:
        logger.error("No handler for event type: %s", event.name)
        raise ValueError(f"No handler for event type: {event.name}")


# Import handler modules to trigger decorator registration.
from . import heartbeats  # noqa: E402,F401
# from . import game_events  # noqa: E402,F401
from . import audio_events_2x  # noqa: E402,F401
from . import round_events_3x  # noqa: E402,F401
# from . import judge_events  # noqa: E402,F401
