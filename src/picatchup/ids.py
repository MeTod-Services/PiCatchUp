"""Dependency-free ULID and correlation-id helpers."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Final

_CROCKFORD: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_MAX_TIMESTAMP_MS: Final[int] = (1 << 48) - 1


def new_id(*, now: datetime | None = None, randomness: bytes | None = None) -> str:
    """Create a 26-character ULID suitable for persistent entity ids.

    ``now`` and ``randomness`` are injectable to make exact values testable. This
    helper does not promise monotonic ordering between ids from the same millisecond.
    """

    instant = now or datetime.now(UTC)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("ULID generation requires a timezone-aware datetime")
    utc_instant = instant.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    if utc_instant < epoch:
        raise ValueError("ULID timestamp is outside the 48-bit range")
    delta = utc_instant - epoch
    timestamp_ms = (
        delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000
    )
    if timestamp_ms > _MAX_TIMESTAMP_MS:
        raise ValueError("ULID timestamp is outside the 48-bit range")

    random_bytes = randomness if randomness is not None else secrets.token_bytes(10)
    if len(random_bytes) != 10:
        raise ValueError("ULID randomness must contain exactly 10 bytes")

    value = (timestamp_ms << 80) | int.from_bytes(random_bytes, "big")
    return _encode_crockford(value, length=26)


def new_correlation_id() -> str:
    """Create an opaque correlation id using the same sortable representation."""

    return new_id()


def _encode_crockford(value: int, *, length: int) -> str:
    encoded = ["0"] * length
    for index in range(length - 1, -1, -1):
        value, remainder = divmod(value, 32)
        encoded[index] = _CROCKFORD[remainder]
    if value:
        raise ValueError("value does not fit in requested Crockford encoding")
    return "".join(encoded)


__all__ = ["new_correlation_id", "new_id"]
