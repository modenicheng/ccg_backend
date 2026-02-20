from enum import Enum


class EventType(Enum):
    OMIT = 0
    AUDIO_FRAME = 1
    META_DATA = 2
    HEARTBEAT = 3
    TIME_SYNC = 4
    MESSAGE = 255  # for error handling


class AudioEncoding(Enum):
    UNKNOWN = 0
    OPUS = 1
    PCM = 2


class HeartbeatType(Enum):
    PING = 0
    PONG = 1
