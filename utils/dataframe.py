from __future__ import annotations
from abc import abstractmethod
import string

from utils.errors import InvalidFrameError
from .enumerations import EventType, AudioEncoding, HeartbeatType
from .logger import get_logger
import datetime
import struct
import random

logger = get_logger(__name__)


def get_event_type(data: bytes) -> EventType:
    """Get the event type from the binary data.
    
    The event type is stored in the first byte of the data, and refer to `EventType` enum.
    
    Args:
        data (bytes): The binary data of the frame.
    """
    if not data:
        raise ValueError("Data is empty.")
    return EventType(data[0])


def current_timestamp_ms() -> int:
    return int(datetime.datetime.now().timestamp() * 1000)


class BaseFrame:
    """
    
    All of the data frame must include follow fields:
    - 1 byte: event type, refer to `EventType` enum
    - 8 bytes: timestamp, uint64 (milliseconds)

    Returns:
        _type_: _description_
    """
    event_type = EventType.OMIT
    timestamp: int = current_timestamp_ms()

    def __init__(self, **kwargs):
        pass

    @abstractmethod
    def dump(self):
        return bytes(b"")

    @property
    def bin(self):
        return self.dump()

    # Here you **must** keep `staticmethord` decorator before `abstractmethod` decorator,
    # otherwise it will cause error: `AttributeError: attribute '__isabstractmethod__' of 'staticmethod' objects is not writable`
    @staticmethod
    @abstractmethod
    def load(data: bytes):
        return BaseFrame()


# class AudioFrame(BaseFrame):
#     """A class to abstract the audio frame data.

#     The binary format of the audio frame is as follows:
#     - 1 byte: event type, refer to `EventType` enum
#     - 8 bytes: timestamp, uint64 (milliseconds)
#     - 2 byte: sample rate, short, uint16 (opus supports up to 48000Hz, so uint16 is enough)
#     - 4 bytes: sample num, uint32
#     - 1 bytes: channels num, uint8
#     - 4 bytes: length of audio data, uint32, refering to `N bytes` below
#     - 1 byte: encoding type, uint8, refer to AudioEncoding enum
#     - N bytes: audio data

#     Please be careful when using this class. Big numbers may cause overflow when converting to bytes.
#     For example, the sample rate and the sample num should be less than 4294967296.

#     """
#     # Metadata for this frame, which can be used for filtering and routing
#     event_type: EventType = EventType.AUDIO_FRAME
#     timestamp: int = current_timestamp_ms()

#     # Metadata of the audio, and audio data
#     sample_rate: int = 48000
#     sample_num: int = 0
#     channels: int = 2
#     _length: int = 0  # the length of audio data in bytes
#     encoding: AudioEncoding = AudioEncoding.OPUS
#     data: bytes = b""

#     frame_format = "!B Q H I B I B"  # the format for struct packing and unpacking
#     frame_header_size = struct.calcsize(
#         frame_format)  # the size of the frame header in bytes

#     def __init__(self, sample_rate: int, sample_num: int, channels: int,
#                  encoding: AudioEncoding, data: bytes):

#         self.timestamp = current_timestamp_ms()
#         self.sample_rate = sample_rate
#         self.sample_num = sample_num
#         self.channels = channels
#         self._length = len(data)
#         self.encoding = encoding
#         self.data = data

#     def dump(self):
#         if not self.data:
#             logger.warning("AudioFrame dump called with empty data")

#         # `!B Q H I B I B` means: unsigned char (1), unsigned long long (8), unsigned short (2), unsigned int (4), unsigned char (1), unsigned int (4), unsigned char (1)
#         return struct.pack(AudioFrame.frame_format,
#                            AudioFrame.event_type.value, self.timestamp,
#                            self.sample_rate, self.sample_num, self.channels,
#                            self._length, self.encoding.value) + self.data

#     @staticmethod
#     def load(data: bytes):
#         unpacked = struct.unpack(AudioFrame.frame_format,
#                                  data[:AudioFrame.frame_header_size])
#         frame = AudioFrame(sample_rate=unpacked[2],
#                            sample_num=unpacked[3],
#                            channels=unpacked[4],
#                            encoding=AudioEncoding(unpacked[6]),
#                            data=data[AudioFrame.frame_header_size:])
#         frame.event_type = EventType(unpacked[0])
#         frame.timestamp = unpacked[1]
#         return frame

#     def to_dict(self):
#         return {
#             "event_type": self.event_type.name,
#             "timestamp": self.timestamp,
#             "sample_rate": self.sample_rate,
#             "sample_num": self.sample_num,
#             "length": self._length,
#             "encoding": self.encoding.name,
#             "data": self.data.hex(),
#         }


class HeartbeatFrame(BaseFrame):
    """A class to abstract the heartbeat frame data.
    
    The binary format of the heartbeat frame is as follows:
    - 1 byte: event type, refer to `EventType` enum
    - 1 byte: heartbeat type, refer to `HeartbeatType` enum
    - 8 bytes: timestamp, uint64 (milliseconds)
    - 8 bytes: uid, string, random generated. Used for identifying the source of the heartbeat frame.
    - 8 bytes: t1, uint64 (milliseconds), the timestamp when the client sends the heartbeat frame.
    - 8 bytes: t2, uint64 (milliseconds), the timestamp when the server receives the heartbeat frame.
    - 8 bytes: t3, uint64 (milliseconds), the timestamp when the server sends the heartbeat response frame.
    - 8 bytes: t4, uint64 (milliseconds), the timestamp when the client receives the heartbeat response frame.
    
    The heartbeat can be used to keep the connection, and measure the latency and time offset between the client and the server.
    
    """
    event_type: EventType = EventType.HEARTBEAT
    heartbeat_type: HeartbeatType
    timestamp: int = current_timestamp_ms()
    uid: str = "".join(random.sample(string.ascii_letters + string.digits, 8))
    t1: int = 0
    t2: int = 0
    t3: int = 0
    t4: int = 0

    _data_format = "!B B Q 8s Q Q Q Q"  # the format for struct packing and unpacking

    def __init__(
        self,
        heartbeat_type: HeartbeatType = HeartbeatType.PING,
        uid: str | None = None,
        t1: int = 0,
        t2: int = 0,
        t3: int = 0,
        t4: int = 0,
    ):
        self.timestamp = current_timestamp_ms()
        self.heartbeat_type = heartbeat_type
        if uid:
            self.uid = uid
        else:
            self.uid = "".join(
                random.sample(string.ascii_letters + string.digits, 8))
        if t1 == 0 and heartbeat_type == HeartbeatType.PING:
            t1 = self.timestamp
        self.t1 = t1
        self.t2 = t2
        self.t3 = t3
        self.t4 = t4

    def dump(self):
        return struct.pack(
            HeartbeatFrame._data_format,
            self.event_type.value,
            self.heartbeat_type.value,
            self.timestamp,
            self.uid.encode(),
            self.t1,
            self.t2,
            self.t3,
            self.t4,
        )

    @staticmethod
    def load(data: bytes):
        try:
            unpacked: tuple[int, int, int, bytes, int, int, int,
                            int] = struct.unpack(HeartbeatFrame._data_format,
                                                 data)
        except struct.error as e:
            logger.error("Failed to unpack HeartbeatFrame: %s", e)
            raise InvalidFrameError("Invalid data for HeartbeatFrame") from e
        frame = HeartbeatFrame()
        frame.event_type = EventType(unpacked[0])
        frame.heartbeat_type = HeartbeatType(unpacked[1])
        frame.timestamp = unpacked[2]
        frame.uid = unpacked[3].decode()
        frame.t1 = unpacked[4]
        frame.t2 = unpacked[5]
        frame.t3 = unpacked[6]
        frame.t4 = unpacked[7]
        return frame
