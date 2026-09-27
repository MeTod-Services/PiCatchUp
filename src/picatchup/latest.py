"""Summarize, deliver and archive only the recording just transcribed."""

from __future__ import annotations

import logging
import sqlite3
import subprocess
from pathlib import Path

from picatchup import audio, db, pdf_report, recap, users
from picatchup.config import Settings, require
from picatchup.errors import PiCatchUpError
from picatchup.providers import groq_text, telegram
from picatchup.util import new_id, utc_now

log = logging.getLogger(__name__)

NAS_TARGET = "metod@100.126.19.115:/DATA/Documents/PiCatchUp/"


def _send_pdf_to_nas(pdf_path: Path) -> bool:
    """Copy one completed PDF to the NAS. Never delete anything on failure."""
    command = [
        "rsync",
        "-v",
        "--no-perms",
        "--no-owner",
        "--no-group",
        str(pdf_path),
        NAS_TARGET,
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        log.warning("NAS archive failed: %s", error)
        return False

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        log.warning(
            "NAS archive failed for %s: %s",
            pdf_path.name,
            detail[-500:],
        )
        return False

    log.info("PDF archived on NAS: %s", pdf_path.name)
    return True


def _purge_lesson(
    conn: sqlite3.Connection,
    *,
    lesson_id: str,
    stored_audio: Path | None,
    original_recording: Path,
    pdf_path: Path,
) -> None:
    """Remove local material only after the PDF is safely on the NAS."""

    # chunks_fts is independent from the normal foreign-key relationship,
    # so remove its copies before deleting the lesson/chunks.
    with conn:
        conn.execute(
            """
            DELETE FROM chunks_fts
            WHERE chunk_id IN (
                SELECT id
                FROM chunks
                WHERE lesson_id = ?
            )
            """,
            (lesson_id,),
        )

        conn.execute(
            "DELETE FROM chunks WHERE lesson_id = ?",
            (lesson_id,),
        )

        conn.execute(
            "DELETE FROM lessons WHERE id = ?",
            (lesson_id,),
        )

    paths = [original_recording, pdf_path]

    if stored_audio is not None:
        paths.append(stored_audio)

    seen: set[Path] = set()

    for path in paths:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path

        if resolved in seen:
            continue
        seen.add(resolved)

        try:
            path.unlink(missing_ok=True)
            log.info("local file removed: %s", path)
        except OSError as error:
            log.warning("could not remove local file %s: %s", path, error)


def summarize_and_send(settings: Settings, recording: Path) -> str:
    """Summarize only `recording`, send it, archive its PDF, then clean up."""

    audio_hash = audio.sha256_of(recording)

    conn = db.connect(settings.db_path)

    try:
        lesson = conn.execute(
            """
            SELECT
                id,
                subject,
                duration_ms,
                text,
                lesson_date,
                audio_path
            FROM lessons
            WHERE audio_sha256 = ?
              AND text IS NOT NULL
              AND text != ''
            LIMIT 1
            """,
            (audio_hash,),
        ).fetchone()

        if lesson is None:
            raise PiCatchUpError(f"transcribed lesson not found for {recording.name}")

        model = require(settings.text_model, "TEXT_MODEL")
        api_key = require(settings.groq_api_key, "GROQ_API_KEY")

        # Una sola lesson: mai il recap cumulativo della giornata.
        prompt = recap.build_prompt(
            recap.template_for(settings),
            lesson["lesson_date"],
            [lesson],
        )

        summary = groq_text.generate(
            prompt,
            api_key=api_key,
            model=model,
        ).strip()

        if not summary:
            raise PiCatchUpError("the text provider returned an empty summary")

        token = require(
            settings.telegram_bot_token,
            "TELEGRAM_BOT_TOKEN",
        )

        people = users.recipients(conn)

        if not people:
            raise PiCatchUpError("there is nobody to send it to. Add someone: picatchup user add")

        if settings.mode == "conversation":
            with conn:
                conn.execute(
                    "DELETE FROM deliveries WHERE recap_id IN "
                    "(SELECT id FROM recaps WHERE recap_date = ?)",
                    (lesson["lesson_date"],),
                )
                conn.execute(
                    "INSERT INTO recaps (id, recap_date, text, model, created_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT (recap_date) DO UPDATE SET "
                    "text = excluded.text, model = excluded.model, "
                    "created_at = excluded.created_at",
                    (new_id(), lesson["lesson_date"], summary, model, utc_now()),
                )

        # 1. Messaggio testuale.
        for person in people:
            telegram.send_message(
                summary,
                token=token,
                chat_id=person["telegram_user_id"],
            )

        # 2. Crea il PDF.
        reports_dir = settings.data_dir / "reports"
        pdf_path = reports_dir / f"{recording.stem}.pdf"

        try:
            pdf_report.create_summary_pdf(
                pdf_path,
                date=lesson["lesson_date"],
                subject=lesson["subject"],
                summary=summary,
            )

            # 3. Invia il PDF su Telegram.
            for person in people:
                telegram.send_document(
                    pdf_path,
                    token=token,
                    chat_id=person["telegram_user_id"],
                    caption="PiCatchUp — riassunto della registrazione",
                )

            log.info("PDF delivered: %s", pdf_path.name)

        except Exception as error:
            # Il recap testuale è già arrivato.
            # Conserviamo tutto localmente per sicurezza.
            log.warning("PDF delivery failed: %s", error)
            return summary

        # Conversation material remains in its isolated store. Never upload it
        # to the lesson NAS destination or purge it through the lesson policy.
        if settings.mode == "conversation":
            return summary

        # 4. NAS. Se fallisce, NON cancellare niente.
        if not _send_pdf_to_nas(pdf_path):
            log.warning(
                "keeping local lesson because NAS archive failed: %s",
                recording.name,
            )
            return summary

        # 5. Solo dopo rsync riuscito: cleanup totale locale.
        stored_audio = Path(lesson["audio_path"]) if lesson["audio_path"] else None

        _purge_lesson(
            conn,
            lesson_id=lesson["id"],
            stored_audio=stored_audio,
            original_recording=recording,
            pdf_path=pdf_path,
        )

        log.info(
            "lesson archived and local data purged: %s",
            recording.name,
        )

        return summary

    finally:
        conn.close()
