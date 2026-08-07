"""Injectable UTC clock helpers for deterministic application tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class FixedClock:
    instant: datetime

    def __post_init__(self) -> None:
        if self.instant.tzinfo is None or self.instant.utcoffset() is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")

    def now(self) -> datetime:
        return self.instant.astimezone(UTC)


def utc_now() -> datetime:
    """Return the current aware UTC datetime."""

    return SystemClock().now()


def to_utc_iso(value: datetime) -> str:
    """Serialize an aware datetime as canonical ISO-8601 UTC text."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("UTC serialization requires a timezone-aware datetime")
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = ["Clock", "FixedClock", "SystemClock", "to_utc_iso", "utc_now"]
