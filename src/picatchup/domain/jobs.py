"""JSON-ready payload contracts for every persistent job type.

Field names are frozen by ADR-0001. Queue serialization must preserve these names and
must reject unknown or missing fields rather than silently inventing defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from .enums import JobType
from .types import EntityId, LocalDate, UtcTimestamp


@dataclass(frozen=True, slots=True)
class IngestAudioPayload:
    class_id: EntityId
    inbox_relative_path: str
    lesson_date: LocalDate
    recorded_at: UtcTimestamp | None


@dataclass(frozen=True, slots=True)
class TranscribeAudioPayload:
    class_id: EntityId
    audio_asset_id: EntityId


@dataclass(frozen=True, slots=True)
class NormalizeTranscriptPayload:
    class_id: EntityId
    transcript_id: EntityId


@dataclass(frozen=True, slots=True)
class SegmentLessonPayload:
    class_id: EntityId
    transcript_id: EntityId
    lesson_date: LocalDate


@dataclass(frozen=True, slots=True)
class IndexTranscriptPayload:
    class_id: EntityId
    transcript_id: EntityId


@dataclass(frozen=True, slots=True)
class GenerateRecapPayload:
    class_id: EntityId
    recap_date: LocalDate
    force: bool


@dataclass(frozen=True, slots=True)
class DeliverRecapPayload:
    class_id: EntityId
    recap_id: EntityId
    user_id: EntityId


@dataclass(frozen=True, slots=True)
class ExportRecapPayload:
    class_id: EntityId
    recap_id: EntityId
    recap_version: int


@dataclass(frozen=True, slots=True)
class PurgeAudioPayload:
    class_id: EntityId
    as_of: UtcTimestamp


@dataclass(frozen=True, slots=True)
class BackupDatabasePayload:
    snapshot_date: LocalDate


JobPayload: TypeAlias = (
    IngestAudioPayload
    | TranscribeAudioPayload
    | NormalizeTranscriptPayload
    | SegmentLessonPayload
    | IndexTranscriptPayload
    | GenerateRecapPayload
    | DeliverRecapPayload
    | ExportRecapPayload
    | PurgeAudioPayload
    | BackupDatabasePayload
)


@dataclass(frozen=True, slots=True)
class JobRequest:
    type: JobType
    class_id: EntityId | None
    payload: JobPayload
    idempotency_key: str
    priority: int
    max_attempts: int
    run_after: UtcTimestamp
