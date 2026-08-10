"""Getting the recap onto people's phones.

The rule that matters: ordinary retries avoid duplicate recaps. We record each
Telegram-accepted send and skip it later. A process crash after Telegram accepts
a message but before SQLite commits can still cause a duplicate on retry; this
MVP deliberately does not claim distributed exactly-once delivery.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

from picatchup import users
from picatchup.config import Settings, require
from picatchup.errors import PiCatchUpError, ProviderError
from picatchup.providers import telegram
from picatchup.util import utc_now

# (text, telegram id) -> the id of every message that arrived
Sender = Callable[[str, int], list[int]]

log = logging.getLogger(__name__)


@dataclass
class Delivered:
    sent_to: list[str] = field(default_factory=list)
    already_had_it: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def send_recap(
    conn: sqlite3.Connection,
    settings: Settings,
    recap_date: str,
    *,
    sender: Sender | None = None,
    force: bool = False,
) -> Delivered:
    """Send the recap of one day to everyone who has not received it yet."""
    recap = conn.execute(
        "SELECT id, text FROM recaps WHERE recap_date = ?", (recap_date,)
    ).fetchone()
    if recap is None:
        raise PiCatchUpError(
            f"there is no recap for {recap_date}. Run: picatchup recap {recap_date}"
        )

    people = users.recipients(conn)
    if not people:
        raise PiCatchUpError("there is nobody to send it to. Add someone: picatchup user add")

    sender = sender or _telegram(settings)
    result = Delivered()

    for person in people:
        if not force and _already_sent(conn, recap["id"], person["id"]):
            result.already_had_it.append(person["display_name"])
            continue
        try:
            message_ids = sender(recap["text"], person["telegram_user_id"])
        except ProviderError as error:
            # One blocked bot must not stop the rest of the class from reading
            # the recap, so we note it and carry on.
            log.warning("delivery_failure user_id=%s error=%s", person["id"], error)
            result.failed.append(person["display_name"])
            continue
        _record(conn, recap["id"], person["id"], message_ids)
        log.info("delivery_success recap=%s user_id=%s", recap["id"], person["id"])
        result.sent_to.append(person["display_name"])

    return result


def _telegram(settings: Settings) -> Sender:
    token = require(settings.telegram_bot_token, "TELEGRAM_BOT_TOKEN")

    def send(text: str, chat_id: int) -> list[int]:
        return telegram.send_message(text, token=token, chat_id=chat_id)

    return send


def _already_sent(conn: sqlite3.Connection, recap_id: str, user_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM deliveries WHERE recap_id = ? AND user_id = ?", (recap_id, user_id)
    ).fetchone()
    return row is not None


def _record(conn: sqlite3.Connection, recap_id: str, user_id: str, message_ids: list[int]) -> None:
    """Record Telegram acceptance; this does not prove the student read it."""
    with conn:
        conn.execute(
            "INSERT INTO deliveries (recap_id, user_id, telegram_message_id, sent_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT (recap_id, user_id) DO UPDATE SET"
            " telegram_message_id = excluded.telegram_message_id, sent_at = excluded.sent_at",
            (recap_id, user_id, message_ids[0] if message_ids else None, utc_now()),
        )
