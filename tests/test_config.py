from __future__ import annotations

from pathlib import Path

import pytest

from picatchup.config import load_settings, require
from picatchup.errors import ConfigError


def write_env(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_reads_values_and_derives_paths(tmp_path: Path) -> None:
    env = write_env(
        tmp_path,
        "# a comment\n"
        "GROQ_API_KEY=gsk_secret\n"
        'TEXT_MODEL="some-model"\n'
        f"DATA_DIR={tmp_path / 'store'}\n"
        "\n",
    )

    settings = load_settings(env)

    assert settings.groq_api_key == "gsk_secret"
    assert settings.text_model == "some-model", "quotes should be stripped"
    assert settings.db_path == tmp_path / "store" / "picatchup.db"
    assert settings.inbox_dir == tmp_path / "store" / "inbox"


def test_defaults_apply_without_an_env_file(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "missing.env")

    assert settings.audio_retention_days == 7
    assert settings.gpio_button_pin == 17
    assert settings.gpio_led_pin == 27
    assert settings.oled_i2c_address == 0x3C
    assert settings.backup_retention_count == 14
    assert settings.backup_remote == ""
    assert settings.timezone == "Europe/Rome"
    assert settings.groq_api_key == "", "a missing secret is empty, not an error"


def test_environment_wins_over_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = write_env(tmp_path, "TIMEZONE=Europe/Rome\n")
    monkeypatch.setenv("TIMEZONE", "UTC")

    assert load_settings(env).timezone == "UTC"


def test_secrets_lists_only_the_values_that_were_set(tmp_path: Path) -> None:
    env = write_env(tmp_path, "GROQ_API_KEY=gsk_secret\nTELEGRAM_BOT_TOKEN=\n")

    assert load_settings(env).secrets == ["gsk_secret"]


@pytest.mark.parametrize(
    "line, expected",
    [
        ("this line has no equals sign", "expected KEY=value"),
        ("AUDIO_RETENTION_DAYS=soon", "must be a whole number"),
        ("AUDIO_RETENTION_DAYS=0", "must be at least 1"),
        ("GPIO_BUTTON_PIN=not-a-pin", "must be a whole number"),
        ("OLED_I2C_ADDRESS=not-an-address", "must be a whole number"),
        ("DISPLAY_ENABLED=perhaps", "must be true or false"),
        ("GPIO_LED_PIN=17", "must be different"),
        ("GPIO_BUTTON_PIN=2", "reserved for the configured I2C OLED"),
        ("OLED_WIDTH=64", "supports OLED_WIDTH=128"),
        ("LOG_LEVEL=LOUD", "LOG_LEVEL must be"),
        ("TIMEZONE=Nowhere/Imaginary", "TIMEZONE is not available"),
    ],
)
def test_bad_values_explain_themselves(tmp_path: Path, line: str, expected: str) -> None:
    env = write_env(tmp_path, line + "\n")

    with pytest.raises(ConfigError, match=expected):
        load_settings(env)


def test_require_names_the_missing_key() -> None:
    with pytest.raises(ConfigError, match="GROQ_API_KEY is not set"):
        require("", "GROQ_API_KEY")

    assert require("gsk_secret", "GROQ_API_KEY") == "gsk_secret"
