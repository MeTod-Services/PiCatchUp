"""Allowlisted Telegram bot orchestration.

HTTP stays in ``providers.telegram``. This module only polls domain ``Update``
values, checks access in SQLite, routes commands, and calls injected functions.
Message and question text is deliberately never logged.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Protocol

from picatchup import qa
from picatchup.config import Settings, require
from picatchup.domain import Update
from picatchup.errors import PiCatchUpError, ProviderError
from picatchup.providers import telegram

POLL_TIMEOUT = 30
MAX_POLL_TIMEOUT = 50
RETRY_DELAY = 2.0

UNAUTHORIZED_TEXT = "Questo servizio è disponibile solo per gli utenti autorizzati."
GROUP_TEXT = "Per proteggere la privacy, questo bot funziona solo nelle chat private."
START_TEXT = (
    "Ciao! Sono PiCatchUp. Posso mostrarti l'ultimo riassunto e rispondere "
    "alle domande sulle lezioni registrate. Scrivi /aiuto per vedere i comandi."
)
HELP_TEXT = """Comandi disponibili:
/start — presenta PiCatchUp
/aiuto (o /help) — mostra questo aiuto
/privacy — spiega come vengono protetti i dati
/riassunto — mostra il riassunto più recente

Puoi anche scrivere una domanda sulla lezione."""
PRIVACY_TEXT = (
    "PiCatchUp usa il tuo ID Telegram solo per verificare che tu sia autorizzato. "
    "I contenuti delle lezioni e i riassunti sono condivisi soltanto con gli utenti "
    "abilitati. Il bot non accetta gruppi. Per revocare l'accesso o chiedere la "
    "rimozione dei tuoi dati, contatta chi gestisce PiCatchUp."
)
RECAP_UNAVAILABLE_TEXT = "Al momento non è disponibile alcun riassunto."
ANSWER_UNAVAILABLE_TEXT = "Al momento non riesco a rispondere. Riprova più tardi."

GetUpdates = Callable[[int, int], list[Update]]
Sender = Callable[[str, int], list[int]]
Leaver = Callable[[int], None]
Sleeper = Callable[[float], None]
Answerer = Callable[[sqlite3.Connection, Settings, str], str]

log = logging.getLogger(__name__)


class StopEvent(Protocol):
    """The small part of ``threading.Event`` needed by the polling loop."""

    def is_set(self) -> bool: ...


def run(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    get_updates: GetUpdates | None = None,
    sender: Sender | None = None,
    leaver: Leaver | None = None,
    sleeper: Sleeper = time.sleep,
    answerer: Answerer | None = None,
    stop_event: StopEvent | None = None,
    poll_timeout: int = POLL_TIMEOUT,
    retry_delay: float = RETRY_DELAY,
) -> None:
    """Long-poll until ``stop_event`` is set.

    Provider failures are logged without message content, followed by a delay,
    and then polling resumes. Dependencies are injectable so tests never need
    internet access or real sleeps.
    """
    if not 1 <= poll_timeout <= MAX_POLL_TIMEOUT:
        raise ValueError(f"poll_timeout must be between 1 and {MAX_POLL_TIMEOUT} seconds")
    if retry_delay <= 0:
        raise ValueError("retry_delay must be greater than zero")

    if get_updates is None or sender is None or leaver is None:
        token = require(settings.telegram_bot_token, "TELEGRAM_BOT_TOKEN")
        get_updates = get_updates or _telegram_getter(token)
        sender = sender or _telegram_sender(token)
        leaver = leaver or _telegram_leaver(token)

    answerer = answerer or _default_answerer
    stop_event = stop_event or threading.Event()
    offset = _load_offset(conn)

    while not stop_event.is_set():
        try:
            updates = get_updates(offset, poll_timeout)
        except ProviderError as error:
            log.warning("Telegram polling failed: %s", error)
            sleeper(retry_delay)
            continue

        if not updates:
            # A real long poll has already waited, but this also prevents a
            # misbehaving endpoint from turning empty responses into a busy loop.
            sleeper(retry_delay)
            continue

        for update in sorted(updates, key=lambda item: item.update_id):
            if update.update_id < offset:
                continue
            try:
                handle_update(conn, settings, update, sender, leaver, answerer)
            except ProviderError as error:
                # Do not acknowledge this update or any later one in the batch.
                log.warning("Telegram update %d failed: %s", update.update_id, error)
                sleeper(retry_delay)
                break
            offset = update.update_id + 1
            _save_offset(conn, offset)


def handle_update(
    conn: sqlite3.Connection,
    settings: Settings,
    update: Update,
    sender: Sender,
    leaver: Leaver,
    answerer: Answerer,
) -> None:
    """Authorize and route one provider-neutral update."""
    if update.chat_type is None or update.chat_id is None:
        log.info("ignored malformed Telegram update %d", update.update_id)
        return

    if update.chat_type != "private":
        try:
            sender(GROUP_TEXT, update.chat_id)
        except ProviderError as error:
            # The privacy control is leaving; inability to post a courtesy
            # refusal must not make a successful leave replay forever.
            log.warning("could not send group refusal for update %d: %s", update.update_id, error)
        leaver(update.chat_id)
        return

    if not _is_authorized(conn, update.telegram_user_id):
        log.info("unauthorized Telegram user_id=%s", update.telegram_user_id)
        sender(UNAUTHORIZED_TEXT, update.chat_id)
        return

    text = (update.text or "").strip()
    if not text:
        return

    command = _command(text)
    if command == "/start":
        sender(START_TEXT, update.chat_id)
    elif command in {"/aiuto", "/help"}:
        sender(HELP_TEXT, update.chat_id)
    elif command == "/privacy":
        sender(PRIVACY_TEXT, update.chat_id)
    elif command == "/riassunto":
        sender(_latest_recap(conn), update.chat_id)
    elif command is not None:
        sender(HELP_TEXT, update.chat_id)
    else:
        try:
            answer = answerer(conn, settings, text)
        except PiCatchUpError as error:
            log.warning("could not answer Telegram update %d: %s", update.update_id, error)
            answer = ANSWER_UNAVAILABLE_TEXT
        sender(answer, update.chat_id)


def _load_offset(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT next_update_id FROM bot_state WHERE id = 1").fetchone()
    return int(row[0]) if row is not None else 0


def _save_offset(conn: sqlite3.Connection, offset: int) -> None:
    with conn:
        conn.execute(
            "INSERT INTO bot_state (id, next_update_id) VALUES (1, ?)"
            " ON CONFLICT (id) DO UPDATE SET next_update_id = excluded.next_update_id",
            (offset,),
        )


def _is_authorized(conn: sqlite3.Connection, telegram_user_id: int | None) -> bool:
    if telegram_user_id is None:
        return False
    row = conn.execute(
        "SELECT 1 FROM users WHERE telegram_user_id = ? AND is_active = 1",
        (telegram_user_id,),
    ).fetchone()
    return row is not None


def _latest_recap(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT text FROM recaps ORDER BY recap_date DESC, created_at DESC LIMIT 1"
    ).fetchone()
    return str(row[0]) if row is not None else RECAP_UNAVAILABLE_TEXT


def _command(text: str) -> str | None:
    first = text.split(maxsplit=1)[0]
    if not first.startswith("/"):
        return None
    # Telegram may deliver a command addressed as /aiuto@bot_name.
    return first.partition("@")[0].casefold()


def _telegram_getter(token: str) -> GetUpdates:
    def receive(offset: int, timeout: int) -> list[Update]:
        return telegram.get_updates(token, offset, timeout)

    return receive


def _telegram_sender(token: str) -> Sender:
    def send(text: str, chat_id: int) -> list[int]:
        return telegram.send_message(text, token=token, chat_id=chat_id)

    return send


def _telegram_leaver(token: str) -> Leaver:
    def leave(chat_id: int) -> None:
        telegram.leave_chat(token, chat_id)

    return leave


def _default_answerer(conn: sqlite3.Connection, settings: Settings, text: str) -> str:
    return qa.answer_question(conn, settings, text).text
