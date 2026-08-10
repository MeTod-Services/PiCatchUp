"""Telegram: sending a message, and finding out who wrote to the bot.

The message loop of the bot (step 4 of the plan) is not here yet. This is the
half we need to deliver a recap: send text to a chat id.
"""

from __future__ import annotations

import logging
from typing import Any

from picatchup.errors import ProviderError
from picatchup.providers import _http

API = "https://api.telegram.org"

# Telegram refuses anything longer, so we split before it has to.
MAX_MESSAGE = 4096

log = logging.getLogger(__name__)


def send_message(text: str, *, token: str, chat_id: int, timeout: float = 30.0) -> list[int]:
    """Send text to one chat, split across messages if it is long.

    Returns the id of every message that arrived, in order.
    """
    ids: list[int] = []
    for part in split_message(text):
        answer = _http.post_json(
            f"{API}/bot{token}/sendMessage",
            {"chat_id": chat_id, "text": part, "disable_web_page_preview": True},
            timeout=timeout,
        )
        ids.append(_message_id(answer))
    log.info("sent %d message(s) to chat %d", len(ids), chat_id)
    return ids


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Cut text into Telegram-sized pieces, preferring clean breaks.

    Paragraph first, then line, then a hard cut. A recap arriving in two
    messages is fine; one arriving with a sentence chopped in half is not.
    """
    text = text.strip()
    if not text:
        return []

    parts: list[str] = []
    while len(text) > limit:
        window = text[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    parts.append(text)
    return [part for part in parts if part]


def recent_chats(*, token: str, timeout: float = 30.0) -> list[tuple[int, str]]:
    """Who has written to the bot lately: (telegram id, name), most recent last.

    This is how you find your own id without a bot loop: write to the bot, run
    `picatchup user ids`, read the number.
    """
    answer = _http.get_json(f"{API}/bot{token}/getUpdates", timeout=timeout)
    if not answer.get("ok"):
        reason = answer.get("description", "no reason given")
        raise ProviderError(f"Telegram refused getUpdates: {reason}")

    found: dict[int, str] = {}
    for update in answer.get("result") or []:
        chat = _chat_of(update)
        if chat is None:
            continue
        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            continue
        name = " ".join(
            str(chat.get(key)) for key in ("first_name", "last_name") if chat.get(key)
        )
        found[chat_id] = name or str(chat.get("username") or chat_id)
    return list(found.items())


def _chat_of(update: Any) -> dict[str, Any] | None:
    if not isinstance(update, dict):
        return None
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    # A group has a chat id too, and recaps belong to the class, not to a
    # group the bot happened to be added to.
    if not isinstance(chat, dict) or chat.get("type") != "private":
        return None
    return chat


def _message_id(answer: dict[str, Any]) -> int:
    result = answer.get("result")
    message_id = result.get("message_id") if isinstance(result, dict) else None
    if not isinstance(message_id, int):
        raise ProviderError("Telegram accepted the message but returned no id")
    return message_id
