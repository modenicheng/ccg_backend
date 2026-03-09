from __future__ import annotations

from cache.room_cache import _normalize_status_value


def test_normalize_status_value_accepts_numeric_and_enum_style() -> None:
    assert _normalize_status_value("0") == "0"
    assert _normalize_status_value("1") == "1"
    assert _normalize_status_value("2") == "2"

    assert _normalize_status_value("RUNNING") == "1"
    assert _normalize_status_value("RoomStatus.RUNNING") == "1"
    assert _normalize_status_value("RoomStatusORM.ENDED") == "2"


def test_normalize_status_value_fallbacks_to_waiting() -> None:
    assert _normalize_status_value("999") == "0"
    assert _normalize_status_value("UNKNOWN") == "0"
    assert _normalize_status_value("") is None
    assert _normalize_status_value(None) is None
