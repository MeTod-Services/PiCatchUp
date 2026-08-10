"""A day's lessons in, one summary out.

If the recap reads badly, the problem is almost always in `prompts/recap.md`
and not in this file. This file only decides which lessons belong to the day
and where the answer is stored.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from picatchup.config import Settings, require
from picatchup.errors import PiCatchUpError
from picatchup.providers import groq_text
from picatchup.util import new_id, utc_now

PROMPT_PATH = Path(__file__).parent / "prompts" / "recap.md"

Writer = Callable[[str], str]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Recap:
    id: str
    recap_date: str
    text: str
    model: str
    #  True when we returned what was already stored instead of paying again.
    reused: bool


def make_recap(
    conn: sqlite3.Connection,
    settings: Settings,
    recap_date: str,
    *,
    writer: Writer | None = None,
    force: bool = False,
) -> Recap:
    """Write (or rewrite) the summary of one school day and store it."""
    existing = conn.execute(
        "SELECT id, recap_date, text, model, created_at FROM recaps WHERE recap_date = ?",
        (recap_date,),
    ).fetchone()
    if existing is not None and not force:
        changed = conn.execute(
            "SELECT 1 FROM lessons"
            " WHERE lesson_date = ? AND text IS NOT NULL AND text != '' AND created_at >= ?"
            " LIMIT 1",
            (recap_date, existing["created_at"]),
        ).fetchone()
        if changed is None:
            return Recap(
                id=existing["id"],
                recap_date=existing["recap_date"],
                text=existing["text"],
                model=existing["model"],
                reused=True,
            )

    lessons = conn.execute(
        "SELECT subject, duration_ms, text FROM lessons"
        " WHERE lesson_date = ? AND text IS NOT NULL AND text != ''"
        " ORDER BY created_at",
        (recap_date,),
    ).fetchall()
    if not lessons:
        raise PiCatchUpError(f"there is no transcribed lesson for {recap_date}")

    model = require(settings.text_model, "TEXT_MODEL")
    writer = writer or _groq(settings, model)
    prompt = build_prompt(load_template(), recap_date, lessons)

    log.info("writing the recap for %s from %d lesson(s)", recap_date, len(lessons))
    text = writer(prompt).strip()
    if not text:
        raise PiCatchUpError("the text provider returned an empty recap")

    recap_id = existing["id"] if existing is not None else new_id()
    with conn:
        if existing is not None:
            # A delivery row proves receipt of the previous text, not this new
            # version. Invalidate it atomically with replacement so normal send
            # delivers the regenerated recap once.
            conn.execute("DELETE FROM deliveries WHERE recap_id = ?", (recap_id,))
        conn.execute(
            "INSERT INTO recaps (id, recap_date, text, model, created_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (recap_date) DO UPDATE SET text = excluded.text, model = excluded.model,"
            " created_at = excluded.created_at",
            (recap_id, recap_date, text, model, utc_now()),
        )
    log.info("recap_generated date=%s lessons=%d", recap_date, len(lessons))
    return Recap(id=recap_id, recap_date=recap_date, text=text, model=model, reused=False)


def _groq(settings: Settings, model: str) -> Writer:
    api_key = require(settings.groq_api_key, "GROQ_API_KEY")

    def write(prompt: str) -> str:
        return groq_text.generate(prompt, api_key=api_key, model=model)

    return write


def load_template(path: Path = PROMPT_PATH) -> str:
    """Read the prompt file, dropping the note to ourselves at the top of it."""
    if not path.is_file():
        raise PiCatchUpError(f"the prompt file is missing: {path}")
    text = re.sub(r"<!--.*?-->", "", path.read_text("utf-8"), flags=re.DOTALL)
    return re.sub(r"\A\s*-{5,}\s*", "", text).strip()


def build_prompt(template: str, recap_date: str, lessons: Sequence[sqlite3.Row]) -> str:
    """Fill the placeholders. Plain replacement, so the prompt can use braces."""
    return template.replace("{{date}}", recap_date).replace(
        "{{lessons}}", "\n\n".join(_describe(lesson) for lesson in lessons)
    )


def _describe(lesson: sqlite3.Row) -> str:
    heading = lesson["subject"] or "Materia non indicata"
    minutes = round((lesson["duration_ms"] or 0) / 60000)
    if minutes:
        heading = f"{heading} ({minutes} minuti)"
    return f"### {heading}\n{lesson['text']}"
