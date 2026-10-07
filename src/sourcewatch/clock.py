"""Injectable clock so every date computation is testable."""

from datetime import UTC, date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    """Anything with a tz-aware ``now()`` in UTC."""

    def now(self) -> datetime: ...


class SystemClock:
    """The real clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock frozen at one instant, for tests and deterministic site builds."""

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FixedClock needs a tz-aware datetime")
        self._at = at.astimezone(UTC)

    def now(self) -> datetime:
        return self._at


def local_date(now: datetime, tz: str) -> date:
    """The calendar date of ``now`` in the zone ``tz``."""
    return now.astimezone(ZoneInfo(tz)).date()
