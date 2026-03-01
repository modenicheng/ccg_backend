from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx

try:
	from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover
	from websockets import connect as ws_connect  # type: ignore

try:
	from websockets.exceptions import InvalidStatus  # websockets>=14
except ImportError:  # pragma: no cover
	from websockets.exceptions import InvalidStatusCode as InvalidStatus  # type: ignore

from websockets.exceptions import ConnectionClosed


class WsConnFactoryError(RuntimeError):
	def __init__(
		self,
		message: str,
		*,
		stage: str,
		status_code: int | None = None,
		response_body: Any | None = None,
	):
		self.stage = stage
		self.status_code = status_code
		self.response_body = response_body
		super().__init__(self._format_message(message))

	def _format_message(self, message: str) -> str:
		extra = f" stage={self.stage}"
		if self.status_code is not None:
			extra += f", status={self.status_code}"
		if self.response_body is not None:
			extra += f", response={self.response_body}"
		return f"{message} ({extra})"


class RoomCreateError(WsConnFactoryError):
	pass


class RoomJoinError(WsConnFactoryError):
	pass


class WsConnectError(WsConnFactoryError):
	pass


class WsAuthError(WsConnFactoryError):
	pass


class DependencyUnavailableError(WsConnFactoryError):
	pass


@dataclass(slots=True)
class WsUser:
	id: int
	token: str
	username: str
	is_owner: bool


@dataclass(slots=True)
class WsConnHandle:
	room_id: str
	role: Literal["host", "guest"]
	active_user: WsUser
	host_user: WsUser
	guest_user: WsUser | None
	cookies: dict[str, str]
	cookie_header: str
	http_client: httpx.AsyncClient
	ws: Any
	initial_message: Any | None = None
	_owns_http_client: bool = False

	async def close(self) -> None:
		try:
			await self.ws.close()
		except Exception:
			pass
		if self._owns_http_client:
			await self.http_client.aclose()


def _normalize_ws_base_url(base_http_url: str, base_ws_url: str | None) -> str:
	if base_ws_url:
		return base_ws_url.rstrip("/")

	parsed = urlsplit(base_http_url.rstrip("/"))
	if parsed.scheme == "http":
		ws_scheme = "ws"
	elif parsed.scheme == "https":
		ws_scheme = "wss"
	else:
		ws_scheme = parsed.scheme

	return urlunsplit((ws_scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment)).rstrip("/")


def _response_body_preview(resp: httpx.Response) -> Any:
	try:
		return resp.json()
	except Exception:
		text = resp.text
		return text[:500] + ("..." if len(text) > 500 else "")


def _extract_user(payload: dict[str, Any], key: str, *, stage: str) -> WsUser:
	user = payload.get(key)
	if not isinstance(user, dict):
		raise WsConnFactoryError(
			f"Missing `{key}` in response payload",
			stage=stage,
			response_body=_short_json(payload),
		)

	try:
		return WsUser(
			id=int(user["id"]),
			token=str(user["token"]),
			username=str(user["username"]),
			is_owner=bool(user["is_owner"]),
		)
	except Exception as exc:
		raise WsConnFactoryError(
			f"Invalid `{key}` payload shape: {exc}",
			stage=stage,
			response_body=_short_json(payload),
		) from exc


def _short_json(data: Any) -> str:
	text = json.dumps(data, ensure_ascii=False, default=str)
	return text[:500] + ("..." if len(text) > 500 else "")


def _raise_http_error_by_stage(
	*,
	stage: str,
	response: httpx.Response,
	fallback_message: str,
) -> None:
	body = _response_body_preview(response)
	detail_text = _short_json(body)

	is_redis_error = response.status_code == 503 and "Redis unavailable" in detail_text
	if is_redis_error:
		raise DependencyUnavailableError(
			"Redis dependency unavailable",
			stage=stage,
			status_code=response.status_code,
			response_body=body,
		)

	if stage == "create_room":
		raise RoomCreateError(
			fallback_message,
			stage=stage,
			status_code=response.status_code,
			response_body=body,
		)
	if stage == "join_room":
		raise RoomJoinError(
			fallback_message,
			stage=stage,
			status_code=response.status_code,
			response_body=body,
		)
	raise WsConnFactoryError(
		fallback_message,
		stage=stage,
		status_code=response.status_code,
		response_body=body,
	)


def _build_auth_cookies(room_id: str, user: WsUser) -> dict[str, str]:
	return {
		f"ccg-room-token:{room_id}": user.token,
		f"ccg-room-user-id:{room_id}": str(user.id),
		f"ccg-room-username:{room_id}": user.username,
	}


def _build_cookie_header(cookies: dict[str, str]) -> str:
	return "; ".join(f"{k}={v}" for k, v in cookies.items())


def _extract_status_code_from_invalid_status(exc: Exception) -> int | None:
	status_code = getattr(exc, "status_code", None)
	if isinstance(status_code, int):
		return status_code

	response = getattr(exc, "response", None)
	if response is None:
		return None

	code = getattr(response, "status_code", None)
	return code if isinstance(code, int) else None


async def _connect_websocket(url: str, cookie_header: str, timeout: float) -> Any:
	connect_fn: Any = ws_connect

	for header_arg in ("additional_headers", "extra_headers"):
		try:
			return await connect_fn(
				url,
				open_timeout=timeout,
				**{header_arg: [("Cookie", cookie_header)]},
			)
		except TypeError:
			continue

	raise WsConnectError(
		"Failed to open websocket due to unsupported headers argument",
		stage="ws_connect",
	)


async def create_join_and_connect_ws(
	*,
	base_http_url: str,
	role: Literal["host", "guest"],
	base_ws_url: str | None = None,
	room_id: str | None = None,
	host_name: str | None = None,
	guest_name: str | None = None,
	title: str | None = None,
	http_client: httpx.AsyncClient | None = None,
	connect_timeout: float = 10.0,
	expect_first_message: bool = False,
	first_message_timeout: float = 3.0,
) -> WsConnHandle:
	"""测试工厂：创建房间/加入房间，并返回可直接使用的 WS 连接句柄。

	约束：
	- role 必须显式传入（host/guest）
	- 默认不读取首包（expect_first_message=False）
	- 使用后端要求的 Cookie 进行 WS 鉴权
	"""

	if role not in ("host", "guest"):
		raise ValueError("role must be either 'host' or 'guest'")
	if role == "host" and room_id is not None:
		raise ValueError("room_id should not be provided when role='host'")

	http_base = base_http_url.rstrip("/")
	ws_base = _normalize_ws_base_url(base_http_url=http_base,
									 base_ws_url=base_ws_url)

	own_http_client = http_client is None
	client = http_client or httpx.AsyncClient(timeout=connect_timeout)

	host_name = host_name or f"host-{uuid4().hex[:8]}"
	guest_name = guest_name or f"guest-{uuid4().hex[:8]}"
	title = title or f"room-{uuid4().hex[:8]}"

	host_user: WsUser | None = None
	guest_user: WsUser | None = None

	try:
		# 1) create room
		create_resp = await client.post(
			f"{http_base}/api/room/",
			json={"title": title, "host_name": host_name},
		)
		if create_resp.status_code >= 400:
			_raise_http_error_by_stage(
				stage="create_room",
				response=create_resp,
				fallback_message="Create room failed",
			)

		create_payload = create_resp.json()
		room_id_from_create = create_payload.get("room_id")
		if not isinstance(room_id_from_create, str) or not room_id_from_create:
			raise RoomCreateError(
				"Create room response missing room_id",
				stage="create_room",
				status_code=create_resp.status_code,
				response_body=_response_body_preview(create_resp),
			)

		room_id = room_id_from_create
		host_user = _extract_user(create_payload, "host", stage="create_room")

		# 2) join room (if guest role)
		if role == "guest":
			join_resp = await client.post(
				f"{http_base}/api/room/{room_id}",
				json={"username": guest_name},
			)
			if join_resp.status_code >= 400:
				_raise_http_error_by_stage(
					stage="join_room",
					response=join_resp,
					fallback_message="Join room failed",
				)
			join_payload = join_resp.json()
			guest_user = _extract_user(join_payload, "user", stage="join_room")

		# 3) build cookies + connect ws
		active_user = host_user if role == "host" else guest_user
		if active_user is None:
			raise WsConnFactoryError(
				"Active user is missing",
				stage="ws_connect",
			)

		cookies = _build_auth_cookies(room_id, active_user)
		cookie_header = _build_cookie_header(cookies)
		ws_url = f"{ws_base}/ws/{room_id}"

		try:
			ws = await _connect_websocket(ws_url, cookie_header, connect_timeout)
		except InvalidStatus as exc:
			status_code = _extract_status_code_from_invalid_status(exc)
			if status_code in (401, 403):
				raise WsAuthError(
					"WebSocket authentication failed",
					stage="ws_connect",
					status_code=status_code,
				) from exc
			raise WsConnectError(
				"WebSocket handshake failed",
				stage="ws_connect",
				status_code=status_code,
			) from exc
		except Exception as exc:
			raise WsConnectError(
				f"WebSocket connection failed: {exc}",
				stage="ws_connect",
			) from exc

		initial_message = None
		if expect_first_message:
			try:
				initial_message = await asyncio.wait_for(
					ws.recv(), timeout=first_message_timeout
				)
			except asyncio.TimeoutError as exc:
				await ws.close()
				raise WsConnectError(
					"Timed out waiting for websocket initial message",
					stage="ws_initial_message",
				) from exc
			except ConnectionClosed as exc:
				await ws.close()
				if exc.code == 1008:
					raise WsAuthError(
						"WebSocket closed with auth error while waiting for initial message",
						stage="ws_initial_message",
						status_code=1008,
					) from exc
				raise WsConnectError(
					f"WebSocket closed while waiting for initial message: code={exc.code}",
					stage="ws_initial_message",
				) from exc

		return WsConnHandle(
			room_id=room_id,
			role=role,
			active_user=active_user,
			host_user=host_user,
			guest_user=guest_user,
			cookies=cookies,
			cookie_header=cookie_header,
			http_client=client,
			ws=ws,
			initial_message=initial_message,
			_owns_http_client=own_http_client,
		)
	except Exception:
		if own_http_client:
			await client.aclose()
		raise


__all__ = [
	"WsConnFactoryError",
	"RoomCreateError",
	"RoomJoinError",
	"WsConnectError",
	"WsAuthError",
	"DependencyUnavailableError",
	"WsUser",
	"WsConnHandle",
	"create_join_and_connect_ws",
]
