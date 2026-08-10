from __future__ import annotations

from pathlib import Path
from threading import Event

from picatchup.buttons import RecordingToggle
from picatchup.config import Settings, load_settings


def settings_in(tmp_path: Path) -> Settings:
    env = tmp_path / ".env"
    env.write_text(f"DATA_DIR={tmp_path / 'data'}\n", encoding="utf-8")
    return load_settings(env)


def test_one_button_starts_and_stops_a_recording(tmp_path: Path) -> None:
    settings = settings_in(tmp_path)
    started = Event()
    messages: list[str] = []

    def fake_record(destination: Path, **options: object) -> Path:
        stop_event = options["stop_event"]
        assert isinstance(stop_event, Event)
        started.set()
        assert stop_event.wait(timeout=1)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"recorded")
        return destination

    controller = RecordingToggle(settings, recorder=fake_record, output=messages.append)

    controller.press()
    assert started.wait(timeout=1)
    controller.press()
    controller.stop_and_wait()

    recordings = list(settings.inbox_dir.glob("*.mp3"))
    assert len(recordings) == 1
    assert recordings[0].read_bytes() == b"recorded"
    assert any(message.startswith("recording to ") for message in messages)
    assert any(message.startswith("saved ") for message in messages)


def test_recording_error_does_not_crash_the_button_listener(tmp_path: Path) -> None:
    settings = settings_in(tmp_path)
    failed = Event()
    messages: list[str] = []

    def broken_record(destination: Path, **options: object) -> Path:
        failed.set()
        raise RuntimeError("microphone disconnected")

    controller = RecordingToggle(settings, recorder=broken_record, output=messages.append)

    controller.press()
    assert failed.wait(timeout=1)
    controller.stop_and_wait()

    assert "unexpected recording error: microphone disconnected" in messages
