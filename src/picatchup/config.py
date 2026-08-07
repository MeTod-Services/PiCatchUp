"""Typed application settings loaded from a dotenv file and the environment.

Environment variables take precedence over values in ``.env``. Configuration
errors name the keys to fix but never include their secret values.
"""

from __future__ import annotations

import os
import re
import shlex
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from picatchup.errors import ConfigurationError

_REQUIRED_KEYS: Final[tuple[str, ...]] = (
    "GROQ_API_KEY",
    "GROQ_RECAP_MODEL",
    "GROQ_QA_MODEL",
    "TELEGRAM_BOT_TOKEN",
)
_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TRUE_VALUES: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES: Final[frozenset[str]] = frozenset({"0", "false", "no", "off"})


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


@dataclass(frozen=True, slots=True)
class SecretValue:
    """A string whose normal representations cannot disclose its contents."""

    _value: str

    def get_secret_value(self) -> str:
        return self._value

    def __str__(self) -> str:
        return "***"

    def __repr__(self) -> str:
        return "SecretValue('***')"


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated process configuration.

    Recap and Q&A model ids are required rather than guessed: their live Groq ids
    can change. The transcription model is a frozen product decision.
    """

    environment: Environment
    data_dir: Path
    database_path: Path
    groq_api_key: SecretValue
    telegram_bot_token: SecretValue
    groq_transcription_model: str
    groq_recap_model: str
    groq_qa_model: str
    timezone: str
    audio_retention_days: int
    recap_delivery_time: time
    backup_remote: str | None
    log_level: str
    allow_debug_content: bool

    @classmethod
    def from_env(
        cls,
        env_file: str | Path | None = ".env",
        *,
        environ: Mapping[str, str] | None = None,
        required_keys: Sequence[str] = _REQUIRED_KEYS,
    ) -> Settings:
        """Load settings, with process environment overriding ``env_file``.

        Pass an empty ``required_keys`` only for commands which deliberately do not
        initialize providers. Secret fields still receive empty redacted values.
        """

        path = Path(env_file) if env_file is not None else None
        values = _read_dotenv(path) if path is not None else {}
        values.update(os.environ if environ is None else environ)

        missing = sorted(key for key in required_keys if not values.get(key, "").strip())
        if missing:
            location = str(path) if path is not None else "the process environment"
            names = ", ".join(missing)
            raise ConfigurationError(
                f"Missing required settings: {names}.",
                hint=f"Set them in the environment or {location}; use .env.example as a template.",
            )

        environment = _parse_environment(values.get("ENV", "development"))
        data_dir = Path(values.get("DATA_DIR", "data")).expanduser().resolve()
        configured_database_path = values.get("DATABASE_PATH")
        if configured_database_path is None:
            database_path = data_dir / "db" / "picatchup.db"
        else:
            candidate = Path(configured_database_path).expanduser()
            database_path = (
                candidate if candidate.is_absolute() else data_dir / candidate
            ).resolve()
        if not database_path.is_relative_to(data_dir):
            raise ConfigurationError(
                "DATABASE_PATH must resolve inside DATA_DIR.",
                hint="Use a path relative to DATA_DIR or a contained absolute path.",
            )
        retention_days = _parse_int(
            "AUDIO_RETENTION_DAYS", values.get("AUDIO_RETENTION_DAYS", "7"), minimum=1
        )
        delivery_time = _parse_time(values.get("RECAP_DELIVERY_TIME", "15:00"))
        allow_debug_content = _parse_bool(
            "ALLOW_DEBUG_CONTENT", values.get("ALLOW_DEBUG_CONTENT", "0")
        )
        log_level = values.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError(
                "LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL."
            )
        timezone = values.get("TIMEZONE", "Europe/Rome").strip()
        try:
            ZoneInfo(timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ConfigurationError("TIMEZONE must be a valid IANA timezone name.") from exc
        if (
            environment is Environment.PRODUCTION
            and log_level == "DEBUG"
            and not allow_debug_content
        ):
            raise ConfigurationError(
                "DEBUG logging is disabled in production.",
                hint="Use INFO, or explicitly set ALLOW_DEBUG_CONTENT=1 after a privacy review.",
            )

        return cls(
            environment=environment,
            data_dir=data_dir,
            database_path=database_path,
            groq_api_key=SecretValue(values.get("GROQ_API_KEY", "")),
            telegram_bot_token=SecretValue(values.get("TELEGRAM_BOT_TOKEN", "")),
            groq_transcription_model=values.get(
                "GROQ_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo"
            ).strip(),
            groq_recap_model=values.get("GROQ_RECAP_MODEL", "").strip(),
            groq_qa_model=values.get("GROQ_QA_MODEL", "").strip(),
            timezone=timezone,
            audio_retention_days=retention_days,
            recap_delivery_time=delivery_time,
            backup_remote=values.get("BACKUP_REMOTE", "").strip() or None,
            log_level=log_level,
            allow_debug_content=allow_debug_content,
        )

    @property
    def redaction_secrets(self) -> tuple[str, ...]:
        """Secret values which must be registered with the logging redactor."""

        return tuple(
            value
            for value in (
                self.groq_api_key.get_secret_value(),
                self.telegram_bot_token.get_secret_value(),
            )
            if value
        )


def load_settings(
    env_file: str | Path | None = ".env",
    *,
    environ: Mapping[str, str] | None = None,
    required_keys: Sequence[str] = _REQUIRED_KEYS,
) -> Settings:
    """Convenience wrapper for dependency-injection roots."""

    return Settings.from_env(env_file, environ=environ, required_keys=required_keys)


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    try:
        with path.open("rb") as handle:
            file_stat = os.fstat(handle.fileno())
            if not stat.S_ISREG(file_stat.st_mode):
                raise ConfigurationError(f"Environment file is not a regular file: {path}")
            if stat.S_IMODE(file_stat.st_mode) & 0o077:
                raise ConfigurationError(
                    f"Environment file permissions are too broad: {path}.",
                    hint="Run chmod 600 on the file before starting PiCatchUp.",
                )
            contents = handle.read()
    except OSError as exc:
        raise ConfigurationError(f"Cannot read environment file {path}: {exc.strerror}") from exc
    try:
        lines = contents.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ConfigurationError(f"Environment file must be valid UTF-8: {path}") from exc

    values: dict[str, str] = {}

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigurationError(
                f"Invalid dotenv syntax in {path} at line {line_number}: expected KEY=value."
            )
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not _KEY_PATTERN.fullmatch(key):
            raise ConfigurationError(
                f"Invalid dotenv key in {path} at line {line_number}."
            )
        values[key] = _parse_dotenv_value(raw_value, path=path, line_number=line_number)
    return values


def _parse_dotenv_value(raw: str, *, path: Path, line_number: int) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in {'"', "'"}:
        try:
            parsed = shlex.split(value, comments=True, posix=True)
        except ValueError as exc:
            raise ConfigurationError(
                f"Invalid quoted value in {path} at line {line_number}."
            ) from exc
        if len(parsed) != 1:
            raise ConfigurationError(
                f"Invalid value in {path} at line {line_number}: quote values containing spaces."
            )
        return parsed[0]
    # In dotenv syntax, a comment begins at a hash preceded by whitespace.
    return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()


def _parse_environment(value: str) -> Environment:
    try:
        return Environment(value.strip().lower())
    except ValueError as exc:
        raise ConfigurationError("ENV must be development, test, or production.") from exc


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ConfigurationError(f"{name} must be one of: 1/0, true/false, yes/no, on/off.")


def _parse_int(name: str, value: str, *, minimum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer.") from exc
    if parsed < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}.")
    return parsed


def _parse_time(value: str) -> time:
    if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) is None:
        raise ConfigurationError("RECAP_DELIVERY_TIME must use HH:MM (24-hour time).")
    hour, minute = (int(part) for part in value.split(":"))
    return time(hour, minute)


__all__ = ["Environment", "SecretValue", "Settings", "load_settings"]
