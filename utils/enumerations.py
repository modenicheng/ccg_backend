"""
枚举定义
定义项目中使用的事件类型、游戏状态等枚举
"""
from __future__ import annotations
from enum import Enum


class EventType(Enum):
    """事件类型枚举"""

    OMIT = 0
    AUDIO_FRAME = 1
    META_DATA = 2
    HEARTBEAT = 3
    TIME_SYNC = 4
    ERROR = 255  # for error handling


class ErrorEventType(Enum):
    """错误事件类型枚举"""

    INVALID_JSON = 200
    MISSING_EVENT_FIELD = 201
    UNSUPPORTED_EVENT = 202
    HANDLER_EXCEPTION = 203


class GameEventType(Enum):
    """游戏事件类型枚举"""

    # ROOM_CREATE = 10
    ROOM_JOIN = 11
    ROOM_STATE = 12
    GAME_OVER = 13
    START_POS_UPDATE = 14
    KICK_USER = 15
    PLAYER_LEAVE = 16

    PLAY = 20
    PAUSE = 21
    SEEK = 22
    PRELOAD_AUDIO = 23

    PLAYER_READY = 30
    GAME_START = 31
    ROUND_START = 32
    ATTEMPT_ANSWER = 33
    YOUR_TURN = 34
    SUBMIT_ANSWER = 35
    ANSWER_BROADCAST = 36
    ANSWER_QUEUE = 37
    ROUND_END = 38

    JUDGING = 40
    JUDGE_SUBMIT = 41
    SCORE_UPDATE = 42
    # We consider the skipping as a judging state event
    SKIP_ROUND = 43
    SHOW_ANSWER = 44
    ROUND_STATE_UPDATE = 45
    SHOW_SONG = 46

    # 全量的玩家答案，用于房间内所有客户端的同步显示与断线重连恢复
    PLAYER_ANSWER = 50
    PLAYER_SELECTION_UPDATE = 51  # 用于玩家选择的增量更新，减少网络传输
    PLAYER_DESCRIPTION_UPDATE = 52  # 用于玩家描述的增量更新，减少网络传输
    CLEAR_ANSWER_QUEUE = 53  # 清空抢答队列

    TAGS_UPDATE = 60  # 标签增量更新
    TAG_GROUPS_UPDATE = 61  # 标签组增量更新
    TAG_GROUP = 62  # 房间已选标签组同步（非全量 ROOM_STATE）


class AudioEncoding(Enum):
    """音频编码格式枚举"""

    UNKNOWN = 0
    OPUS = 1
    PCM = 2


class HeartbeatType(Enum):
    """心跳类型枚举"""

    PING = 0
    PONG = 1


class MusicPlatform(Enum):
    """音乐平台枚举"""

    QQ = "qq"
    NETEASE = "netease"


class RoomStatus(Enum):
    """房间状态枚举"""

    WAITING = 0
    RUNNING = 1
    ENDED = 2


class RoundState(Enum):
    PENDING = 0
    PLAYING_AUDIO = 1
    ANSWERING = 2
    JUDGING = 3
    COMPLETED = 4
