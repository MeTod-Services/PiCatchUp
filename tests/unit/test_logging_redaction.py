from __future__ import annotations

import io
import json
import logging
from contextlib import redirect_stderr
from pathlib import Path

from picatchup.config import Settings
from picatchup.logging_setup import configure_logging, correlation_context


def test_registered_api_key_never_reaches_json_log_output() -> None:
    fake_api_key = "gsk_FAKE_API_KEY_do_not_emit_123456"
    fake_telegram_token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd"
    output = io.StringIO()
    internal_errors = io.StringIO()
    with redirect_stderr(internal_errors):
        configure_logging(secrets=[fake_api_key], stream=output)

        logger = logging.getLogger("picatchup.test.child")
        with correlation_context("test-correlation"):
            logger.info(
                "provider failed api_key=%s Bearer bearer-value %s",
                fake_api_key,
                fake_telegram_token,
                extra={
                    "event": "provider.request_failed",
                    "provider_context": {"api_key": fake_api_key, "attempt": 2},
                },
            )

    assert internal_errors.getvalue() == ""

    rendered = output.getvalue()
    assert fake_api_key not in rendered
    assert fake_telegram_token not in rendered
    assert "bearer-value" not in rendered
    assert rendered.count("***") >= 3

    record = json.loads(rendered)
    assert record["level"] == "INFO"
    assert record["logger"] == "picatchup.test.child"
    assert record["event"] == "provider.request_failed"
    assert record["correlation_id"] == "test-correlation"
    assert record["provider_context"]["api_key"] == "***"
    assert record["provider_context"]["attempt"] == 2


def test_invalid_message_formatting_drops_raw_arguments_without_stderr() -> None:
    raw_argument = "raw-argument-must-not-leak"
    output = io.StringIO()
    internal_errors = io.StringIO()
    configure_logging(stream=output)

    with redirect_stderr(internal_errors):
        logging.getLogger("picatchup.test").error(
            "password=%(missing)s", {"unexpected": raw_argument}
        )

    assert raw_argument not in output.getvalue()
    assert raw_argument not in internal_errors.getvalue()
    assert json.loads(output.getvalue())["message"] == "invalid log message formatting"


def test_redaction_covers_exception_text_and_unregistered_credential_shapes() -> None:
    planted_secret = "secret-in-exception-98765"
    output = io.StringIO()
    configure_logging(secrets=[planted_secret], stream=output)

    try:
        raise RuntimeError(f"upstream leaked {planted_secret}")
    except RuntimeError:
        logging.getLogger("picatchup.test").exception(
            "request headers: Authorization=Bearer abc.def-123 password=hunter2"
        )

    rendered = output.getvalue()
    assert planted_secret not in rendered
    assert "abc.def-123" not in rendered
    assert "hunter2" not in rendered

    record = json.loads(rendered)
    assert "***" in record["message"]
    assert "***" in record["exception"]


def test_dotenv_only_secrets_are_registered_from_settings(tmp_path: Path) -> None:
    planted_secret = "dotenv-only-secret-value"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"GROQ_API_KEY={planted_secret}",
                "GROQ_RECAP_MODEL=recap-model",
                "GROQ_QA_MODEL=qa-model",
                "TELEGRAM_BOT_TOKEN=123456789:abcdefghijklmnopqrstuvwxyzABCD",
            ]
        ),
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    settings = Settings.from_env(env_file, environ={})
    output = io.StringIO()
    configure_logging(settings=settings, stream=output)

    logging.getLogger("picatchup.test").error("upstream=%s", planted_secret)

    assert planted_secret not in output.getvalue()
    assert "***" in output.getvalue()


def test_configure_logging_removes_existing_application_child_handlers() -> None:
    child_output = io.StringIO()
    child = logging.getLogger("picatchup.test.owned-handler")
    child.addHandler(logging.StreamHandler(child_output))
    root_output = io.StringIO()

    configure_logging(secrets=["child-handler-secret"], stream=root_output)
    child.warning("token=%s", "child-handler-secret")

    assert child_output.getvalue() == ""
    assert "child-handler-secret" not in root_output.getvalue()


def test_extra_fields_cannot_replace_canonical_log_fields() -> None:
    output = io.StringIO()
    configure_logging(stream=output)

    logging.getLogger("picatchup.real").error(
        "real message",
        extra={
            "event": "test.real_event",
            "level": "FORGED",
            "logger": "forged.logger",
            "ts": "never",
            "exception": "forged exception",
        },
    )

    record = json.loads(output.getvalue())
    assert record["level"] == "ERROR"
    assert record["logger"] == "picatchup.real"
    assert record["event"] == "test.real_event"
    assert record["ts"] != "never"
    assert "exception" not in record


def test_secret_environment_values_are_registered_automatically(monkeypatch) -> None:
    planted_secret = "automatic-fake-token-value"
    monkeypatch.setenv("SOME_SERVICE_API_KEY", planted_secret)
    output = io.StringIO()
    configure_logging(stream=output)

    logging.getLogger("picatchup.test").warning("credential=%s", planted_secret)

    assert planted_secret not in output.getvalue()
    assert "***" in output.getvalue()
