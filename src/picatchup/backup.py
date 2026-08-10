"""Consistent, rotated SQLite backups."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from picatchup.config import Settings
from picatchup.errors import PiCatchUpError

log = logging.getLogger(__name__)

_BACKUP_PREFIX = "picatchup-"
_BACKUP_GLOB = f"{_BACKUP_PREFIX}*.db"


class BackupError(PiCatchUpError):
    """A database snapshot could not be created or verified."""


def create_backup(settings: Settings, *, now: datetime | None = None) -> Path:
    """Create and verify a consistent snapshot, then rotate old snapshots.

    SQLite's backup API takes a transactionally consistent copy even when the
    source database uses WAL and another process is reading it.  The copy is
    first written under a temporary name in the backup directory; it only
    becomes visible under its final name after ``integrity_check`` succeeds.
    """
    if not settings.db_path.is_file():
        raise BackupError(f"database does not exist: {settings.db_path}")
    if settings.backup_retention_count < 1:
        raise BackupError("BACKUP_RETENTION_COUNT must be at least 1")

    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    destination = _unused_destination(settings.backup_dir, timestamp)
    temporary = settings.backup_dir / f".{destination.name}.{uuid4().hex}.tmp"

    try:
        _copy_and_verify(settings.db_path, temporary)
        temporary.replace(destination)
        _rotate(settings.backup_dir, settings.backup_retention_count)
    except BackupError:
        temporary.unlink(missing_ok=True)
        raise
    except (OSError, sqlite3.Error) as error:
        temporary.unlink(missing_ok=True)
        raise BackupError(f"could not create database backup: {error}") from None

    log.info("backup_created path=%s", destination)
    return destination


def _copy_and_verify(source_path: Path, destination_path: Path) -> None:
    """Copy ``source_path`` to a new SQLite file and verify the new file."""
    destination_path.unlink(missing_ok=True)
    source_uri = f"{source_path.resolve().as_uri()}?mode=ro"

    with (
        closing(sqlite3.connect(source_uri, uri=True)) as source,
        closing(sqlite3.connect(destination_path)) as destination,
    ):
        source.backup(destination)
        results = destination.execute("PRAGMA integrity_check").fetchall()

    if results != [("ok",)]:
        details = "; ".join(str(row[0]) for row in results) or "no result"
        raise BackupError(f"backup failed SQLite integrity_check: {details}")


def _unused_destination(directory: Path, timestamp: str) -> Path:
    candidate = directory / f"{_BACKUP_PREFIX}{timestamp}.db"
    sequence = 2
    while candidate.exists():
        candidate = directory / f"{_BACKUP_PREFIX}{timestamp}-{sequence}.db"
        sequence += 1
    return candidate


def _rotate(directory: Path, retention_count: int) -> None:
    backups = [path for path in directory.glob(_BACKUP_GLOB) if path.is_file()]
    backups.sort(key=_backup_sort_key, reverse=True)
    for expired in backups[retention_count:]:
        expired.unlink()


def _backup_sort_key(path: Path) -> tuple[str, int, int]:
    """Sort our timestamp names, including same-second collision suffixes."""
    stem = path.stem.removeprefix(_BACKUP_PREFIX)
    timestamp = stem[:15]
    suffix = stem[15:]
    sequence = 1
    if suffix.startswith("-") and suffix[1:].isdigit():
        sequence = int(suffix[1:])
    return timestamp, sequence, path.stat().st_mtime_ns
