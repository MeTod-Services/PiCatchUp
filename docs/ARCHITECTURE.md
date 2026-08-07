# Architecture

The point of this page is that you can hold the whole system in your head. If
you ever cannot, the system has grown wrong.

The long version of this document, describing a much bigger system, is archived
in `docs/archive/ARCHITECTURE-v1.md`.

---

## Four pieces

Every part of PiCatchUp belongs to exactly one of these.

**1. The warehouse — `db.py` + `schema.sql`**
One SQLite file. It holds lessons, chunks, recaps, users, deliveries. Nothing
else in the program keeps state; if it is not in the database, it did not
happen.

**2. The outside world — `providers/`**
Groq and Telegram. This is the only place allowed to make a network call, and
the only place allowed to `import groq` or touch HTTP. Every function here takes
ordinary values in and gives ordinary values back.

**3. The assembly line — `transcribe.py`, `recap.py`, `search.py`, `qa.py`**
The actual work. Each module takes something from the warehouse, does one
transformation to it (possibly asking the outside world for help), and puts the
result back. These modules know nothing about Groq or Telegram — they are handed
a provider and call it.

**4. The face — `cli.py`, `bot.py`, `daily.py`**
How a human or a clock starts the work. The CLI is for you; the bot is for your
classmates; `daily.py` is for cron. None of these three contain real logic:
they parse an input, call the assembly line, print or send the result.

---

## The picture

```
      you                 classmates              cron, 15:00
       |                       |                       |
    cli.py                  bot.py                 daily.py          THE FACE
       |                       |                       |
       +-----------+-----------+-----------+-----------+
                               |
    transcribe.py     recap.py     search.py     qa.py           ASSEMBLY LINE
                               |
              +----------------+----------------+
              |                                 |
           db.py                          providers/            WAREHOUSE +
      data/picatchup.db              groq_audio, groq_text,     OUTSIDE WORLD
                                          telegram
```

Arrows only ever point downward. The face calls the assembly line; the assembly
line calls the warehouse and the providers. Nothing calls back upward — `db.py`
never imports `bot.py`, `groq_text.py` never imports `recap.py`.

That single rule is what keeps the project understandable as it grows. When you
add a file, ask which of the four layers it belongs to. If the answer is "two of
them", it should be two files.

---

## Why providers are separated

`recap.py` does not call Groq directly. It receives something with a `generate`
method and calls that. This costs one extra parameter and buys three things:

- you can run and test the pipeline without spending API credits
- when Groq changes its SDK, one file changes
- when you want a different model provider, you write one new file and nothing
  else moves

That is the whole reason. Not purity — those three things.

---

## The daily data flow

```
audio file in data/inbox/
   -> sha256 checksum   (have we seen this file before?)
   -> ffmpeg: cut into ~10 minute pieces if it is large
   -> Groq Whisper: each piece to text with timings
   -> reassemble into one timeline, save as a lessons row
   -> cut the text into ~700 character chunks, save + index in FTS5
   -> Groq text model: all of today's lessons to one recap
   -> Telegram: one message per active user, one deliveries row each
```

And, separately, whenever a student sends a message:

```
question
   -> FTS5 search over that class's chunks -> best 5
   -> prompt = question + those 5 chunks only
   -> Groq text model
   -> answer, split into "from the lesson" and "extra explanation"
   -> send
```

The model never sees a whole transcript and never sees the database. It sees a
question and five paragraphs.

---

## Ideas worth knowing

Short definitions of things that come up in the plan.

**Checksum (sha256)** — a long number computed from a file's contents. Same
contents, same number. We use it to notice we already transcribed a file.

**Idempotent** — running it twice does the same thing as running it once. The
whole reason `daily.py` can be safely re-run after a crash. We get it mostly for
free from the `UNIQUE` constraints in the schema rather than from clever code.

**FTS5** — SQLite's built-in full-text search. It ranks paragraphs by how well
they match your search words. No extra software to install, no vector database.

**Long polling** — how the bot receives messages. It asks Telegram "anything
new?", Telegram holds the connection open until there is something or ~30
seconds pass, then it asks again. Nothing needs to reach your machine from the
internet, so there is no server to expose.

**Grounding** — giving the model the real source text and telling it to answer
only from that. It is what makes an answer checkable instead of plausible.

**Prompt injection** — a transcript could contain someone saying "ignore your
instructions". We never let model output become a file path, a database query,
or a permission decision, so the worst case stays "a weird paragraph".
