"""The whole day in one command, for cron to run at 15:00.

It is a `for` loop over the files in the inbox, then a recap, then a send. If
it crashes halfway, running it again picks up what is missing: the checksum on
`lessons` and the primary key on `deliveries` are what make that safe, so there
is no queue and no state machine here.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from picatchup import audio, deliver, transcribe
from picatchup import recap as recap_module
from picatchup.config import Settings
from picatchup.deliver import Delivered
from picatchup.errors import PiCatchUpError
from picatchup.recap import Recap
from picatchup.util import today

log = logging.getLogger(__name__)


@dataclass
class Report:
    """What happened, in a shape the CLI can print and a test can assert on."""

    transcribed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    purged_audio: int = 0
    recap: Recap | None = None
    delivered: Delivered | None = None
    note: str | None = None


def run(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    recap_date: str | None = None,
    force: bool = False,
) -> Report:
    recap_date = recap_date or today(settings.timezone)
    report = Report()

    for path in inbox_files(settings):
        _ingest(conn, settings, path, recap_date, report)

    try:
        report.recap = recap_module.make_recap(conn, settings, recap_date, force=force)
    except PiCatchUpError as error:
        # A Sunday, or a day when nobody recorded anything. Not a failure.
        report.note = str(error)
        return report

    report.delivered = deliver.send_recap(conn, settings, recap_date, force=force)
    report.purged_audio = purge_audio(conn, settings)
    return report


def inbox_files(settings: Settings) -> list[Path]:
    """Whatever somebody dropped in, oldest first, audio only."""
    if not settings.inbox_dir.is_dir():
        return []
    files = [
        path
        for path in settings.inbox_dir.iterdir()
        if path.is_file() and path.suffix.lower() in audio.KNOWN_SUFFIXES
    ]
    return sorted(files, key=lambda path: (path.stat().st_mtime, path.name))


def _ingest(
    conn: sqlite3.Connection, settings: Settings, path: Path, recap_date: str, report: Report
) -> None:
    """Transcribe one dropped file, then take it out of the inbox.

    The audio itself is not lost: `transcribe` has already copied it into
    `data/audio`, which is the copy that gets deleted after a week.
    """
    try:
        if transcribe.find_by_checksum(conn, audio.sha256_of(path)) is not None:
            report.skipped.append(path.name)
            path.unlink()
            return
        lesson = transcribe.transcribe_file(conn, settings, path, lesson_date=recap_date)
    except PiCatchUpError as error:
        # Leave the file where it is: whoever reads the log can fix it and the
        # next run will try again.
        log.warning("could not transcribe %s: %s", path.name, error)
        report.failed.append((path.name, str(error)))
        return

    report.transcribed.append(path.name)
    path.unlink()
    log.info("transcribed %s as lesson %s", path.name, lesson.id)


def purge_audio(conn: sqlite3.Connection, settings: Settings) -> int:
    """Delete recordings older than the retention period. The text stays.

    This is privacy rule number one from the plan, and it is three lines, so
    there is no excuse for putting it off.
    """
    cutoff = (datetime.now(UTC) - timedelta(days=settings.audio_retention_days)).isoformat()
    rows = conn.execute(
        "SELECT id, audio_path FROM lessons WHERE audio_path IS NOT NULL AND created_at < ?",
        (cutoff,),
    ).fetchall()

    for row in rows:
        Path(row["audio_path"]).unlink(missing_ok=True)
        with conn:
            conn.execute("UPDATE lessons SET audio_path = NULL WHERE id = ?", (row["id"],))
    if rows:
        log.info(
            "deleted %d recording(s) older than %d days", len(rows), settings.audio_retention_days
        )
    return len(rows)
