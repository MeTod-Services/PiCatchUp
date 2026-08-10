from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from picatchup import audio, daily, deliver, recap, transcribe, users
from picatchup.config import Settings
from picatchup.errors import AudioError
from picatchup.providers.groq_audio import Segment, Transcription
from picatchup.util import new_id, utc_now


@pytest.fixture
def postbox(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str]]:
    """The whole outside world, replaced: ffmpeg, Whisper, the text model, Telegram.

    What comes back is the list of messages that "arrived", so a test can say
    what a phone would have shown.
    """
    monkeypatch.setattr(audio, "ensure_ffmpeg", lambda: None)
    monkeypatch.setattr(audio, "duration_ms", lambda path: 60_000)
    monkeypatch.setattr(transcribe, "_groq", lambda settings: whisper_says("testo della lezione"))
    monkeypatch.setattr(recap, "_groq", lambda settings, model: lambda prompt: "il riassunto")

    delivered: list[tuple[int, str]] = []

    def telegram(settings: Settings) -> deliver.Sender:
        def send(text: str, chat_id: int) -> list[int]:
            delivered.append((chat_id, text))
            return [len(delivered)]

        return send

    monkeypatch.setattr(deliver, "_telegram", telegram)
    return delivered


def whisper_says(text: str) -> transcribe.Transcriber:
    def transcribe_piece(piece: Path) -> Transcription:
        return Transcription(text=text, segments=(Segment(0, 60_000, text),))

    return transcribe_piece


def drop_in_inbox(settings: Settings, name: str, content: bytes = b"pretend audio") -> Path:
    path = settings.inbox_dir / name
    path.write_bytes(content)
    return path


class TestRun:
    def test_inbox_to_telegram_in_one_command(
        self, conn: sqlite3.Connection, settings: Settings, postbox: list[tuple[int, str]]
    ) -> None:
        drop_in_inbox(settings, "2026-09-15-0900-matematica.mp3", b"prima lezione")
        drop_in_inbox(settings, "2026-09-15-1100-storia.mp3", b"seconda lezione")
        users.add(conn, 111, "Ada")

        report = daily.run(conn, settings, recap_date="2026-09-15")

        assert len(report.transcribed) == 2
        assert report.recap is not None and report.recap.text == "il riassunto"
        assert report.delivered is not None and report.delivered.sent_to == ["Ada"]
        assert postbox == [(111, "il riassunto")]
        assert list(settings.inbox_dir.iterdir()) == [], "handled files leave the inbox"
        assert len(list(settings.audio_dir.iterdir())) == 2, "but the audio itself is kept"

    def test_running_it_again_sends_nothing_new(
        self, conn: sqlite3.Connection, settings: Settings, postbox: list[tuple[int, str]]
    ) -> None:
        drop_in_inbox(settings, "2026-09-15-0900-matematica.mp3")
        users.add(conn, 111, "Ada")

        daily.run(conn, settings, recap_date="2026-09-15")
        second = daily.run(conn, settings, recap_date="2026-09-15")

        assert len(postbox) == 1, "the recap arrives once"
        assert second.transcribed == []
        assert second.delivered is not None and second.delivered.already_had_it == ["Ada"]

    def test_the_same_recording_dropped_twice_is_not_paid_for_twice(
        self, conn: sqlite3.Connection, settings: Settings, postbox: list[tuple[int, str]]
    ) -> None:
        drop_in_inbox(settings, "2026-09-15-0900-matematica.mp3", b"la stessa lezione")
        users.add(conn, 111, "Ada")
        daily.run(conn, settings, recap_date="2026-09-15")

        again = drop_in_inbox(settings, "matematica (copia).mp3", b"la stessa lezione")
        report = daily.run(conn, settings, recap_date="2026-09-15")

        assert report.skipped == [again.name]
        assert conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0] == 1

    def test_a_day_with_nothing_recorded_is_not_a_failure(
        self, conn: sqlite3.Connection, settings: Settings, postbox: list[tuple[int, str]]
    ) -> None:
        users.add(conn, 111, "Ada")

        report = daily.run(conn, settings, recap_date="2026-09-13")

        assert report.note is not None and "no transcribed lesson" in report.note
        assert report.delivered is None and postbox == []

    def test_a_broken_file_is_left_in_the_inbox_and_reported(
        self,
        conn: sqlite3.Connection,
        settings: Settings,
        postbox: list[tuple[int, str]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        drop_in_inbox(settings, "2026-09-15-0900-matematica.mp3", b"lezione buona")
        broken = drop_in_inbox(settings, "2026-09-15-1100-rotto.mp3", b"mezzo file")
        users.add(conn, 111, "Ada")

        def sometimes_broken(piece: Path) -> Transcription:
            # The stored copy keeps the bytes, not the name it was dropped under.
            if piece.read_bytes() == b"mezzo file":
                raise AudioError("ffmpeg failed: invalid data found")
            return Transcription(text="testo buono", segments=())

        monkeypatch.setattr(transcribe, "_groq", lambda settings: sometimes_broken)

        report = daily.run(conn, settings, recap_date="2026-09-15")

        assert [name for name, _ in report.failed] == [broken.name]
        assert broken.is_file(), "left where it is, so the next run can retry it"
        assert report.delivered is not None and report.delivered.sent_to == ["Ada"]

    def test_only_audio_files_are_looked_at(self, settings: Settings) -> None:
        (settings.inbox_dir / "note.txt").write_text("non è audio", encoding="utf-8")
        drop_in_inbox(settings, "lezione.m4a")

        assert [path.name for path in daily.inbox_files(settings)] == ["lezione.m4a"]


class TestPurgeAudio:
    def test_old_recordings_go_the_text_stays(
        self, conn: sqlite3.Connection, settings: Settings
    ) -> None:
        old = settings.audio_dir / "old.mp3"
        old.write_bytes(b"audio")
        conn.execute(
            "INSERT INTO lessons (id, lesson_date, audio_path, audio_sha256, text, created_at)"
            " VALUES (?, '2026-08-01', ?, ?, 'quello che è stato detto',"
            " '2026-08-01T10:00:00+00:00')",
            (new_id(), str(old), new_id()),
        )
        conn.commit()

        assert daily.purge_audio(conn, settings) == 1
        assert not old.exists()
        row = conn.execute("SELECT audio_path, text FROM lessons").fetchone()
        assert row["audio_path"] is None and row["text"] == "quello che è stato detto"

    def test_a_recording_from_today_is_kept(
        self, conn: sqlite3.Connection, settings: Settings
    ) -> None:
        recent = settings.audio_dir / "recent.mp3"
        recent.write_bytes(b"audio")
        conn.execute(
            "INSERT INTO lessons (id, lesson_date, audio_path, audio_sha256, created_at)"
            " VALUES (?, '2026-09-15', ?, ?, ?)",
            (new_id(), str(recent), new_id(), utc_now()),
        )
        conn.commit()

        assert daily.purge_audio(conn, settings) == 0
        assert recent.exists()
