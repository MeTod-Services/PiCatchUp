# ADR-0001: Freeze foundational contracts

- **Status:** Accepted by project owner; contributor acknowledgements pending
- **Date:** 2026-08-04
- **Task:** T-S01
- **Decision scope:** provider interfaces, domain vocabulary, logical schema, job
  payloads, and PLAN decisions D1–D9

## Context

PiCatchUp starts from an empty repository and will be implemented in two parallel
lanes. Both lanes need stable provider, persistence, and job vocabulary before they
can work independently. The application also processes classroom recordings and
personal data, often involving minors, so class scoping and shared/personal storage
cannot be left to later convention.

During T-S01 the project owner clarified D1: there will be one recording for each
lesson; the model should infer the subject from what was said because the timetable
is available but is not always respected.

## Decision

### 1. Architecture and dependencies

Use the modular-monolith shape in `PLAN.md` §3 and `docs/ARCHITECTURE.md`. Domain
records are immutable declarations. All external systems are behind synchronous
Protocols in `src/picatchup/providers/base.py`; provider SDK imports are confined to
`src/picatchup/providers/`.

The five frozen interfaces are transcription, text generation, messaging,
retrieval, and blob storage. Messaging explicitly supports callback acknowledgement
and leaving a chat, which are required for disambiguation and group rejection.

### 2. Domain and schema

The enums and records under `src/picatchup/domain/` are the shared vocabulary.
Storage follows `PLAN.md` §5: lesson content is stored once per class, while user
state and interactions are personal and reference shared ids.

Migration ownership is frozen as follows:

- A range (`0001–0049`): identity/membership, consent, lesson/audio/transcript,
  chunks/FTS, notes, recap/sections/deliveries, jobs, audit, migration ledger;
- B range (`0050–0099`): Telegram chats/updates/state/preferences and Q&A/citations.

Delete actions must uphold `PLAN.md` §5.5. In particular, deleting a user must not
be blocked by a shared note they authored (`author_user_id` becomes null), and
references retained after deletion of optional shared context use `ON DELETE SET
NULL`. Deleting lesson-owned notes uses cascade. Higher-level deletion commands
still remove all target artifacts explicitly in one transaction and write an audit
event.

### 3. D1 — one file per lesson; model-first subject classification

**Chosen:** a refinement of D1(a)/(d).

- Each audio file contains exactly one lesson, so no model chooses time boundaries
  inside a recording.
- After transcription and deterministic normalization, an LLM classifies the
  subject from lesson content. Its output is constrained to configured class
  subjects plus a confidence value.
- The timetable supplies period/subject hints, but it is not authoritative because
  substitutions and schedule changes occur.
- A low-confidence classification or timetable disagreement sets
  `needs_review=1`. Exact thresholds are configuration owned by T-A07.
- A manual lesson assignment is authoritative and is never overwritten.
- The transcript remains untrusted prompt data and classification has no effect on
  authorization.

This supersedes the PLAN's recommended timetable-first boundary proposal for T-A07.
Before T-A07 begins, its task wording and segmentation prompt must be aligned with
this ADR by a coordinated PLAN status update.

### 4. D2–D9

The remaining recommended defaults in `PLAN.md` §2.3 are accepted:

| Decision | Accepted default |
| --- | --- |
| D2 text models | Separate configurable recap and Q&A model keys; initially they may point to the same high-quality model. Live model ids must be verified, never guessed. |
| D3 audio retention | Seven days, then hard-delete bytes and retain the `audio_assets` tombstone. |
| D4 identity binding | Single-use, SHA-256-hashed enrollment codes with 48-hour TTL; manual allowlisting remains available. |
| D5 language | One Italian shared recap; per-user verbosity affects only Q&A explanation. |
| D6 consent | Auditable database rows plus an Italian out-of-band process document. Active consent is required before real ingestion. |
| D7 retrieval chunks | Segment-aligned 500–900 character windows with about 15% overlap, never crossing a lesson. |
| D8 delivery | Configurable class-wide delivery time, default 15:00 Europe/Rome; per-user mute is respected. |
| D9 backups | Local WAL database plus nightly local snapshot; optional operator-configured rsync, disabled by default. |

### 5. Persistent job payloads

Payload JSON has exact field names. Deserialization rejects unknown fields, missing
fields, incorrect scalar types, and envelope/payload `class_id` mismatches.
Nullable fields must still be present as JSON `null`; there are no implicit payload
defaults.

| Queue type | Dataclass | Required JSON fields |
| --- | --- | --- |
| `ingest` | `IngestAudioPayload` | `class_id`, `inbox_relative_path`, `lesson_date`, `recorded_at` |
| `transcribe` | `TranscribeAudioPayload` | `class_id`, `audio_asset_id` |
| `normalize` | `NormalizeTranscriptPayload` | `class_id`, `transcript_id` |
| `segment` | `SegmentLessonPayload` | `class_id`, `transcript_id`, `lesson_date` |
| `index` | `IndexTranscriptPayload` | `class_id`, `transcript_id` |
| `recap` | `GenerateRecapPayload` | `class_id`, `recap_date`, `force` |
| `deliver` | `DeliverRecapPayload` | `class_id`, `recap_id`, `user_id` |
| `export` | `ExportRecapPayload` | `class_id`, `recap_id`, `recap_version` |
| `purge` | `PurgeAudioPayload` | `class_id`, `as_of` |
| `backup` | `BackupDatabasePayload` | `snapshot_date` |

One queue row performs one idempotent unit. Discovery calculates a checksum before
enqueueing a per-file ingest row. Recap fan-out creates one deliver row per user.
Idempotency key formats are frozen in `docs/ARCHITECTURE.md` §7.

## Consequences

### Positive

- Lanes A and B can type against the same records without importing each other's
  implementations.
- One-recording-per-lesson removes uncertain boundary detection while model-first
  subject classification tolerates timetable changes.
- Shared lesson text cannot accidentally be duplicated into per-user records.
- Job replay and crash recovery have stable serialization and idempotency seams.
- Offline fakes can satisfy the same contracts as production providers.

### Costs and risks

- Provider contracts are synchronous; concurrency belongs in process/job design,
  not provider-specific async SDK types.
- Model-first subject classification adds one constrained generation call per
  lesson and requires an evaluation set and confidence tuning.
- Incorrect high-confidence classification remains possible. The known-subject
  constraint, timetable disagreement flag, manual override, and audit trail reduce
  but do not eliminate this risk.
- Frozen payload changes require a migration/compatibility decision once jobs exist
  in deployed databases.

## Rejected alternatives

- **Timetable as authoritative:** rejected because the real schedule is not always
  respected.
- **LLM boundary segmentation of a full-day recording:** unnecessary because each
  lesson has a separate recording.
- **Always-manual subject entry:** rejected in favor of automatic classification
  with review only for uncertain/conflicting cases.
- **Provider SDK types outside `providers/`:** rejected because it prevents offline
  testing and makes adapters leak through the codebase.
- **One delivery job that sends to every user:** rejected because per-user retries
  and idempotency would be opaque.

## Change policy

Changes to `providers/base.py`, public domain record fields, enum wire values, or job
payload field names require a joint ADR/PR reviewed by both contributors. Additive
provider result fields should normally be introduced as a new contract revision;
renames and removals require compatibility handling for persisted jobs.

## Sign-off

The project owner approved the decisions above during the T-S01 session. The task's
formal acceptance criterion still requires both implementation-lane contributors to
acknowledge the freeze:

- [ ] Contributor A — name/date
- [ ] Contributor B — name/date
