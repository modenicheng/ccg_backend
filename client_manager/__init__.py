import asyncio
from fastapi import WebSocket


class ClientManager:
    _rooms: dict[str, set[WebSocket]]

    def __init__(self):
        self._rooms = {}

    def push(self, room_id: str, client: WebSocket):
        if room_id not in self._rooms:
            self._rooms[room_id] = set()
        self._rooms[room_id].add(client)

    def pop(self, room_id: str, client: WebSocket):
        clients = self._rooms.get(room_id)
        if not clients:
            return
        clients.discard(client)
        if not clients:
            self._rooms.pop(room_id, None)

    def get_clients(self, room_id: str) -> set[WebSocket]:
        return self._rooms.get(room_id, set())

    def get_all_clients(self) -> set[WebSocket]:
        all_clients: set[WebSocket] = set()
        for clients in self._rooms.values():
            all_clients.update(clients)
        return all_clients

    def clear(self, room_id: str | None = None):
        if room_id is None:
            self._rooms.clear()
            return
        self._rooms.pop(room_id, None)

    def is_empty(self, room_id: str | None = None) -> bool:
        if room_id is None:
            return len(self._rooms) == 0
        return len(self._rooms.get(room_id, set())) == 0

    async def send(self, client: WebSocket, message: str | bytes | dict):
        try:
            if isinstance(message, bytes):
                await client.send_bytes(message)
            elif isinstance(message, str):
                await client.send_text(message)
            elif isinstance(message, dict):
                await client.send_json(message)
            else:
                raise TypeError(f"Unsupported message type: {type(message)}")
        except Exception as e:
            print(f"Failed to send message to client {client.client}: {e}")

    async def broadcast(self,
                        room_id: str,
                        message: str | bytes | dict,
                        except_clients: set[WebSocket] | None = None):
        except_clients = except_clients or set()
        clients = self._rooms.get(room_id, set())
        await asyncio.gather(*[
            self.send(client, message) for client in clients
            if client not in except_clients
        ])

    async def kick(self,
                   room_id: str,
                   client: WebSocket,
                   code: int = 1000,
                   reason: str = "Kicked by server"):
        try:
            await client.close(code=code, reason=reason)
        except Exception as e:
            print(f"Failed to kick client {client.client}: {e}")
        finally:
            self.pop(room_id, client)

    def room_size(self, room_id: str) -> int:
        return len(self._rooms.get(room_id, set()))

    def total_size(self) -> int:
        return sum(len(clients) for clients in self._rooms.values())

    def __len__(self):
        return self.total_size()

    def __iter__(self):
        return iter(self.get_all_clients())

    def __contains__(self, client: WebSocket):
        return any(client in clients for clients in self._rooms.values())
