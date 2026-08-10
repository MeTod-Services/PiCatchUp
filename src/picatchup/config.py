"""Settings, read once at startup from .env and the environment.

Real environment variables win over the .env file, which is what lets systemd
or a test set a value without editing any file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from picatchup.errors import ConfigError


@dataclass(frozen=True)
class Settings:
    """Everything the program needs to know that is not in the database.

    The two secrets may be empty here: `picatchup init` has no business
    demanding a Groq key. Whoever actually needs one calls `require()`.
    """

    groq_api_key: str
    telegram_bot_token: str
    transcription_model: str
    text_model: str
    language: str
    data_dir: Path
    db_path: Path
    inbox_dir: Path
    audio_dir: Path
    backup_dir: Path
    audio_retention_days: int
    audio_input_format: str
    audio_input_device: str
    audio_bitrate: str
    gpio_button_pin: int
    max_upload_mb: int
    segment_minutes: int
    timezone: str
    log_level: str

    @property
    def secrets(self) -> list[str]:
        """The values the logger must never print. See log.setup_logging."""
        return [s for s in (self.groq_api_key, self.telegram_bot_token) if s]


def require(value: str, name: str) -> str:
    """Return a setting, or explain what to do if it was never filled in."""
    if not value:
        raise ConfigError(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return value


def load_settings(env_file: str | Path = ".env") -> Settings:
    values = _read_env_file(Path(env_file))
    values.update(os.environ)

    data_dir = Path(values.get("DATA_DIR", "data")).expanduser().resolve()

    return Settings(
        groq_api_key=values.get("GROQ_API_KEY", "").strip(),
        telegram_bot_token=values.get("TELEGRAM_BOT_TOKEN", "").strip(),
        transcription_model=values.get("TRANSCRIPTION_MODEL", "whisper-large-v3-turbo").strip(),
        text_model=values.get("TEXT_MODEL", "").strip(),
        language=values.get("LANGUAGE", "it").strip(),
        data_dir=data_dir,
        db_path=data_dir / "picatchup.db",
        inbox_dir=data_dir / "inbox",
        audio_dir=data_dir / "audio",
        backup_dir=data_dir / "backups",
        audio_retention_days=_positive_int(values, "AUDIO_RETENTION_DAYS", default=7),
        audio_input_format=values.get("AUDIO_INPUT_FORMAT", "pulse").strip(),
        audio_input_device=values.get("AUDIO_INPUT_DEVICE", "default").strip(),
        audio_bitrate=values.get("AUDIO_BITRATE", "48k").strip(),
        gpio_button_pin=_positive_int(values, "GPIO_BUTTON_PIN", default=17),
        max_upload_mb=_positive_int(values, "MAX_UPLOAD_MB", default=20),
        segment_minutes=_positive_int(values, "SEGMENT_MINUTES", default=10),
        timezone=values.get("TIMEZONE", "Europe/Rome").strip(),
        log_level=values.get("LOG_LEVEL", "INFO").strip().upper(),
    )


def _read_env_file(path: Path) -> dict[str, str]:
    """Read KEY=value lines. Missing file is fine; broken syntax is not."""
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text("utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"{path} line {number}: expected KEY=value.")
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _positive_int(values: dict[str, str], name: str, *, default: int) -> int:
    raw = values.get(name, "").strip()
    if not raw:
        return default
    try:
        number = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, not {raw!r}.") from None
    if number < 1:
        raise ConfigError(f"{name} must be at least 1.")
    return number
