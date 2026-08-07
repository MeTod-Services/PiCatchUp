"""The records that move between modules, one per database table.

These are plain immutable data holders: no persistence, no business logic. Text
fields may contain lesson content and must never be logged.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Lesson:
    """One recording. See `lessons` in schema.sql."""

    id: str
    lesson_date: str  # YYYY-MM-DD
    subject: str | None
    audio_path: str | None  # None once the audio has been purged
    audio_sha256: str
    duration_ms: int | None
    text: str | None  # None until transcribed
    created_at: str  # UTC ISO-8601


@dataclass(frozen=True, slots=True)
class Chunk:
    """A searchable piece of a lesson, with its position in the recording."""

    id: str
    lesson_id: str
    idx: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class Recap:
    """One summary per day."""

    id: str
    recap_date: str  # YYYY-MM-DD
    text: str
    model: str
    created_at: str


@dataclass(frozen=True, slots=True)
class User:
    """Someone allowed to use the bot. No row means no access."""

    id: str
    telegram_user_id: int
    display_name: str
    is_admin: bool
    is_active: bool
    is_muted: bool
    created_at: str


@dataclass(frozen=True, slots=True)
class Delivery:
    """Proof that a recap was sent, so it is never sent twice."""

    recap_id: str
    user_id: str
    telegram_message_id: int | None
    sent_at: str
