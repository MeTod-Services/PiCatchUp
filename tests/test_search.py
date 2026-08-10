from __future__ import annotations

import sqlite3

from picatchup import search
from picatchup.util import new_id, utc_now


def add_chunk(
    conn: sqlite3.Connection,
    *,
    date: str,
    subject: str | None,
    start_ms: int,
    text: str,
) -> str:
    lesson_id = new_id()
    chunk_id = new_id()
    conn.execute(
        "INSERT INTO lessons (id, lesson_date, subject, audio_sha256, text, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (lesson_id, date, subject, new_id(), text, utc_now()),
    )
    conn.execute(
        "INSERT INTO chunks (id, lesson_id, idx, start_ms, end_ms, text)"
        " VALUES (?, ?, 0, ?, ?, ?)",
        (chunk_id, lesson_id, start_ms, start_ms + 30_000, text),
    )
    conn.execute("INSERT INTO chunks_fts (chunk_id, text) VALUES (?, ?)", (chunk_id, text))
    conn.commit()
    return chunk_id


def test_returns_the_best_chunk_with_its_lesson_metadata(conn: sqlite3.Connection) -> None:
    best = add_chunk(
        conn,
        date="2026-09-15",
        subject="Matematica",
        start_ms=1_380_000,
        text="La derivata descrive la variazione di una funzione. Studiamo la funzione derivata.",
    )
    add_chunk(
        conn,
        date="2026-09-14",
        subject="Storia",
        start_ms=0,
        text="Una variazione nella società industriale.",
    )

    hits = search.search(conn, "Che cosa è la funzione derivata?")

    assert hits[0].chunk_id == best
    assert (hits[0].subject, hits[0].lesson_date, hits[0].start_ms) == (
        "Matematica",
        "2026-09-15",
        1_380_000,
    )
    assert hits[0].score >= hits[-1].score


def test_searches_across_multiple_lessons(conn: sqlite3.Connection) -> None:
    add_chunk(
        conn,
        date="2026-09-15",
        subject="Fisica",
        start_ms=300_000,
        text="L'energia cinetica dipende dalla velocità.",
    )
    add_chunk(
        conn,
        date="2026-09-16",
        subject="Scienze",
        start_ms=600_000,
        text="L'energia passa tra i livelli della catena alimentare.",
    )

    hits = search.search(conn, "Cosa avete detto dell'energia?", limit=5)

    assert {hit.subject for hit in hits} == {"Fisica", "Scienze"}
    assert len({hit.lesson_id for hit in hits}) == 2


def test_match_query_never_uses_user_fts_syntax(conn: sqlite3.Connection) -> None:
    chunk_id = add_chunk(
        conn,
        date="2026-09-15",
        subject="Matematica",
        start_ms=0,
        text="Oggi studiamo le derivate.",
    )

    query = search.build_match_query('derivate* NEAR(storia) NOT "fisica" OR 1=1 --')
    hits = search.search(conn, 'derivate* NEAR(storia) NOT "fisica" OR 1=1 --')

    assert "*" not in query and "(" not in query and "=" not in query
    assert '"near"' in query and '"not"' in query and '"or"' in query
    assert [hit.chunk_id for hit in hits] == [chunk_id]


def test_fts_tokenizer_matches_without_accents(conn: sqlite3.Connection) -> None:
    chunk_id = add_chunk(
        conn,
        date="2026-09-15",
        subject=None,
        start_ms=0,
        text="Il perché della velocità istantanea.",
    )

    assert search.search(conn, "velocita")[0].chunk_id == chunk_id
