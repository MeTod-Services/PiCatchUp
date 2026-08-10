from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from picatchup import audio, transcribe
from picatchup.config import Settings
from picatchup.errors import PiCatchUpError
from picatchup.providers.groq_audio import Segment, Transcription


def segment(start_ms: int, end_ms: int, text: str) -> Segment:
    return Segment(start_ms=start_ms, end_ms=end_ms, text=text)


@pytest.fixture
def no_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend ffmpeg is there and every recording lasts a minute.

    The tests are about the timeline and the database, not about ffmpeg.
    """
    monkeypatch.setattr(audio, "ensure_ffmpeg", lambda: None)
    monkeypatch.setattr(audio, "duration_ms", lambda path: 60_000)


def a_recording(tmp_path: Path, name: str = "2026-09-15-matematica.mp3") -> Path:
    path = tmp_path / name
    path.write_bytes(b"not really an mp3, but it has a checksum")
    return path


class TestReadName:
    def test_takes_the_date_and_the_subject(self) -> None:
        assert transcribe.read_name(Path("2026-09-15 Matematica.mp3")) == (
            "2026-09-15",
            "Matematica",
        )

    def test_ignores_the_time_that_record_puts_in(self) -> None:
        assert transcribe.read_name(Path("2026-09-15-1430-fisica.m4a")) == ("2026-09-15", "fisica")

    def test_a_date_on_its_own_is_enough(self) -> None:
        assert transcribe.read_name(Path("2026-09-15.mp3")) == ("2026-09-15", None)

    def test_a_name_that_says_nothing_says_nothing(self) -> None:
        assert transcribe.read_name(Path("registrazione finale (2).mp3")) == (None, None)


class TestBuildChunks:
    def test_groups_segments_up_to_the_size(self) -> None:
        segments = [segment(i * 1000, (i + 1) * 1000, "dieci char") for i in range(10)]

        chunks = transcribe.build_chunks(segments, size=40)

        assert [chunk.text for chunk in chunks] == [
            "dieci char dieci char dieci char",
            "dieci char dieci char dieci char",
            "dieci char dieci char dieci char",
            "dieci char",
        ]

    def test_a_chunk_starts_and_ends_on_real_moments(self) -> None:
        segments = [segment(5_000, 8_000, "prima parte"), segment(8_000, 12_000, "seconda parte")]

        [chunk] = transcribe.build_chunks(segments, size=100)

        assert (chunk.idx, chunk.start_ms, chunk.end_ms) == (0, 5_000, 12_000)

    def test_a_segment_longer_than_the_size_is_kept_whole(self) -> None:
        segments = [segment(0, 1000, "x" * 50)]

        assert transcribe.build_chunks(segments, size=10)[0].text == "x" * 50

    def test_no_speech_gives_no_chunks(self) -> None:
        assert transcribe.build_chunks([]) == []


class TestShift:
    def test_moves_a_piece_onto_the_lessons_timeline(self) -> None:
        moved = transcribe.shift([segment(0, 2_000, "ciao")], 600_000)

        assert (moved[0].start_ms, moved[0].end_ms) == (600_000, 602_000)


class TestTranscribeFile:
    def test_saves_the_lesson_its_chunks_and_the_search_index(
        self, conn: sqlite3.Connection, settings: Settings, tmp_path: Path, no_ffmpeg: None
    ) -> None:
        source = a_recording(tmp_path)

        lesson = transcribe.transcribe_file(
            conn,
            settings,
            source,
            transcriber=lambda piece: Transcription(
                text="Oggi parliamo delle derivate.",
                segments=(segment(0, 3_000, "Oggi parliamo delle derivate."),),
            ),
        )

        assert lesson.lesson_date == "2026-09-15", "read from the file name"
        assert lesson.subject == "matematica"
        assert lesson.text == "Oggi parliamo delle derivate."
        row = conn.execute("SELECT * FROM lessons WHERE id = ?", (lesson.id,)).fetchone()
        assert row["duration_ms"] == 60_000
        assert Path(row["audio_path"]).is_file(), "the audio is kept, out of the inbox"
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
        hits = conn.execute("SELECT * FROM chunks_fts WHERE text MATCH 'derivate'").fetchall()
        assert len(hits) == 1, "a lesson is searchable as soon as it is stored"

    def test_arguments_beat_the_file_name(
        self, conn: sqlite3.Connection, settings: Settings, tmp_path: Path, no_ffmpeg: None
    ) -> None:
        lesson = transcribe.transcribe_file(
            conn,
            settings,
            a_recording(tmp_path),
            lesson_date="2026-10-01",
            subject="Storia",
            transcriber=lambda piece: Transcription(text="testo", segments=()),
        )

        assert (lesson.lesson_date, lesson.subject) == ("2026-10-01", "Storia")

    def test_text_without_timings_still_becomes_one_chunk(
        self, conn: sqlite3.Connection, settings: Settings, tmp_path: Path, no_ffmpeg: None
    ) -> None:
        """Some models answer with the text and no segments. Do not lose the lesson."""
        lesson = transcribe.transcribe_file(
            conn,
            settings,
            a_recording(tmp_path),
            transcriber=lambda piece: Transcription(text="testo senza tempi", segments=()),
        )

        chunk = conn.execute("SELECT * FROM chunks WHERE lesson_id = ?", (lesson.id,)).fetchone()
        assert (chunk["start_ms"], chunk["end_ms"]) == (0, 60_000)

    def test_the_same_file_is_never_paid_for_twice(
        self, conn: sqlite3.Connection, settings: Settings, tmp_path: Path, no_ffmpeg: None
    ) -> None:
        transcriber = lambda piece: Transcription(text="testo", segments=())  # noqa: E731
        transcribe.transcribe_file(conn, settings, a_recording(tmp_path), transcriber=transcriber)

        copy = a_recording(tmp_path, name="2026-09-15-copia.mp3")
        with pytest.raises(PiCatchUpError, match="already transcribed"):
            transcribe.transcribe_file(conn, settings, copy, transcriber=transcriber)

    def test_a_long_recording_is_cut_up_and_the_timings_reassembled(
        self,
        conn: sqlite3.Connection,
        settings: Settings,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        no_ffmpeg: None,
    ) -> None:
        source = a_recording(tmp_path)
        settings = replace(settings, max_upload_mb=0)  # so anything counts as too big

        pieces = [tmp_path / "piece-000.mp3", tmp_path / "piece-001.mp3"]
        for piece in pieces:
            piece.write_bytes(b"piece")
        monkeypatch.setattr(audio, "split", lambda source, into, *, minutes: pieces)
        # Every piece comes back thinking it starts at zero...
        spoken = {
            "piece-000.mp3": "prima parte",
            "piece-001.mp3": "seconda parte",
        }
        transcriber = lambda piece: Transcription(  # noqa: E731
            text=spoken[piece.name], segments=(segment(0, 5_000, spoken[piece.name]),)
        )

        lesson = transcribe.transcribe_file(conn, settings, source, transcriber=transcriber)

        assert lesson.text == "prima parte seconda parte"
        [chunk] = conn.execute(
            "SELECT text, start_ms, end_ms FROM chunks WHERE lesson_id = ?", (lesson.id,)
        ).fetchall()
        # ...and the second one is moved by the measured length of the first,
        # so it ends at 60s + 5s and not at 5s.
        assert (chunk["start_ms"], chunk["end_ms"]) == (0, 65_000)
