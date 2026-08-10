"""Audio file in, a lesson in the database out.

The one piece of real logic here is the timeline: a two-hour recording is too
big to upload in one go, so it is cut into pieces, each piece comes back with
timings that start at zero, and we add the offset back before saving. Get that
wrong and every citation in the project points at the wrong minute.
"""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from picatchup import audio
from picatchup.config import Settings, require
from picatchup.errors import PiCatchUpError
from picatchup.providers import groq_audio
from picatchup.providers.groq_audio import Segment, Transcription
from picatchup.util import new_id, today, utc_now

# Big enough to hold a whole thought, small enough that quoting it in an answer
# does not bore the reader.
CHUNK_SIZE = 700

Transcriber = Callable[[Path], Transcription]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Lesson:
    """What `picatchup transcribe` has to tell you when it is done."""

    id: str
    lesson_date: str
    subject: str | None
    audio_path: Path
    duration_ms: int
    text: str
    chunks: int


@dataclass(frozen=True)
class Chunk:
    idx: int
    start_ms: int
    end_ms: int
    text: str


def find_by_checksum(conn: sqlite3.Connection, checksum: str) -> sqlite3.Row | None:
    """The lesson made from this exact file, if we have already done it."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT id, lesson_date, subject FROM lessons WHERE audio_sha256 = ?", (checksum,)
    ).fetchone()
    return row


def transcribe_file(
    conn: sqlite3.Connection,
    settings: Settings,
    source: Path,
    *,
    lesson_date: str | None = None,
    subject: str | None = None,
    transcriber: Transcriber | None = None,
) -> Lesson:
    """Store the audio, send it to Whisper, save the text and its chunks."""
    if not source.is_file():
        raise PiCatchUpError(f"{source} is not a file")
    audio.ensure_ffmpeg()

    checksum = audio.sha256_of(source)
    known = find_by_checksum(conn, checksum)
    if known is not None:
        raise PiCatchUpError(
            f"{source.name} was already transcribed, as the lesson of {known['lesson_date']}"
        )

    guessed_date, guessed_subject = read_name(source)
    lesson_date = lesson_date or guessed_date or today(settings.timezone)
    subject = subject or guessed_subject

    stored = _store(settings, source, checksum, lesson_date)
    duration = audio.duration_ms(stored)

    transcriber = transcriber or _groq(settings)
    transcription = _transcribe_whole(stored, settings, transcriber)
    segments = transcription.segments or (
        # Some models answer with the text and no timings at all. One segment
        # covering the whole recording keeps every later query working; it just
        # cites the lesson rather than the minute.
        (Segment(start_ms=0, end_ms=duration, text=transcription.text),)
        if transcription.text
        else ()
    )
    chunks = build_chunks(segments)

    lesson = Lesson(
        id=new_id(),
        lesson_date=lesson_date,
        subject=subject,
        audio_path=stored,
        duration_ms=duration,
        text=transcription.text,
        chunks=len(chunks),
    )
    _save(conn, lesson, checksum, chunks)
    log.info("saved lesson %s (%d chunks)", lesson.id, len(chunks))
    return lesson


def _groq(settings: Settings) -> Transcriber:
    """The real provider, with the settings already tied to it."""
    api_key = require(settings.groq_api_key, "GROQ_API_KEY")
    model = require(settings.transcription_model, "TRANSCRIPTION_MODEL")

    def transcribe(piece: Path) -> Transcription:
        return groq_audio.transcribe(
            piece, api_key=api_key, model=model, language=settings.language
        )

    return transcribe


def _transcribe_whole(
    stored: Path, settings: Settings, transcriber: Transcriber
) -> Transcription:
    """One upload if the file is small, otherwise piece by piece."""
    if stored.stat().st_size <= settings.max_upload_mb * 1_000_000:
        return transcriber(stored)

    with tempfile.TemporaryDirectory(prefix="picatchup-") as workspace:
        pieces = audio.split(stored, Path(workspace), minutes=settings.segment_minutes)
        texts: list[str] = []
        segments: list[Segment] = []
        offset_ms = 0
        for number, piece in enumerate(pieces, start=1):
            log.info("piece %d of %d", number, len(pieces))
            part = transcriber(piece)
            texts.append(part.text)
            segments.extend(shift(part.segments, offset_ms))
            # Measured, not assumed: the cut lands on a frame boundary, so the
            # piece is never exactly `segment_minutes` long.
            offset_ms += audio.duration_ms(piece)
        return Transcription(text=" ".join(t for t in texts if t).strip(), segments=tuple(segments))


def shift(segments: Iterable[Segment], offset_ms: int) -> list[Segment]:
    """Move segments from "seconds into this piece" to "seconds into the lesson"."""
    return [
        Segment(start_ms=s.start_ms + offset_ms, end_ms=s.end_ms + offset_ms, text=s.text)
        for s in segments
    ]


def build_chunks(segments: Sequence[Segment], size: int = CHUNK_SIZE) -> list[Chunk]:
    """Group segments into searchable pieces of roughly `size` characters.

    A chunk never cuts a segment in half, so its start and end are real moments
    in the recording rather than estimates.
    """
    chunks: list[Chunk] = []
    texts: list[str] = []
    start_ms = end_ms = 0
    length = 0

    for segment in segments:
        if texts and length + len(segment.text) + 1 > size:
            chunks.append(
                Chunk(idx=len(chunks), start_ms=start_ms, end_ms=end_ms, text=" ".join(texts))
            )
            texts, length = [], 0
        if not texts:
            start_ms = segment.start_ms
        texts.append(segment.text)
        length += len(segment.text) + 1
        end_ms = segment.end_ms

    if texts:
        chunks.append(
            Chunk(idx=len(chunks), start_ms=start_ms, end_ms=end_ms, text=" ".join(texts))
        )
    return chunks


_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_TIME = re.compile(r"^\d{3,4}$")


def read_name(path: Path) -> tuple[str | None, str | None]:
    """Read date and subject out of a filename, if whoever named it put them there.

    `2026-09-15 Matematica.mp3` and `2026-09-15-1430-fisica.m4a` both work.
    Anything else just means we fall back to today and to no subject.
    """
    stem = path.stem
    match = _DATE.match(stem)
    if match is None:
        return None, None

    rest = stem[match.end() :].strip(" -_")
    words = [word for word in re.split(r"[\s_-]+", rest) if word]
    if words and _TIME.match(words[0]):
        words = words[1:]
    return match.group(1), " ".join(words) or None


def _store(settings: Settings, source: Path, checksum: str, lesson_date: str) -> Path:
    """Put the audio where it lives from now on, as mp3.

    The checksum is in the name so two recordings from the same day cannot
    overwrite each other.
    """
    destination = settings.audio_dir / f"{lesson_date}-{checksum[:8]}.mp3"
    destination.parent.mkdir(parents=True, exist_ok=True)

    if source.suffix.lower() == ".mp3":
        shutil.copy2(source, destination)
    else:
        log.info("converting %s to mp3", source.name)
        audio.to_mp3(source, destination, bitrate=settings.audio_bitrate)
    return destination


def _save(conn: sqlite3.Connection, lesson: Lesson, checksum: str, chunks: list[Chunk]) -> None:
    """One transaction: either the whole lesson is there or none of it is."""
    with conn:
        conn.execute(
            "INSERT INTO lessons"
            " (id, lesson_date, subject, audio_path, audio_sha256, duration_ms, text, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                lesson.id,
                lesson.lesson_date,
                lesson.subject,
                str(lesson.audio_path),
                checksum,
                lesson.duration_ms,
                lesson.text,
                utc_now(),
            ),
        )
        for chunk in chunks:
            chunk_id = new_id()
            conn.execute(
                "INSERT INTO chunks (id, lesson_id, idx, start_ms, end_ms, text)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (chunk_id, lesson.id, chunk.idx, chunk.start_ms, chunk.end_ms, chunk.text),
            )
            conn.execute(
                "INSERT INTO chunks_fts (chunk_id, text) VALUES (?, ?)", (chunk_id, chunk.text)
            )
