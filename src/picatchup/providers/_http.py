"""The one place that opens a socket.

Two shapes of request cover everything we do: a JSON body, and a multipart
upload with one file in it. They live here so that groq_audio.py, groq_text.py
and telegram.py do not each grow their own copy of timeouts, retries and error
messages.

Only the standard library. The project has no dependencies and stays that way
as long as this file is a hundred lines.
"""

from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping
from typing import Any

from picatchup.errors import ProviderError

ATTEMPTS = 3
# Rate limits and hiccups. Anything else (401, 400) will not improve by asking
# again, so we fail immediately and say what the server said.
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


def get_json(
    url: str,
    params: Mapping[str, str | int] | None = None,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    return _send(urllib.request.Request(url, method="GET"), timeout=timeout)


def post_json(
    url: str,
    payload: Mapping[str, Any],
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", "PiCatchUp/1.0")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    return _send(request, timeout=timeout)


def post_file(
    url: str,
    *,
    fields: Mapping[str, str],
    field_name: str,
    filename: str,
    content: bytes,
    headers: Mapping[str, str] | None = None,
    timeout: float = 600.0,
) -> dict[str, Any]:
    """POST a multipart/form-data body: some plain fields plus one file."""
    body, content_type = _multipart(fields, field_name, filename, content)
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", content_type)
    request.add_header("User-Agent", "PiCatchUp/1.0")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    return _send(request, timeout=timeout)


def _multipart(
    fields: Mapping[str, str], field_name: str, filename: str, content: bytes
) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{field_name}";'
        f' filename="{filename}"\r\nContent-Type: {mime_type}\r\n\r\n'.encode()
    )
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _send(request: urllib.request.Request, *, timeout: float) -> dict[str, Any]:
    host = urllib.parse.urlsplit(request.full_url).hostname or request.full_url

    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            if error.code in RETRY_STATUSES and attempt < ATTEMPTS:
                error.close()
                time.sleep(2**attempt)
                continue
            # Provider bodies are untrusted and can echo submitted lesson or
            # question text. Status + host are actionable without logging body.
            error.close()
            raise ProviderError(f"{host} answered HTTP {error.code}") from None
        except urllib.error.URLError as error:
            if attempt < ATTEMPTS:
                time.sleep(2**attempt)
                continue
            raise ProviderError(f"cannot reach {host}: {error.reason}") from None

        try:
            answer: Any = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderError(f"{host} did not answer with JSON") from None
        if not isinstance(answer, dict):
            raise ProviderError(f"{host} answered with {type(answer).__name__}, expected an object")
        return answer

    raise AssertionError("the retry loop always returns or raises")
