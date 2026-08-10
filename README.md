```txt
     ._________________________________.
   .'                                  `-.
  /     .--------------------.            `----.__     .---.
 |     /      PiCatchUp       \                   `====|###|
 |     \                      /             ___.--'====|###|
  \     `--------.  .--------'       ___.--'           `---'
   `-_____________\/_______________.--'
```

# PiCatchUp - Catch Up when you're away.

Record the lesson, get a summary on Telegram in the evening, then ask it
questions and it answers citing the minute of the recording.

## Setup

```sh
sudo apt install ffmpeg   # recording, converting and cutting audio
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
# On the Raspberry Pi use: pip install -e ".[dev,rpi]"
cp .env.example .env      # then fill in your keys
picatchup init
```

`picatchup init` creates `data/` and the database. Running it again is harmless.

```sh
pytest          # tests
ruff check .    # lint
mypy            # types
```

## Using it

**Who receives the recap.** Nobody, until you say so.

```sh
picatchup user ids                  # write to the bot first, then read your id here
picatchup user add 424242 "Ada"
picatchup user list
picatchup user remove 424242        # and everything we sent them goes too
```

**Record.** Writes an mp3 into `data/inbox/`, and stops on Ctrl-C.

```sh
picatchup record --subject Matematica
picatchup record --minutes 55       # or let it stop by itself
```

**Record with one Raspberry Pi button.** Wire a normally-open push button
between **GPIO17** (BCM numbering, physical pin 11) and **GND** (for example
physical pin 6). The internal pull-up is enabled; do not connect the button to
5 V. Then run:

```sh
picatchup buttons
```

The first press starts recording and the next press stops it cleanly and saves
the mp3 in `data/inbox/`. Further presses repeat the cycle. Change
`GPIO_BUTTON_PIN` in `.env` to use another BCM pin. Stop the listener with
Ctrl-C; if it is recording, the file is finalized before exit.

If you record with your phone instead, just copy the file into `data/inbox/` —
any format ffmpeg reads is fine, and it gets converted. Name it
`2026-09-15 Matematica.m4a` and the date and subject are read from the name.

**Everything else, in one command.**

```sh
picatchup daily
```

It transcribes whatever is in the inbox, writes the day's recap, sends it on
Telegram, and deletes recordings older than `AUDIO_RETENTION_DAYS`. Running it
twice is safe: nothing is transcribed twice and nobody receives the same recap
twice. In `cron`, at 15:00:

```cron
0 15 * * 1-5 cd /home/pi/PiCatchUp && .venv/bin/picatchup daily
```

The same steps are also separate commands, which is what you want while you are
still improving the prompt:

```sh
picatchup transcribe "lezione.m4a" --subject Fisica
picatchup recap 2026-09-15 --force  # rewrite it after editing prompts/recap.md
picatchup send 2026-09-15
```

## Where to look

- `PLAN.md` — what we are building, in six steps
- `docs/ARCHITECTURE.md` — how the pieces fit together
- `src/picatchup/schema.sql` — the whole database, commented
- `src/picatchup/prompts/recap.md` — how the summary is asked for. Change this
  file, run `picatchup recap <date> --force`, read the result

Steps 1 to 4 of the plan are built, plus recording. Step 5 (asking questions)
and the bot's own message loop are not.

Before you record anyone but yourselves, read the privacy section of `PLAN.md`.
It is not a formality: they are real people, and some of them are minors.

