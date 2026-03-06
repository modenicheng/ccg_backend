"""
Event handler registration and dispatch manager.
This module provides a decorator-based system for registering and dispatching
event handlers for WebSocket events. Handlers can optionally validate incoming
data using Pydantic models before processing.
The module maintains a registry of event handlers and their associated data
validators, and provides a dispatch function to route events to the appropriate
handler with automatic data validation and async support.
Attributes:
    DataValidator: Type alias for valid data validators (Pydantic BaseModel or bytes).
    _handlers: Internal registry mapping event names to handler functions and validators.
    logger: Logger instance for debug and error messages.
Functions:
    regist: Decorator to register a handler function for a specific event type.
    handle: Async dispatch function to route events to registered handlers with validation.
"""

from __future__ import annotations
from typing import Any, Callable, TypeAlias, cast
import inspect

from pydantic import BaseModel

from client_manager import Client, ClientManager
from utils import get_logger
from utils.enumerations import EventType, GameEventType

DataValidator: TypeAlias = type[BaseModel] | type[bytes]
_handlers: dict[str, tuple[Callable[..., Any], DataValidator]] = {}
logger = get_logger(__name__)


def regist(
    event: EventType | GameEventType,
    data_validator: DataValidator = bytes
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to register a handler for a specific event type."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        _handlers[event.name] = (func, data_validator)
        return func

    return decorator


async def handle(
    event: EventType | GameEventType,
    data: bytes | dict | str,
    clients: ClientManager,
    client: Client,
    room_id: str | None = None,
    **kwargs,
) -> Any:
    """Dispatch an incoming WebSocket event to the registered handler."""
    logger.debug("Handling event: %s with data type: %s", event.name,
                 type(data).__name__)
    if event.name not in _handlers:
        logger.error("No handler for event type: %s", event.name)
        raise ValueError(f"No handler for event type: {event.name}")

    handler, data_validator = _handlers[event.name]
    parsed_data: Any = data
    if data_validator is not bytes:
        try:
            parsed_data = cast(type[BaseModel], data_validator).model_validate(data)
        except Exception as exc:
            logger.warning("Failed to parse data for event %s", event.name)
            raise ValueError(f"Failed to parse data for event {event.name}") from exc
    result = handler(parsed_data,
                     clients=clients,
                     client=client,
                     room_id=room_id,
                     **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result
