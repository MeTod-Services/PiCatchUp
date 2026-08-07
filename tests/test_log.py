from __future__ import annotations

import io
import logging

from picatchup.log import setup_logging


def log_to_string(secrets: list[str], message: str, *args: object) -> str:
    """Run one log call through the real handler and return what came out."""
    setup_logging(secrets=secrets)
    handler = logging.getLogger().handlers[0]
    handler.setStream(io.StringIO())  # type: ignore[attr-defined]

    logging.getLogger("picatchup.test").info(message, *args)
    return handler.stream.getvalue()  # type: ignore[attr-defined]


def test_a_secret_passed_as_an_argument_never_reaches_the_output() -> None:
    output = log_to_string(["gsk_supersecret"], "calling groq with key=%s", "gsk_supersecret")

    assert "gsk_supersecret" not in output
    assert "key=***" in output


def test_a_secret_inside_the_message_is_hidden_too() -> None:
    output = log_to_string(["gsk_supersecret"], "url is https://x/gsk_supersecret/y")

    assert "gsk_supersecret" not in output


def test_ordinary_messages_are_left_alone() -> None:
    output = log_to_string(["gsk_supersecret"], "transcribed %s in %d ms", "lesson.m4a", 1200)

    assert "transcribed lesson.m4a in 1200 ms" in output


def test_nothing_breaks_when_there_are_no_secrets() -> None:
    assert "hello world" in log_to_string([], "hello %s", "world")
