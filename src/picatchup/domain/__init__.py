"""Shared data types. No logic lives here, only shapes.

`models` mirrors the database tables; `provider_types` is what the outside world
hands back. Ids are strings, UTC timestamps are ISO-8601 strings, and dates are
"YYYY-MM-DD".
"""

from __future__ import annotations

from .models import Chunk, Delivery, Lesson, Recap, User
from .provider_types import (
    GenerationResult,
    SearchHit,
    TranscriptionResult,
    TranscriptionSegment,
    Update,
)

__all__ = [
    "Chunk",
    "Delivery",
    "GenerationResult",
    "Lesson",
    "Recap",
    "SearchHit",
    "TranscriptionResult",
    "TranscriptionSegment",
    "Update",
    "User",
]
