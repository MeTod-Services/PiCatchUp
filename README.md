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
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env      # then fill in your keys
picatchup init
```

`picatchup init` creates `data/` and the database. Running it again is harmless.

```sh
pytest          # tests
ruff check .    # lint
mypy            # types
```

## Where to look

- `PLAN.md` — what we are building, in six steps
- `docs/ARCHITECTURE.md` — how the pieces fit together
- `src/picatchup/schema.sql` — the whole database, commented

