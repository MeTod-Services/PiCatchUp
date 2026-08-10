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
from picatchup.status import DeviceStatus, Status, StatusFile
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
    lesson_dates: list[str] = field(default_factory=list)
    processed_dates: list[str] = field(default_factory=list)


def run(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    recap_date: str | None = None,
    force: bool = False,
) -> Report:
    explicit_date = recap_date
    fallback_date = recap_date or today(settings.timezone)
    report = Report()
    device_status = StatusFile(settings.status_file)

    # Privacy cleanup is deliberately first and independent of every network
    # stage. A Groq or Telegram outage must never extend audio retention.
    report.purged_audio = purge_audio(conn, settings)

    def publish_progress(current: int, total: int) -> None:
        device_status.publish(
            DeviceStatus(Status.TRANSCRIBING, detail=f"{current} / {total}")
        )

    pending = inbox_files(settings)
    for file_number, path in enumerate(pending, start=1):
        device_status.publish(
            Status.TRANSCRIBING, detail=f"file {file_number} / {len(pending)}"
        )
        _ingest(conn, settings, path, fallback_date, report, progress=publish_progress)

    # Never publish a knowingly partial recap. The good lessons remain stored;
    # after the broken inbox file is fixed, the next run includes all of them.
    if report.failed:
        device_status.publish(Status.ERROR, detail="Trascrizione fallita")
        return report

    if explicit_date is not None:
        target_dates = [explicit_date]
    else:
        target_dates = sorted(
            set(report.lesson_dates)
            | set(_unrecapped_dates(conn))
            | set(_pending_delivery_dates(conn))
        )
        if not target_dates:
            target_dates = [fallback_date]

    for target_date in target_dates:
        if not _process_date(
            conn, settings, target_date, report, device_status=device_status, force=force
        ):
            continue

    if report.failed or (report.delivered is not None and report.delivered.failed):
        device_status.publish(Status.ERROR, detail="Elaborazione fallita")
    elif report.processed_dates:
        lesson_count = int(
            conn.execute(
                (
                    "SELECT COUNT(*) FROM lessons WHERE lesson_date IN ({})"
                    " AND text IS NOT NULL"
                ).format(",".join("?" for _date in report.processed_dates)),
                report.processed_dates,
            ).fetchone()[0]
        )
        device_status.publish(Status.SUCCESS, detail=f"{lesson_count} lezioni / inviate")
    else:
        report.note = f"there is no transcribed lesson for {fallback_date}"
        device_status.publish(Status.READY)
    return report


def _process_date(
    conn: sqlite3.Connection,
    settings: Settings,
    recap_date: str,
    report: Report,
    *,
    device_status: StatusFile,
    force: bool,
) -> bool:
    lesson_available = conn.execute(
        "SELECT 1 FROM lessons WHERE lesson_date = ? AND text IS NOT NULL AND text != '' LIMIT 1",
        (recap_date,),
    ).fetchone()
    if lesson_available is None:
        return False

    device_status.publish(Status.SUMMARIZING)
    try:
        report.recap = recap_module.make_recap(conn, settings, recap_date, force=force)
    except PiCatchUpError as error:
        report.failed.append((f"recap {recap_date}", str(error)))
        return False

    device_status.publish(Status.SENDING)
    try:
        delivered = deliver.send_recap(conn, settings, recap_date, force=force)
    except PiCatchUpError as error:
        report.failed.append((f"delivery {recap_date}", str(error)))
        return False

    if report.delivered is None:
        report.delivered = Delivered()
    report.delivered.sent_to.extend(delivered.sent_to)
    report.delivered.already_had_it.extend(delivered.already_had_it)
    report.delivered.failed.extend(delivered.failed)
    report.processed_dates.append(recap_date)
    return True


def _unrecapped_dates(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT l.lesson_date FROM lessons AS l"
        " LEFT JOIN recaps AS r ON r.recap_date = l.lesson_date"
        " WHERE l.text IS NOT NULL AND l.text != ''"
        " AND (r.id IS NULL OR l.created_at >= r.created_at)"
    ).fetchall()
    return [str(row[0]) for row in rows]


def _pending_delivery_dates(conn: sqlite3.Connection) -> list[str]:
    """Recaps missed during a Telegram outage, excluding pre-enrollment history."""
    rows = conn.execute(
        "SELECT DISTINCT r.recap_date FROM recaps AS r"
        " JOIN users AS u ON u.is_active = 1 AND u.is_muted = 0"
        " AND r.created_at >= u.created_at"
        " LEFT JOIN deliveries AS d ON d.recap_id = r.id AND d.user_id = u.id"
        " WHERE d.recap_id IS NULL"
    ).fetchall()
    return [str(row[0]) for row in rows]


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
    conn: sqlite3.Connection,
    settings: Settings,
    path: Path,
    recap_date: str,
    report: Report,
    *,
    progress: transcribe.Progress | None = None,
) -> None:
    """Transcribe one dropped file, then take it out of the inbox.

    The audio itself is not lost: `transcribe` has already copied it into
    `data/audio`, which is the copy that gets deleted after a week.
    """
    try:
        known = transcribe.find_by_checksum(conn, audio.sha256_of(path))
        if known is not None:
            report.skipped.append(path.name)
            if known["lesson_date"] not in report.lesson_dates:
                report.lesson_dates.append(str(known["lesson_date"]))
            path.unlink()
            return
        lesson = transcribe.transcribe_file(
            conn, settings, path, fallback_date=recap_date, progress=progress
        )
    except PiCatchUpError as error:
        # Leave the file where it is: whoever reads the log can fix it and the
        # next run will try again.
        log.warning("could not transcribe %s: %s", path.name, error)
        report.failed.append((path.name, str(error)))
        return

    report.transcribed.append(path.name)
    if lesson.lesson_date not in report.lesson_dates:
        report.lesson_dates.append(lesson.lesson_date)
    path.unlink()
    log.info("transcribed %s as lesson %s", path.name, lesson.id)


def purge_audio(conn: sqlite3.Connection, settings: Settings) -> int:
    """Delete recordings older than the retention period. The text stays.

    This is privacy rule number one from the plan, and it is three lines, so
    there is no excuse for putting it off.
    """
    cutoff_at = datetime.now(UTC) - timedelta(days=settings.audio_retention_days)
    candidates = conn.execute(
        "SELECT id, lesson_date, audio_path, created_at"
        " FROM lessons WHERE audio_path IS NOT NULL"
    ).fetchall()
    rows: list[sqlite3.Row] = []
    for row in candidates:
        path = Path(row["audio_path"])
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        except OSError:
            modified = datetime.fromisoformat(row["created_at"])
        lesson_day = datetime.fromisoformat(row["lesson_date"]).replace(tzinfo=UTC)
        created = datetime.fromisoformat(row["created_at"])
        if min(modified, lesson_day, created) < cutoff_at:
            rows.append(row)

    for row in rows:
        try:
            Path(row["audio_path"]).unlink(missing_ok=True)
        except OSError as error:
            raise PiCatchUpError(
                f"could not delete expired recording: {error.strerror or error}"
            ) from None
        with conn:
            conn.execute("UPDATE lessons SET audio_path = NULL WHERE id = ?", (row["id"],))
    referenced = {
        str(Path(row["audio_path"]).resolve())
        for row in conn.execute(
            "SELECT audio_path FROM lessons WHERE audio_path IS NOT NULL"
        ).fetchall()
    }
    orphaned = 0
    if settings.audio_dir.is_dir():
        for path in settings.audio_dir.iterdir():
            if not path.is_file() or path.suffix.lower() not in audio.KNOWN_SUFFIXES:
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            if str(path.resolve()) not in referenced and modified < cutoff_at:
                path.unlink()
                orphaned += 1

    inbox_deleted = 0
    if settings.inbox_dir.is_dir():
        for path in settings.inbox_dir.iterdir():
            is_audio = path.suffix.lower() in audio.KNOWN_SUFFIXES
            is_partial = path.name.endswith(".part")
            if not path.is_file() or not (is_audio or is_partial):
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            named_date, _subject = transcribe.read_name(path)
            named_day = (
                datetime.fromisoformat(named_date).replace(tzinfo=UTC)
                if named_date is not None
                else modified
            )
            if min(modified, named_day) < cutoff_at:
                path.unlink()
                inbox_deleted += 1

    deleted = len(rows) + orphaned + inbox_deleted
    if deleted:
        log.info(
            "retention_deleted count=%d days=%d", deleted, settings.audio_retention_days
        )
    return deleted
