class ParseError(Exception):
    """Base class for parsing errors."""
    pass


class InvalidEventTypeError(ParseError):
    """Raised when an invalid event type is encountered."""

    def __init__(self, event_type: int):
        super().__init__(f"Invalid event type: {event_type}")
        self.event_type = event_type


class InvalidFrameError(ParseError):
    """Raised when a frame cannot be parsed correctly."""

    def __init__(self, message: str):
        super().__init__(message)
