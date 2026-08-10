from __future__ import annotations

import logging
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from picatchup import backup, db
from picatchup.backup import BackupError
from picatchup.config import Settings
from picatchup.util import new_id, utc_now


def _add_lesson(settings: Settings, *, subject: str) -> None:
    with db.connect(settings.db_path) as connection:
        db.create_tables(connection)
        connection.execute(
            "INSERT INTO lessons "
            "(id, lesson_date, subject, audio_sha256, created_at) VALUES (?, ?, ?, ?, ?)",
            (new_id(), "2026-09-15", subject, new_id(), utc_now()),
        )
        connection.commit()


def test_backup_contains_a_consistent_copy_of_the_database(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    _add_lesson(settings, subject="Matematica")
    caplog.set_level(logging.INFO, logger="picatchup.backup")

    path = backup.create_backup(settings, now=datetime(2026, 9, 15, 22, 30, tzinfo=UTC))

    assert path == settings.backup_dir / "picatchup-20260915-223000.db"
    with sqlite3.connect(path) as restored:
        assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert restored.execute("SELECT subject FROM lessons").fetchone() == ("Matematica",)
    assert "backup_created" in caplog.text


def test_backup_rotation_keeps_only_the_newest_configured_count(settings: Settings) -> None:
    _add_lesson(settings, subject="Fisica")
    settings = replace(settings, backup_retention_count=2)

    oldest = backup.create_backup(settings, now=datetime(2026, 9, 13, tzinfo=UTC))
    middle = backup.create_backup(settings, now=datetime(2026, 9, 14, tzinfo=UTC))
    newest = backup.create_backup(settings, now=datetime(2026, 9, 15, tzinfo=UTC))

    assert not oldest.exists()
    assert sorted(settings.backup_dir.glob("picatchup-*.db")) == [middle, newest]


def test_a_failed_copy_never_leaves_a_visible_backup(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_lesson(settings, subject="Storia")

    def fail_after_partial_copy(_source: Path, destination: Path) -> None:
        destination.write_bytes(b"partial")
        raise BackupError("integrity failed")

    monkeypatch.setattr(backup, "_copy_and_verify", fail_after_partial_copy)

    with pytest.raises(BackupError, match="integrity failed"):
        backup.create_backup(settings, now=datetime(2026, 9, 15, tzinfo=UTC))

    assert list(settings.backup_dir.iterdir()) == []
