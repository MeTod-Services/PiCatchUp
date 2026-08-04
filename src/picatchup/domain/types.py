"""Shared scalar aliases used by the frozen domain contracts.

Timestamps are UTC ISO-8601 strings and local dates are ``YYYY-MM-DD`` strings
at persistence and job-payload boundaries (PLAN.md §5).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias

EntityId: TypeAlias = str
UtcTimestamp: TypeAlias = str
LocalDate: TypeAlias = str
JsonObject: TypeAlias = Mapping[str, object]
