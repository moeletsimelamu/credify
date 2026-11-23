"""Injectable UTC clock so time-dependent rules are testable."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Protocol

from fastapi import Request

# Calendar dates (eligibility, due dates) use South African Standard Time: the platform's
# currency is ZAR (decision D1) and SAST has no daylight saving, so a fixed offset is exact.
BUSINESS_TZ = timezone(timedelta(hours=2), "SAST")


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A controllable clock for tests and simulations."""

    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._instant = instant.astimezone(UTC)

    def now(self) -> datetime:
        return self._instant

    def set(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._instant = instant.astimezone(UTC)


def business_date(clock: Clock) -> date:
    """Today's calendar date in the business timezone."""
    return clock.now().astimezone(BUSINESS_TZ).date()


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock
