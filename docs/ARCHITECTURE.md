# Architecture

PiCatchUp is a small modular monolith for one class on one Raspberry Pi. It has
one Python package, one SQLite database, ordinary local files, and a few systemd
processes. There is no internal network API, queue service, ORM, or web UI.

## Boundaries

### 1. Persistent state — `db.py` and `schema.sql`

`data/picatchup.db` is the single source of truth for lessons, timed chunks,
recaps, authorized users, delivery records and the Telegram polling offset.
Connections enable WAL, foreign
keys and a busy timeout so the bot can read while the daily command writes.
Schema uniqueness makes ingestion, recap creation and ordinary delivery retries
idempotent.

The only non-database state is `device-status.json`: a tiny atomic, ephemeral
snapshot used for communication between short-lived pipeline commands and the
OLED process. It contains display state/progress only, never lesson text, and
may be deleted at any time.

### 2. Internet boundary — `providers/`

Only modules under `picatchup.providers` open network connections:

- `groq_audio.py` uploads one bounded audio piece and returns timed text;
- `groq_text.py` sends a prompt and returns prose;
- `telegram.py` long-polls, sends/splits messages, and leaves group chats;
- `_http.py` supplies bounded timeouts, retries and safe errors.

Everything outside this directory receives Python strings, dataclasses, rows,
or injected callables. Tests replace providers and use no credentials.

### 3. Pipeline — `transcribe.py`, `recap.py`, `deliver.py`, `search.py`, `qa.py`

- `audio.py` is the sole ffmpeg/ffprobe boundary. It records, validates,
  converts, splits and checksums local audio.
- `transcribe.py` preserves explicit/filename/fallback date precedence, stores
  the audio once, stitches split-file timings, and writes chunks plus FTS5 rows
  in one transaction.
- `recap.py` gives the text model the selected day's transcripts. Forced
  replacement invalidates old delivery rows because they prove receipt of old
  text, not the regenerated text.
- `deliver.py` sends one shared recap to active, unmuted users and records
  Telegram acceptance.
- `search.py` turns a question into quoted FTS5 terms and deterministically
  joins ranked chunks to lesson metadata.
- `qa.py` sends only the best chunks to the model. Python adds citations and
  enforces separate `Dalla lezione` / `Spiegazione aggiuntiva` sections.

The model writes text only. It cannot choose files/users, authorize access,
construct SQL, execute commands, or choose citations.

### 4. Faces — CLI, bot, daily and device

- `cli.py` parses commands and reports outcomes.
- `bot.py` long-polls provider-neutral updates, rejects groups, checks
  `users.is_active` on every message, and routes Italian commands/Q&A.
- `daily.py` is deliberately a for-loop: retention first, inbox ingestion,
  recap, delivery. Files that fail remain in the inbox for the next run.
- `device.py` owns the physical button, OLED and LED lifecycle. GPIO callbacks
  only trigger lightweight state changes; recording runs in a worker thread.

### 5. Hardware adapters — `hardware.py`, `display.py`, `status.py`

Raspberry-Pi imports are lazy and optional. A no-op/fake backend keeps every
module importable and testable on normal Linux/macOS development computers.

The explicit LED controller owns OFF, recording blink, success and error
patterns and always drives OFF on close. The OLED renderer has only the states
needed by a 128×64 display. It opens the configured address directly; it does
not scan I²C during normal operation. A missing OLED/LED or battery reading is
a degraded mode, not a reason to stop recording or Telegram.

## Runtime picture

```text
       GPIO button                 Telegram users        systemd timers
           |                             |                    |
       device.py                      bot.py               daily.py
      /    |    \                       |                    |
 audio.py OLED LED                     qa.py        transcribe/recap/deliver
      \    |    /                       |                    |
       status.json                 search.py (FTS5)          |
             \                         /                     /
              +---------------- SQLite ---------------------+
                                   |
                     providers/ (only internet calls)
```

Long-lived services are `picatchup-device` and `picatchup-bot`. Daily processing
and backup are systemd timer-triggered one-shot commands. This keeps idle CPU and
RAM low on a Pi Zero 2 W.

## Daily flow and failure behavior

```text
retention cleanup
  -> discover supported inbox files, oldest first
  -> checksum (already seen means remove duplicate inbox copy)
  -> validate/convert/split audio
  -> Groq Whisper -> lesson + timed chunks + FTS5
  -> Groq text -> one recap for the processing date
  -> Telegram -> each active, unmuted user not already delivered
```

Retention is first, so Groq/Telegram failures cannot extend the configured audio
lifetime; this includes stale inbox/partial/orphan files. Transcription failures
leave the original inbox file and block a knowingly partial recap. Default runs
process newly unrecapped filename dates and pending delivery dates, including a
persistent timer firing after reboot. A recap remains in SQLite when Telegram is
unavailable. Running the command twice does not ordinarily transcribe, create or
send duplicates.

The delivery guarantee is intentionally modest: a row is written after Telegram
accepts the complete send. A crash in that interval, or a later-part failure for
a split recap, can duplicate already accepted text on retry. PiCatchUp does not
claim distributed exactly-once delivery.

## Search and grounded answers

```text
private Telegram question
  -> active-user authorization
  -> tokenize and quote safe Italian search terms
  -> SQLite FTS5/BM25, best five chunks
  -> model sees question + those chunks only
  -> Python renders model sections + database-derived subject/date/minute sources
```

No result means a deterministic Italian “not found” response and no text-model
call. Unstructured model output is discarded rather than presented as something
the teacher said.

## Backups

`picatchup backup` uses SQLite's online-safe mechanism, verifies the resulting
database, and rotates timestamped files under `data/backups/`. Backups contain
private text and need the same permissions as the live database.
