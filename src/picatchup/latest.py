"""Summarize and deliver only the recording that has just been transcribed."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from picatchup import audio, pdf_report, recap, users
from picatchup.config import Settings, require
from picatchup.errors import PiCatchUpError
from picatchup.providers import groq_text, telegram


log = logging.getLogger(__name__)


def summarize_and_send(settings: Settings, recording: Path) -> str:
    """Summarize only `recording` and deliver it by Telegram."""

    audio_hash = audio.sha256_of(recording)

    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row

    try:
        lesson = conn.execute(
            """
            SELECT subject, duration_ms, text, lesson_date
            FROM lessons
            WHERE audio_sha256 = ?
              AND text IS NOT NULL
              AND text != ''
            LIMIT 1
            """,
            (audio_hash,),
        ).fetchone()

        if lesson is None:
            raise PiCatchUpError(
                f"transcribed lesson not found for {recording.name}"
            )

        model = require(settings.text_model, "TEXT_MODEL")
        api_key = require(settings.groq_api_key, "GROQ_API_KEY")

        # Una sola lesson: mai il recap cumulativo della giornata.
        prompt = recap.build_prompt(
            recap.load_template(),
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
            raise PiCatchUpError(
                "there is nobody to send it to. Add someone: picatchup user add"
            )

        # 1. Il messaggio testuale rimane il canale principale.
        for person in people:
            telegram.send_message(
                summary,
                token=token,
                chat_id=person["telegram_user_id"],
            )

        # 2. PDF aggiuntivo. Se fallisce, NON rompiamo il messaggio Telegram.
        try:
            reports_dir = settings.data_dir / "reports"
            pdf_path = reports_dir / f"{recording.stem}.pdf"

            pdf_report.create_summary_pdf(
                pdf_path,
                date=lesson["lesson_date"],
                subject=lesson["subject"],
                summary=summary,
            )

            for person in people:
                telegram.send_document(
                    pdf_path,
                    token=token,
                    chat_id=person["telegram_user_id"],
                    caption="PiCatchUp — riassunto della registrazione",
                )

            log.info("PDF delivered: %s", pdf_path.name)

        except Exception as error:
            # Il PDF è un extra: il recap testuale è già arrivato.
            log.warning("PDF delivery failed: %s", error)

        return summary

    finally:
        conn.close()
