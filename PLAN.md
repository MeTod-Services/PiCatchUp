# PiCatchUp — The Plan

## Raspberry Pi MVP completion (2026-08-10)

The repository baseline is green (78 tests, Ruff, strict mypy). This implementation
pass completes the remaining MVP in this order:

1. fix date precedence, retention independence, forced-recap delivery invalidation,
   and harden ffmpeg recording validation;
2. add FTS5 retrieval, grounded Italian Q&A, and the long-polling allowlisted bot;
3. add testable LED/OLED/status adapters and integrate the one-button device loop;
4. add backup, doctor/status commands, daily status reporting, and regression/e2e tests;
5. add systemd units, the idempotent Raspberry Pi installer, CI, hardware/privacy/
   acceptance documentation, then run the complete quality gate.

Hardware adapters are tested with fakes on development hosts. Physical GPIO, OLED,
USB microphone, I²C coexistence with PiSugar, and service startup remain explicit
Raspberry Pi acceptance checks rather than being claimed as locally verified.

> **Version 2, simplified.** Version 1 (1606 lines, 25 tasks) is archived in
> `docs/archive/PLAN-v1.md`. It is not wrong — it is written for a company
> team. This one is written for us.

---

## What it is

You record the lesson with your phone. In the evening a summary of what was said
arrives on Telegram, in Italian. Then you can ask it questions and it answers,
citing the exact minute of the recording.

It is for whoever was absent, whoever was distracted, and whoever wants to
review without re-listening to two hours of audio.

---

## How it works, end to end

```
1. drop an audio file into a folder
2. Groq turns it into text              (Whisper)
3. Groq writes a summary of the text    (a text model)
4. the Telegram bot sends the summary
5. you ask a question
6. we search the text for the relevant pieces  (SQLite FTS5)
7. Groq answers using ONLY those pieces
```

Seven lines. The rest of this document is how to get there one piece at a time.

---

## Rules of the game

There are five. Respect only these and the project holds together.

**1. Every step ends with something you can show someone.**
Not "I wrote the normalization module". Instead: "look, I give it yesterday's
recording and it prints the text". If a step does not end in a demo, the step is
badly written.

**2. Code that talks to the internet lives only in `providers/`.**
Groq and Telegram are touched only in there. Everything else works on ordinary
strings, numbers and lists. This is the only architecture rule you need right
now, and it saves you when you want to test without burning API credits or when
you want to swap a provider.

**3. SQLite is the single source of truth.**
One file, `data/picatchup.db`. Delete it and you lose everything, so it gets
backed up. Everything else (Markdown files, search indexes) can be regenerated.

**4. The model decides nothing that matters.**
The AI writes text. It does not pick files to open, does not decide who may read
what, does not write SQL. Our code decides those. If the model went haywire
tomorrow, the worst damage is a badly written summary.

**5. English for code and docs. Italian only for what students read.**
Variables, functions, tables, comments, documentation: English. The recap text,
the answers, and the bot's replies: Italian, because that is the product.

---

## The six steps

Each step fits in a few afternoons. The advice: do **one at a time, both of you
on the same one**, at least for the first three. You are two people, not two
teams, and splitting up this early costs more than it gives.

---

### Step 1 — A skeleton that runs

**What you build:** the minimum for the project to exist as a real program.

- `pyproject.toml` — so that `pip install -e .` works (today it is missing!)
- `.env.example` and `src/picatchup/config.py` — reads the Groq key and the
  Telegram token from a `.env` file that must **not** go into git
- `src/picatchup/db.py` — opens SQLite and runs `schema.sql`
- `schema.sql` — the 5 tables (see "The database" below)
- `src/picatchup/cli.py` — the `picatchup` command, for now with only `init`
- `.gitignore` — `data/`, `.env`, `*.db`, `__pycache__/`

**Done when:** on a clean machine, `pip install -e .` then `picatchup init`
creates `data/picatchup.db`, and running `picatchup init` a second time does not
complain and does not break anything.

**Do not build yet:** a migration system with checksums. When you change the
schema at this stage, delete the database and run `init` again. There is nothing
precious in it yet.

---

### Step 2 — Audio to text

**What you build:** transcription.

- `src/picatchup/providers/groq_audio.py` — calls Groq Whisper, returns text
  plus segments with millisecond timings
- `src/picatchup/transcribe.py` — takes a file, sends it to the provider, saves
  the lesson and its chunks into the database
- command: `picatchup transcribe <file.m4a> --date 2026-09-15`

Two real problems to handle here: long files (Whisper has a size limit, so cut
the audio with `ffmpeg` into ~10 minute pieces and reassemble the timings by
adding the offset), and the fact that a long lesson takes a few minutes to come
back.

**Done when:** you take a real recording of one of your own lessons and
`picatchup transcribe` prints what the teacher actually said. This is the first
"wow" moment of the project — do not skip it, do not postpone it.

**Do not build yet:** clever text cleanup, quality scoring, overlap
de-duplication. Whisper gets a few words wrong; the summary survives it.

---

### Step 3 — Text to summary

**What you build:** the daily recap.

- `prompts/recap.md` — the prompt, as a text file separate from the code, so you
  can improve it without touching Python. Its content is written in Italian
  because the recap it produces is for Italian students.
- `src/picatchup/providers/groq_text.py`
- `src/picatchup/recap.py` — takes a date, loads that day's lessons, builds the
  prompt, saves the recap
- command: `picatchup recap 2026-09-15`

**Done when:** you read the recap of your real lesson and think "yes, that is
how it went". Then you show it to a classmate who was absent and they understand
what they missed. If the output is bad, the problem is almost always the prompt,
not the code — iterating on it is the fun part.

**Do not build yet:** map-reduce (summarize each lesson, then summarize the
summaries). That is needed when the text does not fit in one call. One school
day fits. Add it the day you get a length error.

---

### Step 4 — The bot sends it

**What you build:** Telegram.

- `src/picatchup/providers/telegram.py` — long polling and sending messages
- `src/picatchup/bot.py` — the loop that reads updates and replies
- commands: `/start`, `/aiuto`, `/riassunto`, `/privacy` (Italian names, because
  students type them)
- the **allowlist**: if your Telegram id is not in the `users` table with
  `active=1`, the bot replies with one neutral sentence and nothing else

Three things that look like details and are not: Telegram splits messages over
4096 characters (do the splitting yourself, cleanly, between paragraphs); if the
bot is added to a group it must leave (recaps belong to the class, not the
world); and if you send the same recap twice people get annoyed, which is what
the `deliveries` table prevents.

**Done when:** the recap arrives on your phone. From here the project is real
and you can show it to people.

---

### Step 5 — Questions

**What you build:** the part that makes PiCatchUp more than "a summary".

- `src/picatchup/search.py` — FTS5 search over lesson chunks
- `prompts/answer.md`
- `src/picatchup/qa.py` — question → find the best 5 chunks → hand them to the
  model → answer
- the bot replies to ordinary messages (the ones that are not commands)

The answer has a fixed shape and it must be respected:

```
📖 Dalla lezione
   [what the teacher actually said, from the retrieved chunks]
   (Matematica, 15/09, minute 23)

💡 Spiegazione aggiuntiva
   [what the model adds on its own]
```

That separator line is the heart of the project. Without it you can no longer
tell what the teacher said from what the AI invented — and at that point it is
just another AI chat. If the search finds nothing relevant, the bot **says it
cannot find it** and does not improvise.

**Done when:** you ask "cosa ha detto sulle derivate?" and it answers with the
right content and the right minute.

---

### Step 6 — It runs by itself

**What you build:** not having to launch it by hand.

- `src/picatchup/daily.py` — a `picatchup daily` command that does the whole
  loop: look in `data/inbox/`, transcribe each new file, build the day's recap,
  send it to everyone
- put it in `cron` (or a systemd timer) at 15:00
- delete audio after 7 days
- a nightly backup: `sqlite3 data/picatchup.db "VACUUM INTO 'backups/...'"`

**Done when:** for a whole week nobody touches anything and the recaps still
arrive.

**Do not build yet:** a job queue with leases. `daily.py` is a `for` loop over a
list of files. If it crashes halfway, the next run picks up what is missing (the
tables have the constraints that stop anything being duplicated). That covers
95% of the cases at 5% of the complexity.

---

## The database

Five application tables, one virtual search table, and one operational bot-state
singleton. The full schema lives in
`schema.sql`; here is what each one is for.

| Table | What it holds | Written in |
|---|---|---|
| `lessons` | one row per recording: date, subject, audio path, transcribed text | step 2 |
| `chunks` | the text cut into ~700 character pieces with start milliseconds | step 2 |
| `chunks_fts` | the full-text search index (FTS5 virtual table) | step 5 |
| `recaps` | one summary per day | step 3 |
| `users` | who may use the bot: Telegram id, name, admin flag, active flag | step 4 |
| `deliveries` | which recap has already been sent to whom | step 4 |
| `bot_state` | durable Telegram long-poll offset | step 4 |

Two details worth adding immediately, because retrofitting them costs more:

- `UNIQUE(recap_id, user_id)` on `deliveries` — this is what stops the same
  recap being sent twice, with no logic needed anywhere
- `UNIQUE(audio_sha256)` on `lessons` — this is what stops the same file being
  transcribed twice (and paid for twice)

The `sha256` checksum is just a very long number computed from the file's
contents: if two files have the same one, they are the same file, even under
different names.

There is **no** `class_id` column. You are one class. The day there are two, you
add the column and update the queries: half a day of work on a small codebase,
versus weeks of complication paid up front for a problem that may never arrive.

---

## The serious part: privacy

This one does not get simplified, because it is not technical complexity: you
are recording real people, minors, and your teachers.

**Before recording anything real:**

- [ ] ask each subject teacher whether they accept being recorded
- [ ] ask your classmates (and for minors, their parents)
- [ ] raise it with a teacher you trust or with the head of school
- [ ] write `docs/PRIVACY.md` — in plain Italian, since that is who reads it —
      covering what the system does, where the data goes (Groq: servers in the
      US), and how to ask to be deleted

Until then, work **only** on recordings of yourselves talking.

**In the code, four things:**

1. audio is deleted after 7 days (the text stays)
2. lesson content never goes into the logs
3. the API key never goes into logs or into git — it lives in `.env`, and `.env`
   is in `.gitignore`
4. only users in `users` with `active=1` receive anything

If someone asks to be removed: `picatchup user remove <id>`, and they are gone.

---

## Working with coding agents

They help, but a simple plan changes how you use them.

**Ask for one piece at a time, not "implement Step 3".** Ask: "write
`recap.py`: it takes a date, loads that day's lessons from the database, builds
the prompt from `prompts/recap.md`, calls the text provider, saves into
`recaps`".

**Make it explain before you accept.** If you do not understand a file the agent
wrote, ask it to walk through it line by line or to rewrite it more simply. Code
you do not understand is code you cannot fix when the bot sends nothing at 15:00
on a Monday — and that will happen.

**Be suspicious when it adds things you did not ask for.** An agent left free
produces exactly plan v1: abstractions, interfaces, exception hierarchies. If it
proposes an `AbstractProviderFactory`, the answer is no.

**Run the code before you commit.** Always. Even when it looks obvious.

---

## What we removed, and when to bring it back

None of it was thrown away: it is in `docs/archive/PLAN-v1.md`, already
designed, ready to pick up **when a real problem asks for it**.

| Thing | Bring it back when |
|---|---|
| Job queue with leases and retries | `daily.py` is not enough: too many files, too slow, needs parallelism |
| Migrations with checksums | there is real data you cannot lose and you need to change the schema |
| `class_id` and multi-class | a second class joins |
| Fake providers for offline tests | tests cost too much in API credits or are too slow |
| Hardened systemd units | it runs on a server exposed to the internet |
| Obsidian export | you actually want it (it is a half-day step) |
| Map-reduce recap | the model starts returning length errors |
| Expiring enrollment codes | you are more than ~15 people and adding users by hand is annoying |

The rule: **problem first, then solution.** Plan v1 did the opposite, and that
is why it was unmanageable.

---

## Current state

Steps 1–6 are implemented for the Raspberry Pi MVP: local recording, timed
transcription, recap generation, allowlisted Telegram long polling, FTS5-grounded
questions, retention, backups, diagnostics, OLED/LED/button control and systemd
automation. Automated tests use fake providers and hardware backends.

The remaining acceptance work is physical rather than an unfinished software
step: complete `docs/RPI_CHECKLIST.md` on the target Pi before classroom use and
obtain the authorizations described in `docs/PRIVACY.md`.
