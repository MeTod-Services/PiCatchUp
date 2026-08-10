"""Consistent, rotated SQLite backups."""

from __future__ import annotations

import logging
import re
import sqlite3
import subprocess
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from picatchup.config import Settings
from picatchup.errors import PiCatchUpError

log = logging.getLogger(__name__)

_BACKUP_PREFIX = "picatchup-"
_BACKUP_GLOB = f"{_BACKUP_PREFIX}*.db"
_REMOTE_RE = re.compile(r"(?:[A-Za-z0-9._-]+@)?[A-Za-z0-9._-]+:/[A-Za-z0-9._/-]+")


class BackupError(PiCatchUpError):
    """A database snapshot could not be created, verified, or transferred."""


def create_backup(settings: Settings, *, now: datetime | None = None) -> Path:
    """Create and verify a consistent snapshot, then rotate old snapshots.

    SQLite's backup API takes a transactionally consistent copy even when the
    source database uses WAL and another process is reading it.  The copy is
    first written under a temporary name in the backup directory; it only
    becomes visible under its final name after ``integrity_check`` succeeds.
    When ``BACKUP_REMOTE`` is set, the verified snapshot is then sent there
    with rsync over SSH (including over a Tailscale address).
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
        if settings.backup_remote:
            _send_remote(destination, settings.backup_remote)
    except BackupError:
        temporary.unlink(missing_ok=True)
        raise
    except (OSError, sqlite3.Error) as error:
        temporary.unlink(missing_ok=True)
        raise BackupError(f"could not create database backup: {error}") from None

    log.info("backup_created path=%s", destination)
    return destination


def _send_remote(snapshot: Path, remote: str) -> None:
    """Send one verified snapshot to an rsync SSH destination directory."""
    if _REMOTE_RE.fullmatch(remote) is None or remote.partition(":")[2].strip("/") == "":
        raise BackupError(
            "BACKUP_REMOTE must look like user@tailscale-host:/existing/directory/"
        )

    destination = remote.rstrip("/") + "/"
    try:
        result = subprocess.run(
            [
                "rsync",
                "--archive",
                "--rsh=ssh -o BatchMode=yes",
                "--timeout=120",
                "--",
                str(snapshot),
                destination,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError:
        raise BackupError(
            f"local backup created at {snapshot}, but rsync is not installed"
        ) from None
    except subprocess.TimeoutExpired:
        raise BackupError(
            f"local backup created at {snapshot}, but rsync to BACKUP_REMOTE timed out"
        ) from None
    except OSError as error:
        raise BackupError(
            f"local backup created at {snapshot}, but rsync could not start: {error}"
        ) from None

    if result.returncode != 0:
        detail = _last_error_line(result.stderr)
        suffix = f": {detail}" if detail else ""
        raise BackupError(
            f"local backup created at {snapshot}, but rsync to BACKUP_REMOTE failed "
            f"(exit {result.returncode}){suffix}"
        )

    log.info("backup_remote_synced file=%s remote=%s", snapshot.name, destination)


def _last_error_line(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return lines[-1] if lines else ""


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
