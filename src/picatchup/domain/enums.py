"""Enums shared by persistence, jobs, providers, and application services."""

from __future__ import annotations

from enum import Enum


class ClassStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class UserRole(str, Enum):
    STUDENT = "student"
    ADMIN = "admin"


class UserStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class MembershipRole(str, Enum):
    STUDENT = "student"
    ADMIN = "admin"
    RECORDER = "recorder"


class ConsentSubjectKind(str, Enum):
    STUDENT = "student"
    TEACHER = "teacher"
    CLASS = "class"


class ConsentScope(str, Enum):
    RECORDING = "recording"
    TRANSCRIPTION = "transcription"
    AI_PROCESSING = "ai_processing"


class LessonSource(str, Enum):
    TIMETABLE = "timetable"
    MANUAL = "manual"
    LLM = "llm"


class LessonStatus(str, Enum):
    PLANNED = "planned"
    TRANSCRIBED = "transcribed"
    RECAPPED = "recapped"


class AudioAssetStatus(str, Enum):
    RECEIVED = "received"
    QUARANTINED = "quarantined"
    NORMALIZED = "normalized"
    TRANSCRIBED = "transcribed"
    FAILED = "failed"
    PURGED = "purged"


class TranscriptStatus(str, Enum):
    RAW = "raw"
    NORMALIZED = "normalized"
    INDEXED = "indexed"
    FAILED = "failed"


class LessonNoteKind(str, Enum):
    NOTE = "note"
    HOMEWORK = "homework"
    CORRECTION = "correction"


class RecapStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    SUPERSEDED = "superseded"


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class Verbosity(str, Enum):
    BRIEF = "brief"
    STANDARD = "standard"
    DETAILED = "detailed"


class Coverage(str, Enum):
    FULL = "full"
    PARTIAL = "partial"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    LOW_QUALITY = "low_quality"


class JobType(str, Enum):
    INGEST = "ingest"
    TRANSCRIBE = "transcribe"
    NORMALIZE = "normalize"
    SEGMENT = "segment"
    INDEX = "index"
    RECAP = "recap"
    DELIVER = "deliver"
    EXPORT = "export"
    PURGE = "purge"
    BACKUP = "backup"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    DEAD = "dead"
    CANCELLED = "cancelled"


class ChatType(str, Enum):
    PRIVATE = "private"
    GROUP = "group"
    SUPERGROUP = "supergroup"
    CHANNEL = "channel"
