"""What the providers hand back.

Provider SDK objects are converted into these inside `picatchup.providers`, so
the rest of the program never sees a Groq or Telegram type.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TranscriptionSegment:
    """A timed piece of speech. Times are absolute within the whole recording."""

    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    segments: tuple[TranscriptionSegment, ...]
    model: str


@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    model: str


@dataclass(frozen=True, slots=True)
class Update:
    """One incoming Telegram event, flattened to what we actually use."""

    update_id: int
    telegram_user_id: int | None
    chat_id: int | None
    chat_type: str | None  # "private", "group", "supergroup", "channel"
    message_id: int | None
    text: str | None


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A chunk that matched a question, with enough context to cite it."""

    chunk_id: str
    lesson_id: str
    lesson_date: str
    subject: str | None
    start_ms: int
    text: str
    score: float
