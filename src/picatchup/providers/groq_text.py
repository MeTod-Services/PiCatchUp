"""Groq's text model: a prompt in, a piece of writing out.

One function. Whoever calls it builds the prompt and decides what to do with
the answer; this file only knows how to ask.
"""

from __future__ import annotations

import logging
from typing import Any

from picatchup.errors import ProviderError
from picatchup.providers import _http

URL = "https://api.groq.com/openai/v1/chat/completions"

log = logging.getLogger(__name__)


def generate(
    prompt: str,
    *,
    api_key: str,
    model: str,
    system: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4000,
    timeout: float = 180.0,
) -> str:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    log.info("asking %s for %d characters of prompt", model, len(prompt))
    answer = _http.post_json(
        URL,
        {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )
    return _first_message(answer)


def _first_message(answer: dict[str, Any]) -> str:
    choices = answer.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError("Groq returned no answer")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ProviderError("Groq returned an empty answer")
    return content.strip()
