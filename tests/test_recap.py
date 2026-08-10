from __future__ import annotations

import sqlite3

import pytest

from picatchup import recap
from picatchup.config import Settings
from picatchup.errors import PiCatchUpError
from picatchup.util import new_id, utc_now


def add_lesson(conn: sqlite3.Connection, *, date: str, subject: str | None, text: str) -> None:
    conn.execute(
        "INSERT INTO lessons"
        " (id, lesson_date, subject, audio_sha256, duration_ms, text, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (new_id(), date, subject, new_id(), 3_120_000, text, utc_now()),
    )
    conn.commit()


class TestTemplate:
    def test_the_note_to_ourselves_is_not_part_of_the_prompt(self) -> None:
        template = recap.load_template()

        assert "{{date}}" in template and "{{lessons}}" in template
        assert "<!--" not in template
        assert template.startswith("Sei un bravo studente")

    def test_the_prompt_carries_the_day_and_its_lessons(
        self, conn: sqlite3.Connection
    ) -> None:
        add_lesson(conn, date="2026-09-15", subject="Matematica", text="Oggi le derivate.")
        lessons = conn.execute("SELECT subject, duration_ms, text FROM lessons").fetchall()

        prompt = recap.build_prompt("il {{date}}\n{{lessons}}", "2026-09-15", lessons)

        assert prompt == "il 2026-09-15\n### Matematica (52 minuti)\nOggi le derivate."

    def test_a_lesson_without_a_subject_still_gets_a_heading(
        self, conn: sqlite3.Connection
    ) -> None:
        add_lesson(conn, date="2026-09-15", subject=None, text="testo")
        lessons = conn.execute("SELECT subject, duration_ms, text FROM lessons").fetchall()

        assert "Materia non indicata" in recap.build_prompt("{{lessons}}", "2026-09-15", lessons)


class TestMakeRecap:
    def test_writes_and_stores_the_summary(
        self, conn: sqlite3.Connection, settings: Settings
    ) -> None:
        add_lesson(conn, date="2026-09-15", subject="Matematica", text="Oggi le derivate.")

        written = recap.make_recap(
            conn, settings, "2026-09-15", writer=lambda prompt: "📚 MATEMATICA\n• le derivate"
        )

        assert written.reused is False
        assert written.model == "test-text-model"
        stored = conn.execute("SELECT text FROM recaps WHERE recap_date = '2026-09-15'").fetchone()
        assert stored["text"] == written.text

    def test_a_day_without_lessons_says_so(
        self, conn: sqlite3.Connection, settings: Settings
    ) -> None:
        with pytest.raises(PiCatchUpError, match="no transcribed lesson"):
            recap.make_recap(conn, settings, "2026-09-13", writer=lambda prompt: "never called")

    def test_asking_twice_does_not_pay_twice(
        self, conn: sqlite3.Connection, settings: Settings
    ) -> None:
        add_lesson(conn, date="2026-09-15", subject="Matematica", text="Oggi le derivate.")
        calls: list[str] = []

        def writer(prompt: str) -> str:
            calls.append(prompt)
            return f"versione {len(calls)}"

        first = recap.make_recap(conn, settings, "2026-09-15", writer=writer)
        again = recap.make_recap(conn, settings, "2026-09-15", writer=writer)

        assert len(calls) == 1
        assert again.reused is True and again.text == first.text

    def test_force_rewrites_it_in_place(
        self, conn: sqlite3.Connection, settings: Settings
    ) -> None:
        add_lesson(conn, date="2026-09-15", subject="Matematica", text="Oggi le derivate.")
        first = recap.make_recap(conn, settings, "2026-09-15", writer=lambda p: "prima versione")

        second = recap.make_recap(
            conn, settings, "2026-09-15", writer=lambda p: "seconda versione", force=True
        )

        assert second.text == "seconda versione"
        assert second.id == first.id, "the same recap, so deliveries still point at it"
        assert conn.execute("SELECT COUNT(*) FROM recaps").fetchone()[0] == 1
