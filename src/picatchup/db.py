"""The database: opening it and creating the tables.

Queries do not live here. Each pipeline module writes its own SQL, so you can
read a module and see exactly what it touches.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the database, creating the file and its folder if needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)

    # Rows come back as row["subject"] instead of row[2].
    conn.row_factory = sqlite3.Row
    # WAL lets the bot read while the pipeline writes, instead of erroring.
    conn.execute("PRAGMA journal_mode = WAL")
    # Off by default in SQLite. Without it, REFERENCES in schema.sql is a comment.
    conn.execute("PRAGMA foreign_keys = ON")
    # If another process holds the write lock, wait 5s instead of failing at once.
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
    """Run schema.sql. Safe to call on an existing database."""
    conn.executescript(SCHEMA_PATH.read_text("utf-8"))
    conn.commit()
