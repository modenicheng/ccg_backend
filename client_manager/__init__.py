# pylint: disable=missing-module-docstring,pointless-string-statement
from __future__ import annotations
import asyncio
from enum import Enum
from fastapi import WebSocket
from db.models import Room, User
from utils import get_logger
from schemas.base_message import ErrorMessage, ErrorMessageData
"""WebSocket client management module."""
logger = get_logger(__name__)


class Client:
    """WebSocket 客户端包装类，提供额外的属性和方法"""

    def __init__(self, websocket: WebSocket, user: User, room: Room):
        self.ws: WebSocket = websocket
        self.user: User = user
        self.room: Room = room
        # 缓存用户 ID 以避免 Session 分离问题
        self.user_id: int = user.id
        self.username: str = user.username
        self.is_owner: bool = user.is_owner
        self.is_spectator: bool = getattr(user, 'is_spectator', False)

    async def send(self, message: str | bytes | dict):
        """Send message to client.

        Args:
            message: Message to send, can be bytes, string, or dict
        """
        try:
            if isinstance(message, bytes):
                await self.ws.send_bytes(message)
            elif isinstance(message, str):
                await self.ws.send_text(message)
            elif isinstance(message, dict):
                await self.ws.send_json(message)
            else:
                raise TypeError(f"Unsupported message type: {type(message)}")
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(
                "Failed to send message to client %s<%s>: %s",
                self.user.username,
                self.user.id,
                e,
                exc_info=True,
            )

    async def send_error(self, event_type: Enum | int, message: str):
        """Send error message to client.

        Args:
            event_type: Event type as Enum or integer
            message: Error message text
        """
        event: int
        if isinstance(event_type, Enum):
            event = event_type.value
        elif isinstance(event_type, int):
            event = event_type
        else:
            raise TypeError(f"Unsupported event_type type: {type(event_type)}")
        error_message = ErrorMessage(event=255,
                                     data=ErrorMessageData(message=message,
                                                           error_event=event))
        await self.send(error_message.model_dump())


class ClientManager:
    """Manager for WebSocket clients organized by rooms."""
    _rooms: dict[str, set[Client]]

    def __init__(self):
        self._rooms = {}

    def push(self, room_id: str, client: Client):
        """Add client to room.

        Args:
            room_id: Room identifier
            client: Client instance
        """
        if room_id not in self._rooms:
            self._rooms[room_id] = set()
        self._rooms[room_id].add(client)

    def pop(self, room_id: str, client: Client):
        """Remove client from room.

        Args:
            room_id: Room identifier
            client: Client instance
        """
        clients = self._rooms.get(room_id)
        if not clients:
            return
        clients.discard(client)
        if not clients:
            self._rooms.pop(room_id, None)

    def get_clients(self, room_id: str) -> set[Client]:
        """Get all clients in a room.

        Args:
            room_id: Room identifier

        Returns:
            Set of clients in the room
        """
        return self._rooms.get(room_id, set())

    def has_user_connection(self, room_id: str, user_id: int) -> bool:
        """Check whether a user already has an active connection in a room.

        Args:
            room_id: Room identifier
            user_id: User identifier

        Returns:
            True if at least one active connection exists for the user in this room
        """
        return any(client.user.id == user_id and not client.is_spectator
                   for client in self._rooms.get(room_id, set()))

    def get_all_clients(self) -> set[Client]:
        """Get all clients across all rooms.

        Returns:
            Set of all clients
        """
        all_clients: set[Client] = set()
        for clients in self._rooms.values():
            all_clients.update(clients)
        return all_clients

    def get_room_snapshot(self) -> dict[str, set[Client]]:
        """获取房间-连接快照（用于遍历，避免直接操作内部引用）。"""
        return {room_id: set(clients) for room_id, clients in self._rooms.items()}

    async def clear(self, room_id: str | None = None):
        """Clear clients from room(s).

        Args:
            room_id: Room identifier or None to clear all rooms
        """
        if room_id is None:
            results = await asyncio.gather(
                *[
                    client.ws.close(code=1000, reason="Server shutdown")
                    for clients in self._rooms.values()
                    for client in clients
                ],
                return_exceptions=True,
            )
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error(
                        "Exception occurred in asyncio.gather task %d during clear(all): %s",
                        i,
                        r,
                        exc_info=r,
                    )
            logger.debug(results)
            self._rooms.clear()
            return
        room = self._rooms.pop(room_id, None)
        if room:
            results = await asyncio.gather(
                *[
                    client.ws.close(code=1000, reason="Server shutdown")
                    for client in room
                ],
                return_exceptions=True,
            )
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error(
                        "Exception occurred in asyncio.gather task %d during clear(room %s): %s",
                        i,
                        room_id,
                        r,
                        exc_info=r,
                    )

    def is_empty(self, room_id: str | None = None) -> bool:
        """Check if room or all rooms are empty.

        Args:
            room_id: Room identifier or None to check all rooms

        Returns:
            True if empty, False otherwise
        """
        if room_id is None:
            return len(self._rooms) == 0
        return len(self._rooms.get(room_id, set())) == 0

    async def send(self, client: Client, message: str | bytes | dict):
        """Send message to client.

        Args:
            client: Client instance
            message: Message to send
        """
        try:
            if isinstance(message, bytes):
                await client.ws.send_bytes(message)
            elif isinstance(message, str):
                await client.ws.send_text(message)
            elif isinstance(message, dict):
                await client.ws.send_json(message)
            else:
                raise TypeError(f"Unsupported message type: {type(message)}")
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(
                "Failed to send message to client %s<%s>: %s",
                client.user.username,
                client.user.id,
                e,
                exc_info=True,
            )

    async def broadcast_error(
        self,
        room_id: str,
        event_type: Enum | int,
        message: str,
        excluded_clients: set[Client] | None = None,
    ):
        """Broadcast error message to all clients in room.

        Args:
            room_id: Room identifier
            event_type: Error event type as Enum or integer
            message: Error message text
            excluded_clients: Clients to exclude from broadcast
        """
        event: int
        if isinstance(event_type, Enum):
            event = event_type.value
        elif isinstance(event_type, int):
            event = event_type
        else:
            raise TypeError(f"Unsupported event_type type: {type(event_type)}")
        error_message = ErrorMessage(event=255,
                                     data=ErrorMessageData(message=message,
                                                           error_event=event))
        await self.broadcast(room_id,
                             error_message.model_dump(),
                             excluded_clients=excluded_clients)

    async def broadcast(
        self,
        room_id: str,
        message: str | bytes | dict,
        excluded_clients: set[Client] | None = None,
    ):
        """Broadcast message to all clients in room.

        Args:
            room_id: Room identifier
            message: Message to broadcast
            excluded_clients: Clients to exclude from broadcast
        """
        excluded_clients = excluded_clients or set()
        clients = self._rooms.get(room_id, set())
        results = await asyncio.gather(
            *[
                self.send(client, message)
                for client in clients
                if client not in excluded_clients
            ],
            return_exceptions=True,
        )
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.error(
                    "Exception occurred in asyncio.gather task %d during broadcast in room %s: %s",
                    i,
                    room_id,
                    r,
                    exc_info=r,
                )

    async def kick(
        self,
        room_id: str,
        client: Client,
        code: int = 1000,
        reason: str = "Kicked by server",
    ):
        """Kick client from room and close connection.

        Args:
            room_id: Room identifier
            client: Client to kick
            code: WebSocket close code
            reason: Close reason
        """
        try:
            await client.ws.close(code=code, reason=reason)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Failed to kick client %s<%s>: %s", client.user.username,
                         client.user.id, e)
        finally:
            self.pop(room_id, client)
            del client

    def room_size(self, room_id: str) -> int:
        """Get number of clients in a room.

        Args:
            room_id: Room identifier

        Returns:
            Number of clients in the room
        """
        return len(self._rooms.get(room_id, set()))

    def total_size(self) -> int:
        """Get total number of clients across all rooms.

        Returns:
            Total number of clients
        """
        return sum(len(clients) for clients in self._rooms.values())

    def __len__(self):
        return self.total_size()

    def __iter__(self):
        return iter(self.get_all_clients())

    def __contains__(self, client: Client) -> bool:
        return any(client in clients for clients in self._rooms.values())
