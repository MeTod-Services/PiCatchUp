"""Small helpers used everywhere: ids and time."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def new_id() -> str:
    """A unique id for a database row, as 32 hex characters."""
    return uuid.uuid4().hex


def utc_now() -> str:
    """The current time as UTC ISO-8601 text, e.g. '2026-09-15T13:45:02+00:00'.

    Everything stored in the database uses this, so timestamps are comparable
    and sortable as plain strings.
    """
    return datetime.now(UTC).isoformat(timespec="seconds")


def today(timezone: str) -> str:
    """Today's date as 'YYYY-MM-DD' in the given timezone.

    Not the same as the UTC date: at 01:00 in Rome it is still the previous day
    in UTC, and a lesson belongs to the school day, not to UTC.
    """
    return datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d")
