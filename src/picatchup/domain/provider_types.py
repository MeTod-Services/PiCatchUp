"""Provider-neutral request and result records.

External SDK objects must be converted to these records inside ``picatchup.providers``.
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import ChatType
from .types import EntityId, JsonObject, LocalDate, UtcTimestamp


@dataclass(frozen=True, slots=True)
class TranscriptionSegmentResult:
    start_ms: int
    end_ms: int
    text: str
    avg_logprob: float | None
    no_speech_prob: float | None
    compression_ratio: float | None


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    segments: tuple[TranscriptionSegmentResult, ...]
    language: str
    model: str
    provider: str
    raw_usage: JsonObject | None


@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    finish_reason: str
    prompt_tokens: int | None
    completion_tokens: int | None
    model: str
    provider: str


@dataclass(frozen=True, slots=True)
class KeyboardButton:
    text: str
    callback_data: str | None
    url: str | None


@dataclass(frozen=True, slots=True)
class Keyboard:
    rows: tuple[tuple[KeyboardButton, ...], ...]


@dataclass(frozen=True, slots=True)
class SentMessage:
    chat_id: int
    message_id: int
    sent_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class Update:
    update_id: int
    user_id: int | None
    chat_id: int | None
    chat_type: ChatType | None
    message_id: int | None
    reply_to_message_id: int | None
    text: str | None
    callback_query_id: str | None
    callback_data: str | None


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: EntityId
    class_id: EntityId
    transcript_id: EntityId
    lesson_id: EntityId | None
    lesson_date: LocalDate
    subject: str | None
    idx: int
    start_ms: int
    end_ms: int
    text: str
    score: float
    quality_score: float | None
