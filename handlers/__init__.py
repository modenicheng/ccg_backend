from typing import Callable
import inspect

from utils import get_logger
from utils.enumerations import EventType

_handlers: dict[str, Callable] = {}
logger = get_logger(__name__)


def regist(event: EventType):

    def decorator(func: Callable):
        _handlers[event.name] = func
        return func

    return decorator


async def handle(event: EventType, data: bytes, clients=None, websocket=None):
    logger.debug("Current handlers: %s", list(_handlers.keys()))
    if event.name in _handlers:
        result = _handlers[event.name](data,
                                       clients=clients,
                                       websocket=websocket)
        if inspect.isawaitable(result):
            return await result
        return result
    else:
        logger.error("No handler for event type: %s", event.name)
        raise ValueError(f"No handler for event type: {event.name}")


# Import handler modules to trigger decorator registration.
from . import heartbeats  # noqa: E402,F401
