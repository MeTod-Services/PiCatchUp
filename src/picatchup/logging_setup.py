"""Structured JSON logging with defense-in-depth secret redaction."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final, TextIO

from picatchup.ids import new_correlation_id

if TYPE_CHECKING:
    from picatchup.config import Settings

_REDACTED: Final[str] = "***"
_SENSITIVE_ENV_KEY: Final[re.Pattern[str]] = re.compile(
    r"(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTHORIZATION)", re.IGNORECASE
)
_BEARER_TOKEN: Final[re.Pattern[str]] = re.compile(
    r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE
)
_TELEGRAM_TOKEN: Final[re.Pattern[str]] = re.compile(
    r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b"
)
_KEY_VALUE_SECRET: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix>[\"']?(?:api[_-]?key|token|password|secret)[\"']?\s*[:=]\s*[\"']?)"
    r"(?P<value>[^\"'&,\s}]+)",
    re.IGNORECASE,
)
_CORRELATION_ID: ContextVar[str | None] = ContextVar("picatchup_correlation_id", default=None)
_STANDARD_RECORD_KEYS: Final[frozenset[str]] = frozenset(
    set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "correlation_id"}
)
_CANONICAL_FIELDS: Final[frozenset[str]] = frozenset(
    {"ts", "level", "logger", "event", "message", "correlation_id", "exception", "stack"}
)


class Redactor:
    """Redact registered values and common credential shapes."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        # Longest first prevents one secret which prefixes another from leaving a suffix.
        self._secrets = tuple(
            sorted({secret for secret in secrets if secret}, key=len, reverse=True)
        )

    def text(self, value: str) -> str:
        redacted = value
        for secret in self._secrets:
            redacted = redacted.replace(secret, _REDACTED)
        redacted = _BEARER_TOKEN.sub("Bearer ***", redacted)
        redacted = _TELEGRAM_TOKEN.sub(_REDACTED, redacted)
        return _KEY_VALUE_SECRET.sub(lambda match: f"{match.group('prefix')}***", redacted)

    def value(self, value: object) -> object:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, bytes):
            return self.text(value.decode("utf-8", errors="replace"))
        if isinstance(value, Mapping):
            return {
                str(key): (
                    _REDACTED
                    if _SENSITIVE_ENV_KEY.search(str(key))
                    else self.value(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, tuple):
            return tuple(self.value(item) for item in value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, set):
            return {self.value(item) for item in value}
        return value


class RedactionFilter(logging.Filter):
    """Sanitize messages and arguments before any handler receives a record."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.redactor = Redactor(secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        # Redact only after interpolation. Redacting a ``token=%s`` template first
        # would leave an unmatched argument; logging's error fallback can then print
        # that raw argument to stderr.
        try:
            message = record.getMessage()
        except Exception:  # Logging must never invoke its raw-argument fallback.
            message = "invalid log message formatting"
        record.msg = self.redactor.text(message)
        record.args = ()
        if not hasattr(record, "correlation_id"):
            record.correlation_id = _CORRELATION_ID.get()
        return True


class JsonFormatter(logging.Formatter):
    """Emit one compact JSON object per record, redacting again at output time."""

    def __init__(self, redactor: Redactor | None = None) -> None:
        super().__init__()
        self.redactor = redactor or Redactor()

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", "log.message"),
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None) or _CORRELATION_ID.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        for key, value in record.__dict__.items():
            if (
                key not in _STANDARD_RECORD_KEYS
                and key not in _CANONICAL_FIELDS
                and not key.startswith("_")
            ):
                payload[key] = value

        safe_payload = self.redactor.value(_json_safe(payload))
        serialized = json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":"))
        # Last-boundary pass also covers fallback reprs and formatted tracebacks.
        return self.redactor.text(serialized)


def configure_logging(
    *,
    settings: Settings | None = None,
    level: str | int | None = None,
    secrets: Iterable[str] = (),
    stream: TextIO | None = None,
    force: bool = True,
) -> logging.Logger:
    """Configure root JSON logging and return the root logger.

    Secret-looking environment variables are registered automatically. The filter
    is attached to both root and handlers: handler attachment is required because
    logger filters are not inherited by descendant loggers.
    """

    registered_secrets = set(secrets)
    registered_secrets.update(_known_environment_secrets())
    if settings is not None:
        registered_secrets.update(settings.redaction_secrets)
        if level is None:
            level = settings.log_level
    redaction_filter = RedactionFilter(registered_secrets)

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.addFilter(redaction_filter)
    handler.setFormatter(JsonFormatter(redaction_filter.redactor))

    root = logging.getLogger()
    if force:
        for existing in root.handlers[:]:
            root.removeHandler(existing)
            existing.close()
        for existing_filter in root.filters[:]:
            if isinstance(existing_filter, RedactionFilter):
                root.removeFilter(existing_filter)
        _reset_application_loggers()
    root.setLevel(level or "INFO")
    root.addFilter(redaction_filter)
    root.addHandler(handler)
    return root


# Familiar alias for call sites which use setup_* naming.
setup_logging = configure_logging


def get_correlation_id() -> str | None:
    return _CORRELATION_ID.get()


@contextmanager
def correlation_context(correlation_id: str | None = None) -> Iterator[str]:
    """Bind a correlation id for all logs in an operation, then restore context."""

    bound = correlation_id or new_correlation_id()
    token = _CORRELATION_ID.set(bound)
    try:
        yield bound
    finally:
        _CORRELATION_ID.reset(token)


def _reset_application_loggers() -> None:
    """Enforce root-only sinks for application loggers known at setup time."""

    for name, candidate in logging.root.manager.loggerDict.items():
        if (
            (name == "picatchup" or name.startswith("picatchup."))
            and isinstance(candidate, logging.Logger)
        ):
            for handler in candidate.handlers[:]:
                candidate.removeHandler(handler)
                handler.close()
            candidate.propagate = True


def _known_environment_secrets() -> Iterator[str]:
    for key, value in os.environ.items():
        if value and _SENSITIVE_ENV_KEY.search(key):
            yield value


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return repr(value)


__all__ = [
    "JsonFormatter",
    "RedactionFilter",
    "Redactor",
    "configure_logging",
    "correlation_context",
    "get_correlation_id",
    "setup_logging",
]
