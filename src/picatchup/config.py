"""Settings, read once at startup from .env and the environment.

Real environment variables win over the .env file, which is what lets systemd
or a test set a value without editing any file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    backup_remote: str
    audio_retention_days: int
    audio_input_format: str
    audio_input_device: str
    audio_bitrate: str
    gpio_enabled: bool
    gpio_button_pin: int
    gpio_led_pin: int
    display_enabled: bool
    oled_i2c_address: int
    oled_width: int
    oled_height: int
    status_file: Path
    backup_retention_count: int
    max_upload_mb: int
    segment_minutes: int
    timezone: str
    log_level: str

    conversation_bot_token: str = ""
    mode: str = "lesson"

    def for_mode(self, mode: str) -> Settings:
        """Select an isolated store and bot; never fall back to lesson credentials."""
        if mode == self.mode:
            return self
        if mode != "conversation" or self.mode != "lesson":
            raise ConfigError("select modes from the base lesson settings")
        root = self.data_dir / "conversation"
        return replace(
            self,
            mode=mode,
            data_dir=root,
            db_path=root / "picatchup.db",
            inbox_dir=root / "inbox",
            audio_dir=root / "audio",
            backup_dir=root / "backups",
            status_file=root / "device-status.json",
            telegram_bot_token=self.conversation_bot_token,
            backup_remote="",
        )

    @property
    def secrets(self) -> list[str]:
        """The values the logger must never print. See log.setup_logging."""
        return [
            s
            for s in (self.groq_api_key, self.telegram_bot_token, self.conversation_bot_token)
            if s
        ]


def require(value: str, name: str) -> str:
    """Return a setting, or explain what to do if it was never filled in."""
    if not value:
        raise ConfigError(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return value


def load_settings(env_file: str | Path = ".env") -> Settings:
    values = _read_env_file(Path(env_file))
    values.update(os.environ)

    data_dir = Path(values.get("DATA_DIR", "data")).expanduser().resolve()

    settings = Settings(
        conversation_bot_token=values.get("CONVERSATION_TELEGRAM_BOT_TOKEN", "").strip(),
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
        backup_remote=values.get("BACKUP_REMOTE", "").strip(),
        audio_retention_days=_positive_int(values, "AUDIO_RETENTION_DAYS", default=7),
        audio_input_format=values.get("AUDIO_INPUT_FORMAT", "pulse").strip(),
        audio_input_device=values.get("AUDIO_INPUT_DEVICE", "default").strip(),
        audio_bitrate=values.get("AUDIO_BITRATE", "48k").strip(),
        gpio_enabled=_bool(values, "GPIO_ENABLED", default=True),
        gpio_button_pin=_positive_int(values, "GPIO_BUTTON_PIN", default=17),
        gpio_led_pin=_positive_int(values, "GPIO_LED_PIN", default=27),
        display_enabled=_bool(values, "DISPLAY_ENABLED", default=True),
        oled_i2c_address=_based_int(values, "OLED_I2C_ADDRESS", default=0x3C),
        oled_width=_positive_int(values, "OLED_WIDTH", default=128),
        oled_height=_positive_int(values, "OLED_HEIGHT", default=64),
        status_file=data_dir / "device-status.json",
        backup_retention_count=_positive_int(values, "BACKUP_RETENTION_COUNT", default=14),
        max_upload_mb=_positive_int(values, "MAX_UPLOAD_MB", default=20),
        segment_minutes=_positive_int(values, "SEGMENT_MINUTES", default=10),
        timezone=values.get("TIMEZONE", "Europe/Rome").strip(),
        log_level=values.get("LOG_LEVEL", "INFO").strip().upper(),
    )
    _validate(settings)
    return settings


def _validate(settings: Settings) -> None:
    if (
        settings.conversation_bot_token
        and settings.conversation_bot_token == settings.telegram_bot_token
    ):
        raise ConfigError("CONVERSATION_TELEGRAM_BOT_TOKEN must belong to a separate bot.")
    pins = (settings.gpio_button_pin, settings.gpio_led_pin)
    if any(pin > 27 for pin in pins):
        raise ConfigError("GPIO_BUTTON_PIN and GPIO_LED_PIN must be BCM pins from 1 to 27.")
    if settings.gpio_enabled and settings.gpio_button_pin == settings.gpio_led_pin:
        raise ConfigError("GPIO_BUTTON_PIN and GPIO_LED_PIN must be different.")
    if settings.display_enabled and settings.gpio_enabled and any(pin in {2, 3} for pin in pins):
        raise ConfigError("GPIO2 and GPIO3 are reserved for the configured I2C OLED.")
    if not 0x03 <= settings.oled_i2c_address <= 0x77:
        raise ConfigError("OLED_I2C_ADDRESS must be between 0x03 and 0x77.")
    if settings.display_enabled and (settings.oled_width, settings.oled_height) != (128, 64):
        raise ConfigError("this OLED interface supports OLED_WIDTH=128 and OLED_HEIGHT=64.")
    if settings.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL.")
    try:
        ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        raise ConfigError(f"TIMEZONE is not available: {settings.timezone!r}.") from None


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


def _based_int(values: dict[str, str], name: str, *, default: int) -> int:
    """Read decimal or conventional ``0x`` hexadecimal configuration."""
    raw = values.get(name, "").strip()
    if not raw:
        return default
    try:
        number = int(raw, 0)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, not {raw!r}.") from None
    if number < 1:
        raise ConfigError(f"{name} must be at least 1.")
    return number


def _bool(values: dict[str, str], name: str, *, default: bool) -> bool:
    raw = values.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false, not {raw!r}.")
