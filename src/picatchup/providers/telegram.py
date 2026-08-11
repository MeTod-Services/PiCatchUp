"""Telegram HTTP boundary: long polling, sending messages, and leaving chats.

Raw API payloads are converted to domain values here so bot orchestration never
handles HTTP or Telegram-specific dictionaries.
"""

from __future__ import annotations

import logging
from typing import Any

from picatchup.domain import Update
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
        _require_ok(answer, "sendMessage")
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


def get_updates(token: str, offset: int, timeout: int) -> list[Update]:
    """Long-poll Telegram and flatten valid events into provider-neutral values.

    Events with an id but no usable message are retained. The bot can then
    advance past them instead of receiving the same unsupported event forever.
    """
    answer = _http.get_json(
        f"{API}/bot{token}/getUpdates",
        {"offset": offset, "timeout": timeout},
        # The socket must live slightly longer than Telegram's long poll.
        timeout=float(timeout) + 5.0,
    )
    _require_ok(answer, "getUpdates")

    result = answer.get("result")
    if result is None:
        return []
    if not isinstance(result, list):
        raise ProviderError("Telegram returned malformed updates")

    updates: list[Update] = []
    for raw in result:
        update = _update_of(raw)
        if update is not None:
            updates.append(update)
    return updates


def leave_chat(token: str, chat_id: int, timeout: float = 30.0) -> None:
    """Remove the bot from a group or channel."""
    answer = _http.post_json(f"{API}/bot{token}/leaveChat", {"chat_id": chat_id}, timeout=timeout)
    _require_ok(answer, "leaveChat")
    log.info("left non-private chat %d", chat_id)


def recent_chats(*, token: str, timeout: float = 30.0) -> list[tuple[int, str]]:
    """Who has written to the bot lately: (telegram id, name), most recent last.

    This is how you find your own id without a bot loop: write to the bot, run
    `picatchup user ids`, read the number.
    """
    answer = _http.get_json(f"{API}/bot{token}/getUpdates", timeout=timeout)
    _require_ok(answer, "getUpdates")

    found: dict[int, str] = {}
    for update in answer.get("result") or []:
        chat = _chat_of(update)
        if chat is None:
            continue
        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            continue
        name = " ".join(str(chat.get(key)) for key in ("first_name", "last_name") if chat.get(key))
        found[chat_id] = name or str(chat.get("username") or chat_id)
    return list(found.items())


def _update_of(raw: object) -> Update | None:
    if not isinstance(raw, dict):
        return None
    update_id = _integer(raw.get("update_id"))
    if update_id is None:
        return None

    message = _message_of(raw)
    if message is None:
        membership = raw.get("my_chat_member")
        if isinstance(membership, dict):
            chat = membership.get("chat")
            sender = membership.get("from")
            return Update(
                update_id=update_id,
                telegram_user_id=(
                    _integer(sender.get("id")) if isinstance(sender, dict) else None
                ),
                chat_id=_integer(chat.get("id")) if isinstance(chat, dict) else None,
                chat_type=_string(chat.get("type")) if isinstance(chat, dict) else None,
                message_id=None,
                text=None,
            )
        return Update(update_id, None, None, None, None, None)

    sender = message.get("from")
    chat = message.get("chat")
    text = message.get("text")
    return Update(
        update_id=update_id,
        telegram_user_id=_integer(sender.get("id")) if isinstance(sender, dict) else None,
        chat_id=_integer(chat.get("id")) if isinstance(chat, dict) else None,
        chat_type=_string(chat.get("type")) if isinstance(chat, dict) else None,
        message_id=_integer(message.get("message_id")),
        text=text if isinstance(text, str) else None,
    )


def _message_of(update: dict[Any, Any]) -> dict[Any, Any] | None:
    message = update.get("message")
    if not isinstance(message, dict):
        message = update.get("edited_message")
    return message if isinstance(message, dict) else None


def _chat_of(update: Any) -> dict[str, Any] | None:
    if not isinstance(update, dict):
        return None
    message = _message_of(update)
    if message is None:
        return None
    chat = message.get("chat")
    # A group has a chat id too, and recaps belong to the class, not to a
    # group the bot happened to be added to.
    if not isinstance(chat, dict) or chat.get("type") != "private":
        return None
    return chat


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _require_ok(answer: dict[str, Any], operation: str) -> None:
    if answer.get("ok") is True:
        return
    # Descriptions are untrusted upstream text and are not needed in journals.
    raise ProviderError(f"Telegram refused {operation}")


def _message_id(answer: dict[str, Any]) -> int:
    result = answer.get("result")
    message_id = result.get("message_id") if isinstance(result, dict) else None
    if not isinstance(message_id, int):
        raise ProviderError("Telegram accepted the message but returned no id")
    return message_id


def send_document(
    path,
    *,
    token: str,
    chat_id: int,
    caption: str | None = None,
    timeout: float = 60.0,
) -> int:
    """Send one local document to a Telegram private chat."""

    import json
    import uuid
    from pathlib import Path
    from urllib import request as urllib_request

    file_path = Path(path)

    if not file_path.is_file():
        raise ProviderError(f"document does not exist: {file_path.name}")

    boundary = f"----PiCatchUp{uuid.uuid4().hex}"
    body = bytearray()

    def field(name: str, value: str) -> None:
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        )
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")

    field("chat_id", str(chat_id))

    if caption:
        field("caption", caption)

    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        (
            'Content-Disposition: form-data; name="document"; '
            f'filename="{file_path.name}"\r\n'
        ).encode()
    )
    body.extend(b"Content-Type: application/pdf\r\n\r\n")
    body.extend(file_path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    req = urllib_request.Request(
        f"{API}/bot{token}/sendDocument",
        data=bytes(body),
        method="POST",
    )
    req.add_header(
        "Content-Type",
        f"multipart/form-data; boundary={boundary}",
    )
    req.add_header("User-Agent", "PiCatchUp/1.0")

    try:
        with urllib_request.urlopen(req, timeout=timeout) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except Exception:
        raise ProviderError("Telegram document upload failed") from None

    _require_ok(answer, "sendDocument")
    return _message_id(answer)
