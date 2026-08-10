"""Groq Whisper: an audio file in, text with timings out.

The timings are the whole point. Without them an answer cannot say "minute 23"
and the project is just a summariser.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from picatchup.errors import ProviderError
from picatchup.providers import _http

URL = "https://api.groq.com/openai/v1/audio/transcriptions"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Segment:
    """A stretch of speech and where in the recording it happened."""

    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class Transcription:
    text: str
    segments: tuple[Segment, ...]


def transcribe(
    path: Path,
    *,
    api_key: str,
    model: str,
    language: str = "it",
    timeout: float = 600.0,
) -> Transcription:
    """Send one audio file to Whisper. It must be small enough to upload.

    Splitting a long recording into uploadable pieces is `audio.split`'s job,
    not this file's: here we do one request and nothing clever.
    """
    log.info("transcribing %s (%.1f MB)", path.name, path.stat().st_size / 1_000_000)
    answer = _http.post_file(
        URL,
        fields={
            "model": model,
            "language": language,
            "response_format": "verbose_json",
            "temperature": "0",
        },
        field_name="file",
        filename=path.name,
        content=path.read_bytes(),
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )

    text = answer.get("text")
    if not isinstance(text, str):
        raise ProviderError("Groq returned no transcription text")
    return Transcription(text=text.strip(), segments=_segments(answer.get("segments")))


def _segments(raw: Any) -> tuple[Segment, ...]:
    """Read the segment list, skipping anything malformed rather than crashing.

    A missing segment costs us one citation. A crash costs us the whole lesson,
    which we have already paid to transcribe.
    """
    if not isinstance(raw, list):
        return ()

    segments: list[Segment] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        start, end = item.get("start"), item.get("end")
        if not isinstance(text, str) or not text.strip():
            continue
        if not isinstance(start, int | float) or not isinstance(end, int | float):
            continue
        segments.append(
            Segment(start_ms=int(start * 1000), end_ms=int(end * 1000), text=text.strip())
        )
    return tuple(segments)
