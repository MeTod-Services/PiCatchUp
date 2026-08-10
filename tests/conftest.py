"""Fixtures shared by the pipeline tests.

Everything here is offline: no test in this project may call Groq or Telegram.
The providers are handed in as ordinary functions, which is the whole reason
they are separated in the first place.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from picatchup import db
from picatchup.config import Settings, load_settings


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """A complete configuration pointing at a throwaway folder."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TEXT_MODEL", "test-text-model")

    values = load_settings()
    for folder in (values.inbox_dir, values.audio_dir, values.backup_dir):
        folder.mkdir(parents=True, exist_ok=True)
    return values


@pytest.fixture
def conn(settings: Settings) -> Iterator[sqlite3.Connection]:
    connection = db.connect(settings.db_path)
    db.create_tables(connection)
    yield connection
    connection.close()
