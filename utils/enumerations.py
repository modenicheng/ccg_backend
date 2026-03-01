from enum import Enum


class EventType(Enum):
    OMIT = 0
    AUDIO_FRAME = 1
    META_DATA = 2
    HEARTBEAT = 3
    TIME_SYNC = 4
    ERROR = 255  # for error handling


class ErrorEventType(Enum):
    INVALID_JSON = 200
    MISSING_EVENT_FIELD = 201
    UNSUPPORTED_EVENT = 202
    HANDLER_EXCEPTION = 203


class GameEventType(Enum):
    ROOM_CREATE = 10
    ROOM_JOIN = 11
    ROOM_STATE = 12
    GAME_OVER = 13
    START_POS_UPDATE = 14

    PLAY = 20
    PAUSE = 21
    SEEK = 22
    PRELOAD_AUDIO = 23

    PLAYER_READY = 30
    GAME_START = 31
    COUNTDOWN = 32
    ATTEMPT_ANSWER = 33
    YOUR_TURN = 34
    SUBMIT_ANSWER = 35
    ANSWER_BROADCAST = 36
    ANSWER_QUEUE = 37
    ROUND_END = 38

    JUDGING = 40
    JUDGE_SUBMIT = 41
    SCORE_UPDATE = 42


class AudioEncoding(Enum):
    UNKNOWN = 0
    OPUS = 1
    PCM = 2


class HeartbeatType(Enum):
    PING = 0
    PONG = 1


class MusicPlatform(Enum):
    QQ = "qq"
    NETEASE = "netease"


class RoomStatus(Enum):
    WAITING = 0
    RUNNING = 1
    ENDED = 2
