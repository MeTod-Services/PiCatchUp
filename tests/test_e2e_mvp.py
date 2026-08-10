"""Offline vertical slice: physical interaction through grounded Telegram text."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event

import pytest

from picatchup import audio, daily, deliver, qa, recap, transcribe, users
from picatchup.config import Settings
from picatchup.device import DeviceController
from picatchup.providers.groq_audio import Segment, Transcription
from picatchup.status import DeviceStatus, Status, StatusFile


class FakeLED:
    def __init__(self) -> None:
        self.statuses: list[Status] = []

    def show_status(self, status: Status) -> None:
        self.statuses.append(status)

    def close(self) -> None:
        pass


class FakeDisplay:
    def __init__(self) -> None:
        self.frames: list[DeviceStatus] = []

    def render(self, snapshot: DeviceStatus) -> None:
        self.frames.append(snapshot)

    def close(self) -> None:
        pass


def wait_until(check: Callable[[], bool], timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.005)
    assert check()


def test_button_to_delivery_and_grounded_question_offline(
    conn: sqlite3.Connection,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    led = FakeLED()
    display = FakeDisplay()
    recording_started = Event()

    def recorder(destination: Path, **options: object) -> Path:
        stop = options["stop_event"]
        assert isinstance(stop, Event)
        recording_started.set()
        assert stop.wait(timeout=1)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"synthetic classroom audio")
        return destination

    controller = DeviceController(
        settings,
        recorder=recorder,
        led=led,
        display=display,
        publisher=StatusFile(settings.status_file),
    )
    controller.start()
    controller.press()
    assert recording_started.wait(timeout=1)
    wait_until(lambda: controller.snapshot.status is Status.RECORDING)
    controller.press()
    wait_until(lambda: controller.snapshot.status is Status.SUCCESS)
    controller.close()

    [recording] = settings.inbox_dir.glob("*.mp3")
    lesson_date, _ = transcribe.read_name(recording)
    assert lesson_date is not None
    assert Status.RECORDING in led.statuses and Status.SUCCESS in led.statuses
    assert any(frame.status is Status.RECORDING for frame in display.frames)

    monkeypatch.setattr(audio, "ensure_ffmpeg", lambda: None)
    monkeypatch.setattr(audio, "duration_ms", lambda path: 30 * 60_000)
    monkeypatch.setattr(
        transcribe,
        "_groq",
        lambda config: lambda path: Transcription(
            text="Il professore spiega che la derivata misura la variazione istantanea.",
            segments=(
                Segment(
                    23 * 60_000,
                    24 * 60_000,
                    "La derivata misura la variazione istantanea.",
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        recap,
        "_groq",
        lambda config, model: lambda prompt: "📚 MATEMATICA\n• derivate e variazione",
    )
    sent: list[tuple[int, str]] = []

    def fake_telegram(config: Settings) -> deliver.Sender:
        def send(text: str, chat_id: int) -> list[int]:
            sent.append((chat_id, text))
            return [77]

        return send

    monkeypatch.setattr(deliver, "_telegram", fake_telegram)
    users.add(conn, 111, "Ada")

    report = daily.run(conn, settings, recap_date=lesson_date)

    assert len(report.transcribed) == 1
    assert sent == [(111, "📚 MATEMATICA\n• derivate e variazione")]
    assert conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 1

    answer = qa.answer_question(
        conn,
        settings,
        "Che cosa misura la derivata?",
        writer=lambda prompt: (
            "📖 Dalla lezione\nMisura la variazione istantanea.\n\n"
            "💡 Spiegazione aggiuntiva\nÈ collegata alla pendenza della tangente."
        ),
    )

    assert "📖 Dalla lezione" in answer.text
    assert "💡 Spiegazione aggiuntiva" in answer.text
    assert f"{lesson_date[8:10]}/{lesson_date[5:7]}, minuto 23" in answer.text
