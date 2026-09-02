"""Recurrence math — DESIGN.md §2.5. Pure functions, no I/O.

Time/date conventions (DESIGN.md §1.6):
- `due_date` is a calendar date only (YYYY-MM-DD). The "current date" is never
  needed for any algorithm in v1 — all recurrence math is anchored to stored
  dates (due_date, or created_at when due_date is NULL) — so no timezone
  handling is required anywhere.
"""

import calendar
from datetime import date, datetime, timedelta

__all__ = ["add_months", "next_due"]


def _as_date(value):
    """Coerce a date | datetime | 'YYYY-MM-DD' string to a plain date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def add_months(anchor, months=1):
    """Add whole calendar months to *anchor*, clamping the day to the target
    month's length (Jan 31 + 1 month == Feb 28/29). Pure, unit-testable."""
    a = _as_date(anchor)
    total = a.year * 12 + (a.month - 1) + months
    y, m0 = divmod(total, 12)
    m = m0 + 1
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(a.day, last))


def next_due(prev_due, created_at, recurrence, interval):
    """Compute the next occurrence's due date — DESIGN.md §2.5.

    prev_due   : the occurrence's CURRENT due_date (date | 'YYYY-MM-DD' | None)
    created_at : ISO-8601 UTC timestamp of the occurrence (anchor fallback)
    recurrence : 'daily' | 'weekly' | 'monthly' | 'custom' | 'none'
    interval   : int >= 1, only meaningful when recurrence == 'custom'

    Returns a concrete date (never None) when recurrence != 'none';
    returns None for recurrence == 'none'.
    """
    if recurrence == "none":
        return None

    if prev_due is not None:
        anchor = _as_date(prev_due)
    else:
        # "Creation date if none": UTC calendar date of created_at.
        anchor = date.fromisoformat(str(created_at)[:10])

    if recurrence == "daily":
        return anchor + timedelta(days=1)
    if recurrence == "weekly":
        return anchor + timedelta(days=7)
    if recurrence == "monthly":
        return add_months(anchor, 1)
    if recurrence == "custom":
        # interval validated >= 1 by the API layer (Pydantic + DB CHECK).
        return anchor + timedelta(days=interval)
    raise ValueError(f"unknown recurrence: {recurrence!r}")
