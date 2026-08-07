"""The boundary with the outside world.

Only modules under `picatchup.providers` may import Groq, HTTP or Telegram.
Everything else takes one of these three objects as a parameter and calls it,
which is what lets the pipeline run without network access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from picatchup.domain.provider_types import (
    GenerationResult,
    TranscriptionResult,
    Update,
)


class Transcriber(Protocol):
    """Audio to timed text. Implemented by providers/groq_audio.py."""

    model: str

    def transcribe(self, audio_path: Path, *, language: str = "it") -> TranscriptionResult: ...


class TextGenerator(Protocol):
    """Prompt to text. Implemented by providers/groq_text.py."""

    model: str

    def generate(self, *, system: str, user: str, max_tokens: int) -> GenerationResult: ...


class Messenger(Protocol):
    """Telegram. Implemented by providers/telegram.py."""

    def get_updates(self, offset: int, timeout_s: int) -> list[Update]: ...

    def send_message(self, chat_id: int, text: str) -> int:
        """Returns the Telegram message id."""
        ...

    def leave_chat(self, chat_id: int) -> None: ...
