# PiCatchUp architecture

> Contract baseline: T-S01 / ADR-0001. This document describes boundaries, not an
> implemented system. `PLAN.md` remains the product and delivery plan.

## 1. System shape

PiCatchUp is a modular Python monolith deployed as two long-running processes:

- `picatchup-worker`: leases durable SQLite jobs and runs pipeline handlers;
- `picatchup-bot`: long-polls Telegram and delegates application work.

Timers and the admin CLI enqueue work. SQLite is the canonical store and runs with
WAL, foreign keys enabled, and a busy timeout. Audio and generated Markdown live
under `DATA_DIR`; Markdown and the FTS index are rebuildable artifacts.

There is no internal network API, Redis, container runtime, or web frontend.

## 2. Dependency direction

```text
CLI / Telegram / job handlers
            |
            v
application modules (ingest, transcription, normalize, segmentation,
                     recap, retrieval, QA, export)
            |
            +--> domain records and enums
            +--> repository interfaces / SQLite repositories
            +--> provider Protocols
                         |
                         v
              providers/* implementations
              (Groq, Telegram, local FS, fakes)
```

Rules:

1. `picatchup.domain` contains immutable data declarations and enums, no I/O or
   business logic.
2. `picatchup.providers.base` is the external-service boundary. Only
   `picatchup.providers` implementations may import Groq, HTTP, or Telegram SDKs.
3. Application modules receive providers by dependency injection; offline tests use
   deterministic fakes.
4. Repositories enforce class scoping. A shared-data read always receives a
   server-resolved `class_id`.
5. No LLM output may become a path, SQL fragment, authorization decision, citation,
   chat id, or job-success signal.

## 3. Daily data flow

```text
one audio file per lesson
  -> ingest/checksum/ffmpeg normalization
  -> transcription with absolute timed segments
  -> deterministic text normalization and quality scoring
  -> subject classification (LLM; timetable is a hint)
  -> lesson row and transcript-to-lesson assignment
  -> deterministic lesson-aligned chunks and FTS5 index
  -> one recap per class/day
  -> one delivery job per authorized user
  -> class-scoped retrieval and grounded Q&A
```

### Lesson classification decision

A recording never contains multiple lessons: **one recording is one lesson**. This
removes boundary detection from segmentation. The classifier identifies the subject
from transcript content, constrained to configured class subjects, while the
schedule supplies the likely period and subject as hints.

A low-confidence result or a disagreement with the timetable is persisted with
`needs_review=1`. A manual classification is authoritative and is never overwritten
by a model. Threshold values and the timetable format remain configuration work for
T-A07; the behavior above is frozen by ADR-0001.

The transcript is untrusted input. Classification output is constrained to a known
subject plus confidence; it cannot create arbitrary paths, SQL, or permissions.

## 4. Persistence boundaries

### Shared once per class

`lessons`, `audio_assets`, `transcripts`, `transcript_segments`,
`transcript_chunks`, `chunk_fts`, `lesson_notes`, `recaps`, and `recap_sections`.
These rows never carry a student `user_id`.

### Personal per user

`users`, `telegram_chats`, `conversation_state`, `user_preferences`, `deliveries`,
`questions`, `answers`, and `telegram_updates`. Personal rows reference shared data
by id; transcript and recap text are never copied per user.

### Operational

`jobs`, `audit_log`, and `schema_migrations`.

Access to a shared row requires an active user, a live `class_members` membership,
and equality between the membership class and the row's explicit `class_id`.
Caller-supplied ids are validated inside that scope. Scope mismatches behave as
"not found".

## 5. Schema freeze and migration ownership

The logical schema is `PLAN.md` §5 with these frozen clarifications:

- `0001_init.sql` owns §5.1–§5.3 plus `jobs`, `audit_log`, and
  `schema_migrations`.
- `0050_telegram.sql` owns `telegram_chats`, `conversation_state`,
  `user_preferences`, `telegram_updates`, `questions`, `answers`, and
  `answer_citations`.
- Foreign keys from retained personal/derived records to deletable shared records
  use `ON DELETE SET NULL` where the record remains meaningful. Lesson-owned notes
  use `ON DELETE CASCADE`; `lesson_notes.author_user_id` uses
  `ON DELETE SET NULL`. Explicit data-deletion services still remove dependent
  transcript/recap artifacts transactionally.
- SQLite integers representing booleans are exposed as `bool` in domain records.
- Database JSON text is exposed as `Mapping[str, object]`.
- UTC timestamps cross domain/job boundaries as ISO-8601 strings; school dates use
  `YYYY-MM-DD` in the class timezone.
- `recaps.body_telegram` stores the complete Telegram-safe rendering. Delivery may
  deterministically split it at block boundaries; split messages are derived and
  are not additional recap rows.

Migration SQL must add the FTS5 external-content triggers and the constraints and
indexes listed in `PLAN.md` §5. Applied migrations are append-only.

## 6. Provider contracts

`src/picatchup/providers/base.py` freezes five synchronous Protocols:

- `TranscriptionProvider`
- `TextGenerationProvider`
- `MessagingProvider`
- `Retriever`
- `BlobStore`

Provider SDK objects are converted immediately to records from
`picatchup.domain.provider_types`. Telegram callback acknowledgement and leaving a
group chat are explicit messaging operations because both are required by the bot
flows. Every network implementation applies bounded timeouts and retry policy; the
Protocols themselves do not implement retries.

Changes to `providers/base.py` require a joint contract PR and review by both lanes.

## 7. Persistent jobs

Every queue row has a typed payload from `picatchup.domain.jobs`. Payload JSON uses
exact dataclass field names, rejects unknown/missing fields, and always repeats
`class_id` for class-scoped work. The queue must verify that envelope and payload
class ids match.

| Job type | Payload | Idempotency key |
| --- | --- | --- |
| `ingest` | `IngestAudioPayload` | `ingest:{class_id}:{sha256}` after discovery/checksum |
| `transcribe` | `TranscribeAudioPayload` | `transcribe:{audio_asset_id}:{model}` |
| `normalize` | `NormalizeTranscriptPayload` | `normalize:{transcript_id}` |
| `segment` | `SegmentLessonPayload` | `segment:{transcript_id}` |
| `index` | `IndexTranscriptPayload` | `index:{transcript_id}` |
| `recap` | `GenerateRecapPayload` | `recap:{class_id}:{recap_date}:{input_hash}` |
| `deliver` | `DeliverRecapPayload` | `deliver:{recap_id}:{user_id}` |
| `export` | `ExportRecapPayload` | `export:{class_id}:{recap_id}:{recap_version}` |
| `purge` | `PurgeAudioPayload` | `purge:{class_id}:{UTC-date}` |
| `backup` | `BackupDatabasePayload` | `backup:{snapshot_date}` |

Inbox discovery occurs before enqueueing a per-file `ingest` job so its checksum can
form the idempotency key. Delivery fan-out similarly creates one job per
`(recap_id, user_id)`; one fan-out job must not perform N opaque sends.

Handlers are idempotent independently of queue keys through schema uniqueness and
transactions. Leases allow crash recovery; retryable and fatal exceptions are
classified by application error types, not provider response prose.

## 8. Security and content handling

- Secrets exist only in `.env`/systemd environment files and are represented by
  redacted configuration values.
- Lesson, recap, question, and answer bodies are not logged at INFO or above.
- All runtime paths derive from `DATA_DIR`; original filenames are metadata only.
- The bot rejects non-private chats before normal command handling and checks
  allowlist/admin status on every update.
- Retrieval and citations are deterministic and class-scoped. The LLM receives only
  bounded retrieved chunks, never a full transcript or direct data access.
- Provider output is untrusted, structurally validated, escaped, and length-capped.

See `PLAN.md` §6–§8 for the complete privacy, Q&A, and recovery requirements.
