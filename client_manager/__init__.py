import asyncio
from fastapi import WebSocket


class ClientManager:
    _clients: set[WebSocket]

    def __init__(self):
        self._clients = set()

    def push(self, client: WebSocket):
        self._clients.add(client)

    def pop(self, client: WebSocket):
        self._clients.discard(client)

    def get_clients(self) -> set[WebSocket]:
        return self._clients

    def clear(self):
        self._clients.clear()

    def is_empty(self) -> bool:
        return len(self._clients) == 0

    async def send(self, client: WebSocket, message: str | bytes | dict):
        try:
            if isinstance(message, bytes):
                await client.send_bytes(message)
            elif isinstance(message, dict):
                await client.send_json(message)
            else:
                raise TypeError(f"Unsupported message type: {type(message)}")
        except Exception as e:
            print(f"Failed to send message to client {client.client}: {e}")

    async def broadcast(self,
                        message: str | bytes | dict,
                        except_clients: set[WebSocket] = set()):
        await asyncio.gather(*[
            self.send(client, message) for client in self._clients
            if client not in except_clients
        ])

    async def kick(self,
                   client: WebSocket,
                   code: int = 1000,
                   reason: str = "Kicked by server"):
        try:
            await client.close(code=code, reason=reason)
        except Exception as e:
            print(f"Failed to kick client {client.client}: {e}")
        finally:
            self.pop(client)

    def __len__(self):
        return len(self._clients)

    def __iter__(self):
        return iter(self._clients)

    def __contains__(self, client: WebSocket):
        return client in self._clients
