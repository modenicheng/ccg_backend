import asyncio
from deprecated import deprecated
from fastapi import WebSocket
from db.models import Room, User
from utils import get_logger
from schemas.base_message import ErrorMessage, ErrorMessageData

logger = get_logger(__name__)


class Client:
    """WebSocket 客户端包装类，提供额外的属性和方法"""

    def __init__(self, websocket: WebSocket, user: User, room: Room):
        self.ws: WebSocket = websocket
        self.user: User = user
        self.room: Room = room

    async def send(self, message: str | bytes | dict):
        try:
            if isinstance(message, bytes):
                await self.ws.send_bytes(message)
            elif isinstance(message, str):
                await self.ws.send_text(message)
            elif isinstance(message, dict):
                await self.ws.send_json(message)
            else:
                raise TypeError(f"Unsupported message type: {type(message)}")
        except Exception as e:
            logger.error(
                f"Failed to send message to client {self.user.username}<{self.user.id}>: {e}",
                exc_info=True,
            )

    async def send_error(self, event_type, message: str):
        error_message = ErrorMessage(
            data=ErrorMessageData(message=message, error_event=event_type))
        await self.send(error_message.model_dump())


class ClientManager:
    _rooms: dict[str, set[Client]]

    def __init__(self):
        self._rooms = {}

    def push(self, room_id: str, client: Client):
        if room_id not in self._rooms:
            self._rooms[room_id] = set()
        self._rooms[room_id].add(client)

    def pop(self, room_id: str, client: Client):
        clients = self._rooms.get(room_id)
        if not clients:
            return
        clients.discard(client)
        if not clients:
            self._rooms.pop(room_id, None)

    def get_clients(self, room_id: str) -> set[Client]:
        return self._rooms.get(room_id, set())

    def get_all_clients(self) -> set[Client]:
        all_clients: set[Client] = set()
        for clients in self._rooms.values():
            all_clients.update(clients)
        return all_clients

    def get_room_snapshot(self) -> dict[str, set[Client]]:
        """获取房间-连接快照（用于遍历，避免直接操作内部引用）。"""
        return {
            room_id: set(clients)
            for room_id, clients in self._rooms.items()
        }

    async def clear(self, room_id: str | None = None):
        if room_id is None:
            r = await asyncio.gather(*[
                client.ws.close(code=1000, reason="Server shutdown")
                for clients in self._rooms.values() for client in clients
            ],
                                     return_exceptions=True)
            logger.debug(r)
            self._rooms.clear()
            return
        room = self._rooms.pop(room_id, None)
        if room:
            await asyncio.gather(*[
                client.ws.close(code=1000, reason="Server shutdown")
                for client in room
            ],
                                 return_exceptions=True)

    def is_empty(self, room_id: str | None = None) -> bool:
        if room_id is None:
            return len(self._rooms) == 0
        return len(self._rooms.get(room_id, set())) == 0

    async def send(self, client: Client, message: str | bytes | dict):
        try:
            if isinstance(message, bytes):
                await client.ws.send_bytes(message)
            elif isinstance(message, str):
                await client.ws.send_text(message)
            elif isinstance(message, dict):
                await client.ws.send_json(message)
            else:
                raise TypeError(f"Unsupported message type: {type(message)}")
        except Exception as e:
            logger.error(
                f"Failed to send message to client {client.user.username}<{client.user.id}>: {e}",
                exc_info=True,
            )

    async def broadcast_error(self,
                              room_id: str,
                              event_type,
                              message: str,
                              excluded_clients: set[Client] | None = None):
        error_message = ErrorMessage(
            data=ErrorMessageData(message=message, error_event=event_type))
        await self.broadcast(room_id,
                             error_message.model_dump(),
                             excluded_clients=excluded_clients)

    async def broadcast(self,
                        room_id: str,
                        message: str | bytes | dict,
                        excluded_clients: set[Client] | None = None):
        excluded_clients = excluded_clients or set()
        clients = self._rooms.get(room_id, set())
        await asyncio.gather(*[
            self.send(client, message) for client in clients
            if client not in excluded_clients
        ],
                             return_exceptions=True)

    async def kick(self,
                   room_id: str,
                   client: Client,
                   code: int = 1000,
                   reason: str = "Kicked by server"):
        try:
            await client.ws.close(code=code, reason=reason)
        except Exception as e:
            logger.error(
                f"Failed to kick client {client.user.username}<{client.user.id}>: {e}"
            )
        finally:
            self.pop(room_id, client)
            del client

    def room_size(self, room_id: str) -> int:
        return len(self._rooms.get(room_id, set()))

    def total_size(self) -> int:
        return sum(len(clients) for clients in self._rooms.values())

    def __len__(self):
        return self.total_size()

    def __iter__(self):
        return iter(self.get_all_clients())

    def __contains__(self, client: Client) -> bool:
        return any(client in clients for clients in self._rooms.values())
