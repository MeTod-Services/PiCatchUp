"""Immutable domain records matching the schema frozen by T-S01.

These records intentionally contain no persistence or business logic. Text fields may
contain personal or lesson content and must not be logged at INFO or above.
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import (
    AudioAssetStatus,
    ChatType,
    ClassStatus,
    ConsentScope,
    ConsentSubjectKind,
    Coverage,
    DeliveryStatus,
    JobStatus,
    JobType,
    LessonNoteKind,
    LessonSource,
    LessonStatus,
    MembershipRole,
    RecapStatus,
    TranscriptStatus,
    UserRole,
    UserStatus,
    Verbosity,
)
from .types import EntityId, JsonObject, LocalDate, UtcTimestamp


@dataclass(frozen=True, slots=True)
class SchoolClass:
    id: EntityId
    name: str
    school_year: str
    timezone: str
    status: ClassStatus
    created_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class User:
    id: EntityId
    display_name: str
    telegram_user_id: int | None
    role: UserRole
    is_minor: bool
    status: UserStatus
    locale: str
    created_at: UtcTimestamp
    deleted_at: UtcTimestamp | None


@dataclass(frozen=True, slots=True)
class ClassMember:
    id: EntityId
    class_id: EntityId
    user_id: EntityId
    role: MembershipRole
    joined_at: UtcTimestamp
    left_at: UtcTimestamp | None


@dataclass(frozen=True, slots=True)
class EnrollmentCode:
    code_hash: str
    class_id: EntityId
    issued_to: str | None
    expires_at: UtcTimestamp
    used_at: UtcTimestamp | None
    used_by_user_id: EntityId | None
    created_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class Consent:
    id: EntityId
    subject_kind: ConsentSubjectKind
    subject_ref: str
    class_id: EntityId
    scope: ConsentScope
    granted_at: UtcTimestamp
    revoked_at: UtcTimestamp | None
    evidence_ref: str | None
    notes: str | None


@dataclass(frozen=True, slots=True)
class Lesson:
    id: EntityId
    class_id: EntityId
    lesson_date: LocalDate
    subject: str
    teacher: str | None
    period: int | None
    starts_at: UtcTimestamp | None
    ends_at: UtcTimestamp | None
    source: LessonSource
    confidence: float | None
    needs_review: bool
    status: LessonStatus
    created_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class AudioAsset:
    id: EntityId
    class_id: EntityId
    lesson_date: LocalDate
    original_name: str
    stored_path: str | None
    sha256: str
    bytes: int
    duration_ms: int | None
    recorded_at: UtcTimestamp | None
    mime_type: str | None
    status: AudioAssetStatus
    purge_after: UtcTimestamp | None
    purged_at: UtcTimestamp | None
    created_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class Transcript:
    id: EntityId
    class_id: EntityId
    audio_asset_id: EntityId
    lesson_id: EntityId | None
    language: str
    provider: str
    model: str
    raw_text: str
    clean_text: str | None
    quality_score: float | None
    status: TranscriptStatus
    created_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    id: EntityId
    transcript_id: EntityId
    idx: int
    start_ms: int
    end_ms: int
    text: str
    avg_logprob: float | None
    no_speech_prob: float | None
    compression_ratio: float | None


@dataclass(frozen=True, slots=True)
class TranscriptChunk:
    id: EntityId
    class_id: EntityId
    transcript_id: EntityId
    lesson_id: EntityId | None
    lesson_date: LocalDate
    subject: str | None
    idx: int
    start_ms: int
    end_ms: int
    text: str
    token_estimate: int | None


@dataclass(frozen=True, slots=True)
class LessonNote:
    id: EntityId
    class_id: EntityId
    lesson_id: EntityId | None
    author_user_id: EntityId | None
    kind: LessonNoteKind
    body: str
    created_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class Recap:
    id: EntityId
    class_id: EntityId
    recap_date: LocalDate
    version: int
    body_md: str
    body_telegram: str
    model: str
    provider: str
    prompt_version: str
    input_hash: str
    status: RecapStatus
    created_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class RecapSection:
    id: EntityId
    recap_id: EntityId
    lesson_id: EntityId | None
    subject: str
    ord: int
    body_md: str


@dataclass(frozen=True, slots=True)
class Delivery:
    id: EntityId
    recap_id: EntityId
    user_id: EntityId
    chat_id: int
    status: DeliveryStatus
    attempts: int
    telegram_message_id: int | None
    error_code: str | None
    error_detail: str | None
    sent_at: UtcTimestamp | None
    created_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class TelegramChat:
    chat_id: int
    user_id: EntityId
    chat_type: ChatType
    is_blocked: bool
    last_seen_at: UtcTimestamp | None
    created_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class TelegramUpdate:
    update_id: int
    received_at: UtcTimestamp
    chat_id: int | None
    processed: bool


@dataclass(frozen=True, slots=True)
class ConversationState:
    chat_id: int
    active_recap_id: EntityId | None
    active_date: LocalDate | None
    active_subject: str | None
    pending_intent: str | None
    state: JsonObject
    updated_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class UserPreferences:
    user_id: EntityId
    verbosity: Verbosity
    muted: bool
    quiet_hours: str | None
    reminders: JsonObject
    updated_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class Question:
    id: EntityId
    user_id: EntityId
    class_id: EntityId
    chat_id: int
    telegram_message_id: int | None
    reply_to_message_id: int | None
    recap_id: EntityId | None
    resolved_date: LocalDate | None
    resolved_subject: str | None
    resolved_lesson_id: EntityId | None
    body: str
    created_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class Answer:
    id: EntityId
    question_id: EntityId
    coverage: Coverage
    body_lesson: str | None
    body_extra: str | None
    body_sent: str
    provider: str
    model: str
    prompt_version: str
    retrieval_ms: int | None
    generation_ms: int | None
    token_usage: JsonObject | None
    created_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class AnswerCitation:
    id: EntityId
    answer_id: EntityId
    chunk_id: EntityId
    rank: int
    score: float | None
    used_in_answer: bool


@dataclass(frozen=True, slots=True)
class Job:
    id: EntityId
    type: JobType
    class_id: EntityId | None
    payload: JsonObject
    idempotency_key: str
    status: JobStatus
    priority: int
    attempts: int
    max_attempts: int
    run_after: UtcTimestamp
    leased_until: UtcTimestamp | None
    leased_by: str | None
    last_error: str | None
    created_at: UtcTimestamp
    updated_at: UtcTimestamp


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: EntityId
    actor: str
    action: str
    target_kind: str | None
    target_id: EntityId | None
    class_id: EntityId | None
    detail: JsonObject | None
    created_at: UtcTimestamp
