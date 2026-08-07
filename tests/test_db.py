from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from picatchup import db
from picatchup.util import new_id, utc_now

TABLES = {"lessons", "chunks", "chunks_fts", "recaps", "users", "deliveries"}


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = db.connect(tmp_path / "nested" / "picatchup.db")
    db.create_tables(connection)
    yield connection
    connection.close()


def add_lesson(conn: sqlite3.Connection, sha256: str) -> str:
    lesson_id = new_id()
    conn.execute(
        "INSERT INTO lessons (id, lesson_date, audio_sha256, created_at) VALUES (?, ?, ?, ?)",
        (lesson_id, "2026-09-15", sha256, utc_now()),
    )
    return lesson_id


def test_creates_every_table(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
    assert {row["name"] for row in rows} >= TABLES


def test_running_init_twice_keeps_the_data(tmp_path: Path) -> None:
    path = tmp_path / "picatchup.db"
    first = db.connect(path)
    db.create_tables(first)
    add_lesson(first, "abc123")
    first.commit()
    first.close()

    second = db.connect(path)
    db.create_tables(second)  # as if someone ran `picatchup init` again
    assert second.execute("SELECT COUNT(*) FROM lessons").fetchone()[0] == 1
    second.close()


def test_the_same_audio_cannot_be_stored_twice(conn: sqlite3.Connection) -> None:
    add_lesson(conn, "abc123")

    with pytest.raises(sqlite3.IntegrityError):
        add_lesson(conn, "abc123")


def test_a_chunk_needs_a_lesson_that_exists(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO chunks (id, lesson_id, idx, start_ms, end_ms, text)"
            " VALUES (?, 'no-such-lesson', 0, 0, 1000, 'ciao')",
            (new_id(),),
        )


def test_a_recap_reaches_each_person_only_once(conn: sqlite3.Connection) -> None:
    recap_id, user_id = new_id(), new_id()
    conn.execute(
        "INSERT INTO recaps (id, recap_date, text, model, created_at) VALUES (?, ?, ?, ?, ?)",
        (recap_id, "2026-09-15", "riassunto", "some-model", utc_now()),
    )
    conn.execute(
        "INSERT INTO users (id, telegram_user_id, display_name, created_at) VALUES (?, ?, ?, ?)",
        (user_id, 12345, "Tod", utc_now()),
    )
    conn.execute(
        "INSERT INTO deliveries (recap_id, user_id, sent_at) VALUES (?, ?, ?)",
        (recap_id, user_id, utc_now()),
    )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO deliveries (recap_id, user_id, sent_at) VALUES (?, ?, ?)",
            (recap_id, user_id, utc_now()),
        )


def test_search_ignores_accents(conn: sqlite3.Connection) -> None:
    lesson_id = add_lesson(conn, "abc123")
    chunk_id = new_id()
    conn.execute(
        "INSERT INTO chunks (id, lesson_id, idx, start_ms, end_ms, text) VALUES (?, ?, 0, 0, 5, ?)",
        (chunk_id, lesson_id, "Il perché della velocità istantanea"),
    )
    conn.execute(
        "INSERT INTO chunks_fts (chunk_id, text) VALUES (?, ?)",
        (chunk_id, "Il perché della velocità istantanea"),
    )

    hits = conn.execute("SELECT chunk_id FROM chunks_fts WHERE text MATCH 'velocita'").fetchall()
    assert [row["chunk_id"] for row in hits] == [chunk_id]
