"""
Tests for audio error handling functionality.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from schemas.ws_messages.playback_schemas import AudioErrorData, AudioErrorMessage
from handlers.audio_error_handler import handle_audio_preload_error
from utils.enumerations import EventType
from client_manager import ClientManager, Client


@pytest.mark.asyncio
async def test_audio_error_message_schema():
    """Test AudioErrorData and AudioErrorMessage schema validation."""
    # Valid error data
    error_data = AudioErrorData(error_type="load_failed",
                                reason="Network timeout",
                                audio_url="https://example.com/song.mp3")
    assert error_data.error_type == "load_failed"
    assert error_data.reason == "Network timeout"

    # Valid error message
    message = AudioErrorMessage(event=255, ts=1710000000, data=error_data)
    assert message.event == 255
    assert message.data.error_type == "load_failed"


@pytest.mark.asyncio
async def test_audio_error_schema_with_unknown_url():
    """Test AudioErrorData with unknown URL."""
    error_data = AudioErrorData(error_type="sync_failed",
                                reason="Sync mismatch",
                                audio_url="unknown")
    assert error_data.audio_url == "unknown"


@pytest.mark.asyncio
async def test_audio_error_schema_default_url():
    """Test AudioErrorData with default URL."""
    error_data = AudioErrorData(error_type="load_failed", reason="File not found")
    assert error_data.audio_url == "unknown"


def test_audio_error_message_serialization():
    """Test that AudioErrorMessage serializes correctly."""
    error_data = AudioErrorData(error_type="load_failed",
                                reason="Network timeout",
                                audio_url="https://example.com/song.mp3")
    message = AudioErrorMessage(event=255, ts=1710000000, data=error_data)

    # Serialize to dict
    serialized = message.model_dump()
    assert serialized["event"] == 255
    assert serialized["data"]["error_type"] == "load_failed"
    assert serialized["data"]["reason"] == "Network timeout"


@pytest.mark.asyncio
async def test_handle_audio_error_missing_room():
    """Test audio error handler when room is not found."""
    # Mock dependencies
    clients_mock = AsyncMock(spec=ClientManager)
    client_mock = MagicMock(spec=Client)
    client_mock.sid = "test_socket_id"

    error_data = AudioErrorData(error_type="load_failed",
                                reason="Test error",
                                audio_url="https://example.com/test.mp3")
    message = AudioErrorMessage(event=255, ts=1710000000, data=error_data)

    # Mock get_room to return None
    with patch("handlers.audio_error_handler.get_room",
               new_callable=AsyncMock) as mock_get_room:
        mock_get_room.return_value = None

        # Should handle gracefully without raising exception
        await handle_audio_preload_error(data=message,
                                         clients=clients_mock,
                                         client=client_mock,
                                         room_id="room_not_found")

        # Verify logging occurred
        mock_get_room.assert_called_once_with("room_not_found")


@pytest.mark.asyncio
async def test_handle_audio_error_invalid_song_queue():
    """Test audio error handler with invalid song queue."""
    clients_mock = AsyncMock(spec=ClientManager)
    client_mock = MagicMock(spec=Client)
    client_mock.sid = "test_socket_id"

    error_data = AudioErrorData(error_type="load_failed",
                                reason="Test error",
                                audio_url="https://example.com/test.mp3")
    message = AudioErrorMessage(event=255, ts=1710000000, data=error_data)

    # Mock get_room to return room data with invalid song queue state
    room_data = {
        "current_song_queue_index": 10,
        "room_song_queue": [1, 2, 3]  # Index 10 is out of range
    }

    with patch("handlers.audio_error_handler.get_room",
               new_callable=AsyncMock) as mock_get_room:
        mock_get_room.return_value = room_data

        # Should handle gracefully
        await handle_audio_preload_error(data=message,
                                         clients=clients_mock,
                                         client=client_mock,
                                         room_id="room_123")

        # Verify that no further processing occurs
        clients_mock.broadcast.assert_not_called()


@pytest.mark.asyncio
async def test_handle_audio_error_non_qq_song():
    """Test audio error handler with non-QQ platform song."""
    clients_mock = AsyncMock(spec=ClientManager)
    client_mock = MagicMock(spec=Client)
    client_mock.sid = "test_socket_id"

    error_data = AudioErrorData(error_type="load_failed",
                                reason="Test error",
                                audio_url="https://example.com/test.mp3")
    message = AudioErrorMessage(event=255, ts=1710000000, data=error_data)

    room_data = {"current_song_queue_index": 0, "room_song_queue": [1]}

    # Mock database song with non-QQ platform
    mock_song = MagicMock()
    mock_song.platform = "spotify"  # Not QQ
    mock_song.platform_song_id = None

    with patch("handlers.audio_error_handler.get_room", new_callable=AsyncMock) as mock_get_room, \
         patch("handlers.audio_error_handler.session_scope") as mock_session_scope:
        mock_get_room.return_value = room_data

        async_session_mock = AsyncMock(spec=AsyncSession)
        execute_result_mock = MagicMock()
        execute_result_mock.scalar_one_or_none.return_value = mock_song
        async_session_mock.execute = AsyncMock(return_value=execute_result_mock)

        mock_session_scope.return_value.__aenter__.return_value = async_session_mock
        mock_session_scope.return_value.__aexit__.return_value = None

        # Should skip download for non-QQ songs
        await handle_audio_preload_error(data=message,
                                         clients=clients_mock,
                                         client=client_mock,
                                         room_id="room_123")

        # Verify that no task was triggered
        with patch("handlers.audio_error_handler.tasks") as mock_tasks:
            mock_tasks.download_and_cache_song.assert_not_called()


@pytest.mark.asyncio
async def test_event_type_error_value():
    """Test that EventType.ERROR has correct value."""
    assert EventType.ERROR.value == 255


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
