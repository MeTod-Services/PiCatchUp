"""Deterministic full-text retrieval over transcribed lesson chunks."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections.abc import Sequence
from typing import Final

from picatchup.domain.provider_types import SearchHit
from picatchup.errors import PiCatchUpError

# These words carry little retrieval value in an Italian question. Keeping this
# list here makes query construction deterministic and easy to inspect.
_STOP_WORDS: Final = frozenset(
    {
        "a",
        "abbiamo",
        "ad",
        "agli",
        "ai",
        "al",
        "all",
        "alla",
        "alle",
        "allo",
        "anche",
        "avete",
        "che",
        "chi",
        "ci",
        "cio",
        "come",
        "con",
        "cosa",
        "da",
        "dagli",
        "dai",
        "dal",
        "dall",
        "dalla",
        "dalle",
        "dallo",
        "dei",
        "del",
        "dell",
        "della",
        "delle",
        "dello",
        "detto",
        "di",
        "dire",
        "dove",
        "e",
        "ed",
        "era",
        "erano",
        "fatto",
        "gli",
        "ha",
        "hai",
        "hanno",
        "ho",
        "i",
        "il",
        "in",
        "io",
        "la",
        "le",
        "lezione",
        "lo",
        "ma",
        "mi",
        "nei",
        "nel",
        "nell",
        "nella",
        "nelle",
        "nello",
        "o",
        "parlare",
        "parlato",
        "per",
        "prof",
        "professore",
        "qual",
        "quale",
        "quali",
        "quando",
        "quanto",
        "quella",
        "quelle",
        "quelli",
        "quello",
        "questa",
        "queste",
        "questi",
        "questo",
        "si",
        "spiegare",
        "spiegato",
        "stata",
        "state",
        "stati",
        "stato",
        "sono",
        "su",
        "sui",
        "sul",
        "sull",
        "sulla",
        "sulle",
        "sullo",
        "tra",
        "trattato",
        "un",
        "una",
        "uno",
        "visto",
    }
)
_WORD = re.compile(r"[^\W_]+", flags=re.UNICODE)
_MAX_TERMS: Final = 12


def build_match_query(question: str) -> str:
    """Turn natural Italian into an FTS query without accepting FTS syntax.

    Only Unicode word tokens survive. Each token is quoted, so punctuation,
    quotes, wildcards and words such as ``OR`` supplied by a user can never be
    interpreted as MATCH operators.
    """
    return " OR ".join(f'"{term}"' for term in query_terms(question))


def query_terms(question: str) -> tuple[str, ...]:
    """Return bounded, unique content terms from an Italian question."""
    if not isinstance(question, str) or not question.strip():
        raise PiCatchUpError("la domanda è vuota")

    terms: list[str] = []
    seen: set[str] = set()
    for token in _WORD.findall(question.casefold()):
        comparison = _fold(token)
        if comparison in _STOP_WORDS or comparison in seen:
            continue
        seen.add(comparison)
        terms.append(token)
        if len(terms) == _MAX_TERMS:
            break

    if not terms:
        raise PiCatchUpError("la domanda non contiene parole cercabili")
    return tuple(terms)


def has_meaningful_evidence(question: str, hits: Sequence[SearchHit]) -> bool:
    """Reject broad OR matches that cover too little of a multi-term question."""
    terms = {_fold(term) for term in query_terms(question)}
    required = 1 if len(terms) == 1 else max(2, (len(terms) + 1) // 2)
    for hit in hits:
        hit_terms = {_fold(token) for token in _WORD.findall(hit.text.casefold())}
        if len(terms & hit_terms) >= required:
            return True
    return False


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(character for character in normalized if not unicodedata.combining(character))


def search(conn: sqlite3.Connection, question: str, *, limit: int = 5) -> tuple[SearchHit, ...]:
    """Return the best matching chunks, enriched with lesson citation data."""
    if limit < 1:
        raise PiCatchUpError("il limite di ricerca deve essere almeno 1")

    match_query = build_match_query(question)
    rows = conn.execute(
        "SELECT c.id AS chunk_id, c.lesson_id, l.lesson_date, l.subject,"
        " c.start_ms, c.text, bm25(chunks_fts) AS rank"
        " FROM chunks_fts"
        " JOIN chunks AS c ON c.id = chunks_fts.chunk_id"
        " JOIN lessons AS l ON l.id = c.lesson_id"
        " WHERE chunks_fts MATCH ?"
        " ORDER BY rank ASC, l.lesson_date DESC, c.lesson_id ASC, c.idx ASC, c.id ASC"
        " LIMIT ?",
        (match_query, limit),
    ).fetchall()

    return tuple(
        SearchHit(
            chunk_id=row["chunk_id"],
            lesson_id=row["lesson_id"],
            lesson_date=row["lesson_date"],
            subject=row["subject"],
            start_ms=row["start_ms"],
            text=row["text"],
            # FTS5 BM25 ranks smaller values first. Expose a conventional score
            # where a larger value means a stronger match.
            score=-float(row["rank"]),
        )
        for row in rows
    )
