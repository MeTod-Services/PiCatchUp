"""Grounded answers built from FTS5 lesson evidence."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from picatchup import search
from picatchup.config import Settings, require
from picatchup.domain.provider_types import SearchHit
from picatchup.errors import PiCatchUpError
from picatchup.providers import groq_text

PROMPT_PATH = Path(__file__).parent / "prompts" / "answer.md"
NO_EVIDENCE: Final = "Non ho trovato informazioni pertinenti nelle lezioni registrate."
LESSON_HEADING: Final = "📖 Dalla lezione"
EXTRA_HEADING: Final = "💡 Spiegazione aggiuntiva"

Writer = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class Answer:
    """The rendered reply and the immutable evidence used to produce it."""

    text: str
    hits: tuple[SearchHit, ...]


def answer_question(
    conn: sqlite3.Connection,
    settings: Settings,
    question: str,
    *,
    writer: Writer | None = None,
    limit: int = 5,
) -> Answer:
    """Retrieve evidence and answer one question without inventing citations."""
    question = question.strip()
    if len(question) > 1000:
        raise PiCatchUpError("la domanda è troppo lunga")
    hits = search.search(conn, question, limit=limit)
    if not hits or not search.has_meaningful_evidence(question, hits):
        # Do this before constructing the real provider: a miss costs no API
        # call and does not even require Groq credentials.
        return Answer(text=NO_EVIDENCE, hits=())

    prompt = build_prompt(load_template(), question, hits)
    write = writer if writer is not None else _groq(settings)
    lesson_text, extra_text = _split_sections(write(prompt))
    text = (
        f"{LESSON_HEADING}\n{lesson_text}\n\n"
        f"🔎 Fonti: {_citations(hits)}\n\n"
        f"{EXTRA_HEADING}\n{extra_text}"
    )
    return Answer(text=text, hits=hits)


def _groq(settings: Settings) -> Writer:
    api_key = require(settings.groq_api_key, "GROQ_API_KEY")
    model = require(settings.text_model, "TEXT_MODEL")

    def write(prompt: str) -> str:
        return groq_text.generate(prompt, api_key=api_key, model=model)

    return write


def load_template(path: Path = PROMPT_PATH) -> str:
    """Load the packaged prompt while dropping its maintainer comment."""
    if not path.is_file():
        raise PiCatchUpError(f"the prompt file is missing: {path}")
    text = re.sub(r"<!--.*?-->", "", path.read_text("utf-8"), flags=re.DOTALL)
    return re.sub(r"\A\s*-{5,}\s*", "", text).strip()


def build_prompt(template: str, question: str, hits: Sequence[SearchHit]) -> str:
    """Put only the retrieved chunks, never a full transcript, in the prompt."""
    sources = "\n\n".join(_describe_hit(number, hit) for number, hit in enumerate(hits, 1))
    return template.replace("{{question}}", question).replace("{{sources}}", sources)


def _describe_hit(number: int, hit: SearchHit) -> str:
    return f"<fonte {number}>\n{hit.text}\n</fonte {number}>"


def _split_sections(generated: str) -> tuple[str, str]:
    """Normalize model output so the two trust levels are always visible."""
    text = generated.strip()
    lesson_at = text.find(LESSON_HEADING)
    extra_at = text.find(EXTRA_HEADING)
    if 0 <= lesson_at < extra_at:
        lesson = text[lesson_at + len(LESSON_HEADING) : extra_at]
        extra = text[extra_at + len(EXTRA_HEADING) :]
    else:
        # Never relabel unstructured model prose as something said in class.
        # Discard it instead: preserving the trust boundary matters more than
        # returning a plausible-looking answer.
        lesson = "Non è stato possibile formulare la parte basata sulla lezione."
        extra = "Nessuna spiegazione aggiuntiva."

    lesson = _without_model_citations(lesson)
    extra = _without_model_citations(extra)
    for heading in (LESSON_HEADING, EXTRA_HEADING):
        lesson = lesson.replace(heading, "")
        extra = extra.replace(heading, "")
    lesson = lesson.strip()
    extra = extra.strip()
    return (
        lesson or "Non è stato possibile formulare la parte basata sulla lezione.",
        extra or "Nessuna spiegazione aggiuntiva.",
    )


def _without_model_citations(text: str) -> str:
    """Discard source lines from the model; the database supplies them below."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^(?:🔎\s*)?font[ei]\s*:", stripped, flags=re.IGNORECASE):
            continue
        if re.match(r"^\(.*\bminut(?:o|i)\s+\d+.*\)$", stripped, flags=re.IGNORECASE):
            continue
        lines.append(line)
    return "\n".join(lines)


def _citations(hits: Sequence[SearchHit]) -> str:
    citations: list[str] = []
    seen: set[tuple[str, str, int]] = set()
    for hit in hits:
        subject = hit.subject or "Materia non indicata"
        minute = hit.start_ms // 60_000
        key = (subject, hit.lesson_date, minute)
        if key in seen:
            continue
        seen.add(key)
        citations.append(f"({subject}, {_short_date(hit.lesson_date)}, minuto {minute})")
    return "; ".join(citations)


def _short_date(lesson_date: str) -> str:
    try:
        year, month, day = lesson_date.split("-")
    except ValueError:
        return lesson_date
    if len(year) == 4 and len(month) == 2 and len(day) == 2:
        return f"{day}/{month}"
    return lesson_date
