from __future__ import annotations
import asyncio
import json
import socket
import subprocess
import sys
from contextlib import closing
from typing import Any, AsyncIterator, Callable

import httpx
import pytest
import pytest_asyncio
from rich import print as rprint

from cache.room_cache import delete_room_playback_state, get_room_playback_state
from tests.ws_conn import create_join_and_connect_ws

pytestmark = pytest.mark.asyncio(loop_scope="module")


def _find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


async def _wait_server_ready(base_url: str, timeout: float = 15.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient(timeout=1.0) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await client.get(f"{base_url}/")
                if resp.status_code < 500:
                    return
            except Exception:
                pass
            await asyncio.sleep(0.2)
    raise TimeoutError(f"Server is not ready within {timeout}s: {base_url}")


async def _poll_playback_state(room_id: str, timeout: float = 3.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        state = await get_room_playback_state(room_id)
        if state is not None:
            return state
        await asyncio.sleep(0.1)
    return None


async def _poll_playback_state_by_predicate(room_id: str,
                                            predicate,
                                            timeout: float = 3.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        state = await get_room_playback_state(room_id)
        if state is not None and predicate(state):
            return state
        await asyncio.sleep(0.1)
    return None


async def _send_message_and_receive_broadcast(conn, payload: dict, label: str):
    rprint(f"[cyan]\n[{label}] SEND[/cyan]", payload)
    await conn.ws.send(json.dumps(payload, ensure_ascii=False))
    raw = await asyncio.wait_for(conn.ws.recv(), timeout=3.0)
    rprint(f"[green][{label}] RECV RAW[/green]", raw)
    msg = json.loads(raw)
    rprint(f"[green][{label}] RECV JSON[/green]", msg)
    return msg


@pytest_asyncio.fixture(scope="module")
async def running_server():
    port = _find_free_port()
    base_http_url = f"http://127.0.0.1:{port}"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=".",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        await _wait_server_ready(base_http_url)
        yield {
            "base_http_url": base_http_url,
            "base_ws_url": f"ws://127.0.0.1:{port}",
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest_asyncio.fixture(scope="module")
async def shared_conn(running_server) -> AsyncIterator:
    """模块级共享连接：整份文件复用同一个 room + ws。"""
    conn = await create_join_and_connect_ws(
        base_http_url=running_server["base_http_url"],
        base_ws_url=running_server["base_ws_url"],
        role="host",
        expect_first_message=True,
    )
    rprint("[bold cyan]SHARED ROOM READY[/bold cyan]", {
        "room_id": conn.room_id,
        "user": conn.active_user.username,
    })

    try:
        yield conn
    finally:
        await delete_room_playback_state(conn.room_id)
        await conn.close()


@pytest.mark.asyncio(loop_scope="module")
async def test_play_message_updates_redis_and_broadcasts(shared_conn):
    conn = shared_conn
    payload = {
        "event": 20,
        "data": {
            "progress_ms": 4321,
            "offset_ts": 1234567890,
            "audio_url": "https://example.com/test.opus",
        },
    }

    msg = await _send_message_and_receive_broadcast(conn, payload, label="PLAY")

    assert msg["event"] == 20
    assert msg["data"]["progress_ms"] == 4321
    assert msg["data"]["audio_url"] == "https://example.com/test.opus"

    state = await _poll_playback_state(conn.room_id, timeout=3.0)
    assert state is not None
    rprint("[magenta][PLAY] REDIS STATE[/magenta]", state.model_dump())
    assert state.play_state == "playing"
    assert state.progress_ms == 4321
    assert state.audio_url == "https://example.com/test.opus"


@pytest.mark.asyncio(loop_scope="module")
async def test_pause_message_updates_redis_and_broadcasts(shared_conn):
    conn = shared_conn
    payload = {
        "event": 21,
        "data": {
            "progress_ms": 9876,
            "offset_ts": 2233445566,
            "audio_url": "https://example.com/pause.opus",
        },
    }

    msg = await _send_message_and_receive_broadcast(conn, payload, label="PAUSE")

    assert msg["event"] == 21
    assert msg["data"]["progress_ms"] == 9876
    assert msg["data"]["audio_url"] == "https://example.com/pause.opus"

    state = await _poll_playback_state(conn.room_id, timeout=3.0)
    assert state is not None
    rprint("[magenta][PAUSE] REDIS STATE[/magenta]", state.model_dump())
    assert state.play_state == "paused"
    assert state.progress_ms == 9876
    assert state.audio_url == "https://example.com/pause.opus"


@pytest.mark.asyncio(loop_scope="module")
async def test_seek_message_updates_redis_progress_and_broadcasts(shared_conn):
    conn = shared_conn
    prev_state = await _poll_playback_state(conn.room_id, timeout=3.0)
    prev_audio_url = prev_state.audio_url if prev_state else None

    payload = {
        "event": 22,
        "data": {
            "progress_ms": 5555,
            "offset_ts": 777888999,
            "audio_url": "https://example.com/seek.opus",
        },
    }

    msg = await _send_message_and_receive_broadcast(conn, payload, label="SEEK")

    assert msg["event"] == 22
    assert msg["data"]["progress_ms"] == 5555
    assert msg["data"]["offset_ts"] == 777888999
    assert msg["data"]["audio_url"] == "https://example.com/seek.opus"

    state = await _poll_playback_state(conn.room_id, timeout=3.0)
    assert state is not None
    rprint("[magenta][SEEK] REDIS STATE[/magenta]", state.model_dump())
    assert state.progress_ms == 5555
    assert state.offset_ts == 777888999
    assert state.audio_url == prev_audio_url


@pytest.mark.asyncio(loop_scope="module")
async def test_playback_message_pipe_in_single_room(shared_conn):
    """pipe风格：同一个 room / 同一条 ws 连接里串行发 PLAY→PAUSE→SEEK。"""
    conn = shared_conn

    sequence: list[tuple[str, dict[str, Any], Callable[[Any], bool]]] = [
        (
            "PLAY_PIPE",
            {
                "event": 20,
                "data": {
                    "progress_ms": 1111,
                    "offset_ts": 1717171717,
                    "audio_url": "https://example.com/pipe-play.opus",
                },
            },
            lambda s: s.play_state == "playing" and s.progress_ms == 1111,
        ),
        (
            "PAUSE_PIPE",
            {
                "event": 21,
                "data": {
                    "progress_ms": 2222,
                    "offset_ts": 1818181818,
                    "audio_url": "https://example.com/pipe-pause.opus",
                },
            },
            lambda s: s.play_state == "paused" and s.progress_ms == 2222,
        ),
        (
            "SEEK_PIPE",
            {
                "event": 22,
                "data": {
                    "progress_ms": 3333,
                    "offset_ts": 1919191919,
                    "audio_url": "https://example.com/pipe-seek.opus",
                },
            },
            lambda s: s.progress_ms == 3333 and s.offset_ts == 1919191919,
        ),
    ]

    for label, payload, state_predicate in sequence:
        msg = await _send_message_and_receive_broadcast(conn, payload, label=label)
        assert msg["event"] == payload["event"]
        assert msg["data"]["progress_ms"] == payload["data"]["progress_ms"]

        state = await _poll_playback_state_by_predicate(
            conn.room_id,
            state_predicate,
            timeout=3.0,
        )
        assert state is not None, f"{label} did not update playback state as expected"
        rprint(f"[bold magenta][{label}] PIPE REDIS STATE[/bold magenta]",
               state.model_dump())
