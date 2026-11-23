"""Due-date arithmetic (FINANCIAL_CALCULATIONS.md §3)."""

from __future__ import annotations

from calendar import monthrange
from datetime import date


def add_months(start: date, months: int) -> date:
    """``start`` moved forward by ``months``, keeping the day of month where it exists.

    Days that do not exist in the target month clamp to its last day. Each due date is
    computed from the *original* date, so 31 Jan → 28 Feb → 31 Mar (not 28 Mar).
    """
    if months < 0:
        raise ValueError("months must not be negative")
    years, month_index = divmod(start.month - 1 + months, 12)
    year = start.year + years
    month = month_index + 1
    return date(year, month, min(start.day, monthrange(year, month)[1]))
