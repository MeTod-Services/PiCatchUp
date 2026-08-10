"""The providers, tested against a web server running on this machine.

We write the HTTP by hand, so something has to check that what leaves the
program is a request Groq and Telegram would actually accept — and that a
refusal comes back as a sentence rather than a traceback. Nothing here reaches
the internet: the server is a thread on 127.0.0.1.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from picatchup.errors import ProviderError
from picatchup.providers import _http, groq_audio, groq_text, telegram


class Received:
    """What the fake server was sent, and what it should answer."""

    def __init__(self) -> None:
        self.path = ""
        self.headers: dict[str, str] = {}
        self.body = b""
        self.answers: list[tuple[int, object]] = []
        self.calls = 0


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[Received]:
    received = Received()

    class Handler(BaseHTTPRequestHandler):
        def _reply(self) -> None:
            received.path = self.path
            received.headers = dict(self.headers)
            length = int(self.headers.get("Content-Length") or 0)
            received.body = self.rfile.read(length)
            status, payload = received.answers[min(received.calls, len(received.answers) - 1)]
            received.calls += 1
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _reply
        do_POST = _reply

        def log_message(self, *args: object) -> None:
            """Quiet: the test output is not an access log."""

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    serving = {"poll_interval": 0.01}  # so tearing it down is instant
    threading.Thread(target=httpd.serve_forever, kwargs=serving, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    monkeypatch.setattr(groq_audio, "URL", f"{base}/audio")
    monkeypatch.setattr(groq_text, "URL", f"{base}/chat")
    monkeypatch.setattr(telegram, "API", base)
    monkeypatch.setattr(_http, "ATTEMPTS", 2)
    # The waits between retries are real seconds; nobody should sit through them.
    monkeypatch.setattr(_http, "time", SimpleNamespace(sleep=lambda seconds: None))

    yield received
    httpd.shutdown()
    httpd.server_close()


class TestGroqAudio:
    def test_uploads_the_file_and_reads_the_timings(
        self, server: Received, tmp_path: Path
    ) -> None:
        recording = tmp_path / "lezione.mp3"
        recording.write_bytes(b"the bytes of a lesson")
        server.answers = [
            (
                200,
                {
                    "text": "Oggi le derivate.",
                    "segments": [{"start": 1.5, "end": 4.25, "text": " Oggi le derivate."}],
                },
            )
        ]

        result = groq_audio.transcribe(recording, api_key="gsk_test", model="whisper-test")

        assert server.headers["Authorization"] == "Bearer gsk_test"
        assert b'name="model"\r\n\r\nwhisper-test' in server.body
        assert b'name="response_format"\r\n\r\nverbose_json' in server.body
        assert b'filename="lezione.mp3"' in server.body
        assert b"the bytes of a lesson" in server.body
        assert result.text == "Oggi le derivate."
        assert result.segments == (groq_audio.Segment(1_500, 4_250, "Oggi le derivate."),)

    def test_a_segment_missing_its_timings_is_dropped_not_fatal(self, server: Received) -> None:
        assert groq_audio._segments([{"text": "ciao"}, {"start": 0, "end": 1, "text": "vero"}]) == (
            groq_audio.Segment(0, 1000, "vero"),
        )

    def test_a_refused_key_reads_like_a_sentence(self, server: Received, tmp_path: Path) -> None:
        recording = tmp_path / "lezione.mp3"
        recording.write_bytes(b"audio")
        server.answers = [(401, {"error": {"message": "Invalid API Key"}})]

        with pytest.raises(ProviderError, match="answered 401: Invalid API Key"):
            groq_audio.transcribe(recording, api_key="wrong", model="whisper-test")


class TestGroqText:
    def test_sends_the_prompt_and_returns_the_answer(self, server: Received) -> None:
        server.answers = [(200, {"choices": [{"message": {"content": "  il riassunto  "}}]})]

        answer = groq_text.generate("riassumi questo", api_key="gsk_test", model="text-test")

        sent = json.loads(server.body)
        assert sent["model"] == "text-test"
        assert sent["messages"] == [{"role": "user", "content": "riassumi questo"}]
        assert answer == "il riassunto"

    def test_being_rate_limited_is_tried_again(self, server: Received) -> None:
        server.answers = [
            (429, {"error": {"message": "slow down"}}),
            (200, {"choices": [{"message": {"content": "il riassunto"}}]}),
        ]

        assert groq_text.generate("x", api_key="gsk_test", model="text-test") == "il riassunto"
        assert server.calls == 2

    def test_an_empty_answer_is_an_error_not_an_empty_recap(self, server: Received) -> None:
        server.answers = [(200, {"choices": [{"message": {"content": "   "}}]})]

        with pytest.raises(ProviderError, match="empty answer"):
            groq_text.generate("x", api_key="gsk_test", model="text-test")


class TestTelegram:
    def test_sends_the_text_to_the_chat(self, server: Received) -> None:
        server.answers = [(200, {"ok": True, "result": {"message_id": 77}})]

        ids = telegram.send_message("il riassunto", token="tok", chat_id=424242)

        assert server.path == "/bottok/sendMessage"
        assert json.loads(server.body)["chat_id"] == 424242
        assert ids == [77]

    def test_a_long_recap_arrives_as_several_messages(self, server: Received) -> None:
        server.answers = [(200, {"ok": True, "result": {"message_id": 1}})]

        ids = telegram.send_message("a" * 5000, token="tok", chat_id=1)

        assert len(ids) == 2 and server.calls == 2

    def test_being_blocked_says_so(self, server: Received) -> None:
        server.answers = [(403, {"ok": False, "description": "bot was blocked by the user"})]

        with pytest.raises(ProviderError, match="bot was blocked by the user"):
            telegram.send_message("ciao", token="tok", chat_id=1)

    def test_recent_chats_ignores_groups(self, server: Received) -> None:
        server.answers = [
            (
                200,
                {
                    "ok": True,
                    "result": [
                        {"message": {"chat": {"id": 111, "type": "private", "first_name": "Ada"}}},
                        {"message": {"chat": {"id": -99, "type": "group", "title": "3B"}}},
                    ],
                },
            )
        ]

        assert telegram.recent_chats(token="tok") == [(111, "Ada")]
