"""Offline tests for Telegram polling, authorization, and routing."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable

import pytest

from picatchup import bot, users
from picatchup.config import Settings
from picatchup.domain import Update
from picatchup.errors import ProviderError


class Postbox:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.left: list[int] = []

    def send(self, text: str, chat_id: int) -> list[int]:
        self.sent.append((chat_id, text))
        return [len(self.sent)]

    def leave(self, chat_id: int) -> None:
        self.left.append(chat_id)


def incoming(
    text: str | None,
    *,
    update_id: int = 1,
    user_id: int | None = 111,
    chat_id: int | None = 111,
    chat_type: str | None = "private",
) -> Update:
    return Update(update_id, user_id, chat_id, chat_type, 10, text)


def no_answer(conn: sqlite3.Connection, settings: Settings, text: str) -> str:
    raise AssertionError("the answerer must not see this update")


def handle(
    conn: sqlite3.Connection,
    settings: Settings,
    update: Update,
    postbox: Postbox,
    answerer: bot.Answerer = no_answer,
) -> None:
    bot.handle_update(conn, settings, update, postbox.send, postbox.leave, answerer)


def test_unauthorized_user_gets_only_a_neutral_reply(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    postbox = Postbox()

    handle(conn, settings, incoming("domanda privata"), postbox)

    assert postbox.sent == [(111, bot.UNAUTHORIZED_TEXT)]
    assert postbox.left == []


def test_group_is_rejected_and_left_without_routing_its_content(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    users.add(conn, 111, "Ada")
    postbox = Postbox()

    handle(
        conn,
        settings,
        incoming("contenuto che non deve essere esposto", chat_id=-99, chat_type="group"),
        postbox,
    )

    assert postbox.sent == [(-99, bot.GROUP_TEXT)]
    assert postbox.left == [-99]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/start", bot.START_TEXT),
        ("/aiuto", bot.HELP_TEXT),
        ("/help@PiCatchUpBot", bot.HELP_TEXT),
        ("/privacy", bot.PRIVACY_TEXT),
        ("/sconosciuto", bot.HELP_TEXT),
    ],
)
def test_commands_are_routed_in_italian(
    conn: sqlite3.Connection, settings: Settings, command: str, expected: str
) -> None:
    users.add(conn, 111, "Ada")
    postbox = Postbox()

    handle(conn, settings, incoming(command), postbox)

    assert postbox.sent == [(111, expected)]


def test_recap_returns_the_most_recent_one(conn: sqlite3.Connection, settings: Settings) -> None:
    users.add(conn, 111, "Ada")
    conn.executemany(
        "INSERT INTO recaps (id, recap_date, text, model, created_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("old", "2026-09-14", "vecchio", "test", "2026-09-14T15:00:00+00:00"),
            ("new", "2026-09-15", "più recente", "test", "2026-09-15T15:00:00+00:00"),
        ],
    )
    postbox = Postbox()

    handle(conn, settings, incoming("/riassunto"), postbox)

    assert postbox.sent == [(111, "più recente")]


def test_recap_has_a_neutral_unavailable_reply(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    users.add(conn, 111, "Ada")
    postbox = Postbox()

    handle(conn, settings, incoming("/riassunto"), postbox)

    assert postbox.sent == [(111, bot.RECAP_UNAVAILABLE_TEXT)]


def test_ordinary_text_is_routed_to_the_answerer(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    users.add(conn, 111, "Ada")
    postbox = Postbox()
    questions: list[str] = []

    def answer(connection: sqlite3.Connection, config: Settings, text: str) -> str:
        assert connection is conn
        assert config is settings
        questions.append(text)
        return "Risposta fondata sulla lezione."

    handle(conn, settings, incoming("  Cosa sono le derivate?  "), postbox, answer)

    assert questions == ["Cosa sono le derivate?"]
    assert postbox.sent == [(111, "Risposta fondata sulla lezione.")]


def test_polling_advances_offsets_and_skips_a_malformed_update(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    users.add(conn, 111, "Ada")
    postbox = Postbox()
    stopped = threading.Event()
    offsets: list[tuple[int, int]] = []
    batches = [
        [
            incoming(None, update_id=7, user_id=None, chat_id=None, chat_type=None),
            incoming("/help", update_id=8),
        ],
        [],
    ]

    def receive(offset: int, timeout: int) -> list[Update]:
        offsets.append((offset, timeout))
        result = batches.pop(0)
        if not batches:
            stopped.set()
        return result

    bot.run(
        conn,
        settings,
        get_updates=receive,
        sender=postbox.send,
        leaver=postbox.leave,
        sleeper=lambda seconds: None,
        answerer=no_answer,
        stop_event=stopped,
        poll_timeout=12,
    )

    assert offsets == [(0, 12), (9, 12)]
    assert postbox.sent == [(111, bot.HELP_TEXT)]


def test_poll_offset_survives_a_bot_restart(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    users.add(conn, 111, "Ada")
    postbox = Postbox()
    first_stop = threading.Event()

    def first_receive(offset: int, timeout: int) -> list[Update]:
        first_stop.set()
        return [incoming("/help", update_id=41)]

    bot.run(
        conn,
        settings,
        get_updates=first_receive,
        sender=postbox.send,
        leaver=postbox.leave,
        sleeper=lambda seconds: None,
        answerer=no_answer,
        stop_event=first_stop,
    )

    seen_offsets: list[int] = []
    second_stop = threading.Event()

    def second_receive(offset: int, timeout: int) -> list[Update]:
        seen_offsets.append(offset)
        second_stop.set()
        return []

    bot.run(
        conn,
        settings,
        get_updates=second_receive,
        sender=postbox.send,
        leaver=postbox.leave,
        sleeper=lambda seconds: None,
        answerer=no_answer,
        stop_event=second_stop,
    )

    assert seen_offsets == [42]
    assert postbox.sent == [(111, bot.HELP_TEXT)]


def test_provider_error_waits_and_does_not_exit_the_loop(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    postbox = Postbox()
    stopped = threading.Event()
    attempts = 0
    sleeps: list[float] = []

    def receive(offset: int, timeout: int) -> list[Update]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ProviderError("temporary failure")
        stopped.set()
        return []

    sleeper: Callable[[float], None] = sleeps.append
    bot.run(
        conn,
        settings,
        get_updates=receive,
        sender=postbox.send,
        leaver=postbox.leave,
        sleeper=sleeper,
        answerer=no_answer,
        stop_event=stopped,
        retry_delay=0.25,
    )

    assert attempts == 2
    assert sleeps == [0.25, 0.25]
