"""Frozen external-provider boundaries for PiCatchUp (T-S01).

Only modules under :mod:`picatchup.providers` may import external provider SDKs.
Application code depends on these synchronous protocols and provider-neutral domain
records. Changes to this file require the joint review described in PLAN.md §11.5.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO, Protocol

from picatchup.domain.provider_types import (
    GenerationResult,
    Keyboard,
    RetrievedChunk,
    SentMessage,
    TranscriptionResult,
    Update,
)


class TranscriptionProvider(Protocol):
    name: str
    model: str

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str = "it",
        prompt: str | None = None,
        timeout_s: float = 120.0,
    ) -> TranscriptionResult: ...


class TextGenerationProvider(Protocol):
    name: str
    model: str

    def generate(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float = 0.2,
        stop: Sequence[str] | None = None,
        timeout_s: float = 60.0,
    ) -> GenerationResult: ...


class MessagingProvider(Protocol):
    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        keyboard: Keyboard | None = None,
        idempotency_key: str,
    ) -> SentMessage: ...

    def get_updates(self, offset: int, timeout_s: int) -> list[Update]: ...

    def answer_callback_query(self, callback_query_id: str) -> None: ...

    def leave_chat(self, chat_id: int) -> None: ...


class Retriever(Protocol):
    def search(
        self,
        *,
        class_id: str,
        query: str,
        lesson_date: str | None,
        subject: str | None,
        limit: int,
    ) -> list[RetrievedChunk]: ...


class BlobStore(Protocol):
    def put(self, src: Path, key: str) -> str: ...

    def open(self, key: str) -> BinaryIO: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...
