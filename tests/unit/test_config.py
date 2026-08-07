from __future__ import annotations

from datetime import time
from pathlib import Path

import pytest

from picatchup.config import Environment, Settings
from picatchup.errors import ConfigurationError


def _required_environment() -> dict[str, str]:
    return {
        "GROQ_API_KEY": "gsk-test-not-real",
        "GROQ_RECAP_MODEL": "configured-recap-model",
        "GROQ_QA_MODEL": "configured-qa-model",
        "TELEGRAM_BOT_TOKEN": "123456789:not-a-real-telegram-token",
    }


def test_settings_load_dotenv_with_environment_override(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "GROQ_API_KEY='from dotenv with spaces'",
                "GROQ_RECAP_MODEL=recap-from-file",
                "GROQ_QA_MODEL=qa-from-file",
                "TELEGRAM_BOT_TOKEN=123456789:file-token-value-abcdefgh",
                "DATA_DIR=./private-data",
                "AUDIO_RETENTION_DAYS=10",
                "RECAP_DELIVERY_TIME=14:45",
            ]
        ),
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    settings = Settings.from_env(
        env_file,
        environ={"GROQ_RECAP_MODEL": "recap-from-process"},
    )

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.groq_api_key.get_secret_value() == "from dotenv with spaces"
    assert settings.groq_recap_model == "recap-from-process"
    assert settings.groq_qa_model == "qa-from-file"
    assert settings.data_dir == Path("private-data").resolve()
    assert settings.database_path == Path("private-data/db/picatchup.db").resolve()
    assert settings.audio_retention_days == 10
    assert settings.recap_delivery_time == time(14, 45)
    assert str(settings.groq_api_key) == "***"
    assert "from dotenv" not in repr(settings.groq_api_key)


def test_missing_keys_raise_actionable_error_without_secret_values(tmp_path: Path) -> None:
    existing_secret = "gsk-this-value-must-not-appear"

    with pytest.raises(ConfigurationError) as caught:
        Settings.from_env(
            tmp_path / "missing.env",
            environ={"GROQ_API_KEY": existing_secret},
        )

    message = str(caught.value)
    assert "Missing required settings" in message
    assert "GROQ_RECAP_MODEL" in message
    assert "GROQ_QA_MODEL" in message
    assert "TELEGRAM_BOT_TOKEN" in message
    assert ".env.example" in message
    assert existing_secret not in message
    assert caught.value.retryable is False


def test_environment_values_override_dotenv(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(f"{key}=file-{value}" for key, value in _required_environment().items()),
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    process_values = _required_environment()

    settings = Settings.from_env(env_file, environ=process_values)

    assert settings.groq_api_key.get_secret_value() == process_values["GROQ_API_KEY"]
    assert settings.groq_recap_model == process_values["GROQ_RECAP_MODEL"]


def test_production_debug_content_requires_explicit_opt_in() -> None:
    values = _required_environment() | {"ENV": "production", "LOG_LEVEL": "DEBUG"}

    with pytest.raises(ConfigurationError, match="disabled in production"):
        Settings.from_env(None, environ=values)

    allowed = Settings.from_env(None, environ=values | {"ALLOW_DEBUG_CONTENT": "1"})
    assert allowed.allow_debug_content is True


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("AUDIO_RETENTION_DAYS", "0", "at least 1"),
        ("AUDIO_RETENTION_DAYS", "seven", "integer"),
        ("RECAP_DELIVERY_TIME", "3pm", "HH:MM"),
        ("RECAP_DELIVERY_TIME", "15", "HH:MM"),
        ("ENV", "live", "development, test, or production"),
        ("TIMEZONE", "Not/A_Real_Zone", "IANA timezone"),
    ],
)
def test_invalid_values_name_the_setting(key: str, value: str, expected: str) -> None:
    values = _required_environment() | {key: value}

    with pytest.raises(ConfigurationError, match=expected):
        Settings.from_env(None, environ=values)


def test_dotenv_rejects_group_or_world_permissions(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GROQ_API_KEY=planted-secret-value\n", encoding="utf-8")
    env_file.chmod(0o644)

    with pytest.raises(ConfigurationError, match="permissions are too broad") as caught:
        Settings.from_env(env_file, environ={})

    assert "planted-secret-value" not in str(caught.value)
    assert "chmod 600" in str(caught.value)


def test_database_path_must_remain_inside_data_dir(tmp_path: Path) -> None:
    values = _required_environment() | {
        "DATA_DIR": str(tmp_path / "data"),
        "DATABASE_PATH": "../outside.db",
    }

    with pytest.raises(ConfigurationError, match="inside DATA_DIR"):
        Settings.from_env(None, environ=values)
