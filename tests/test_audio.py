from __future__ import annotations

import io
import signal
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from picatchup import audio
from picatchup.errors import AudioError


class FakeProcess:
    def __init__(self, destination: Path, *, returncode: int, stderr: bytes = b"") -> None:
        destination.write_bytes(b"partial audio")
        self.returncode: int | None = returncode
        self.stderr = io.BytesIO(stderr)
        self.signals: list[int] = []

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        assert self.returncode is not None
        return self.returncode

    def send_signal(self, signum: int) -> None:
        self.signals.append(signum)
        self.returncode = 255

    def kill(self) -> None:
        self.returncode = -9


def test_record_checks_ffmpeg_return_code_and_removes_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "failed.mp3"
    monkeypatch.setattr(audio, "ensure_ffmpeg", lambda: None)
    monkeypatch.setattr(audio, "check_input_device", lambda input_format, device: device)

    def popen(command: list[str], **options: Any) -> FakeProcess:
        options["stderr"].write(b"Device or resource busy")
        return FakeProcess(Path(command[-1]), returncode=1)

    monkeypatch.setattr(audio.subprocess, "Popen", popen)

    with pytest.raises(AudioError, match="Device or resource busy"):
        audio.record(destination, input_format="alsa", device="hw:1,0")

    assert not destination.exists()


def test_record_validates_successful_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "lesson.mp3"
    monkeypatch.setattr(audio, "ensure_ffmpeg", lambda: None)
    monkeypatch.setattr(audio, "check_input_device", lambda input_format, device: device)
    validated: list[Path] = []
    monkeypatch.setattr(audio, "validate_audio", lambda path: validated.append(path) or 1000)

    def popen(command: list[str], **options: Any) -> FakeProcess:
        return FakeProcess(Path(command[-1]), returncode=0)

    monkeypatch.setattr(audio.subprocess, "Popen", popen)

    assert audio.record(destination, input_format="alsa", device="hw:1,0") == destination
    assert len(validated) == 1 and validated[0].name.endswith(".part")
    assert destination.is_file()
    assert not list(tmp_path.glob("*.part"))


def test_button_stop_sends_sigint_and_keeps_valid_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "lesson.mp3"
    monkeypatch.setattr(audio, "ensure_ffmpeg", lambda: None)
    monkeypatch.setattr(audio, "check_input_device", lambda input_format, device: device)
    monkeypatch.setattr(audio, "validate_audio", lambda path: 1000)
    process: FakeProcess | None = None

    def popen(command: list[str], **options: Any) -> FakeProcess:
        nonlocal process
        assert Path(command[-1]).name.endswith(".part")
        assert not destination.exists(), "active audio must not be discoverable in the inbox"
        process = FakeProcess(Path(command[-1]), returncode=0)
        process.returncode = None
        return process

    monkeypatch.setattr(audio.subprocess, "Popen", popen)
    stop = Event()
    stop.set()

    audio.record(destination, input_format="alsa", device="hw:1,0", stop_event=stop)

    assert process is not None and process.signals == [signal.SIGINT]


def test_alsa_auto_rejects_ambiguous_microphones(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio, "alsa_capture_devices", lambda: ["hw:1,0", "hw:2,0"])

    with pytest.raises(AudioError, match="multiple ALSA capture devices"):
        audio.resolve_input_device("alsa", "auto")


def test_explicit_alsa_device_must_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio, "alsa_capture_devices", lambda: ["hw:1,0"])

    with pytest.raises(AudioError, match="was not found"):
        audio.check_input_device("alsa", "hw:2,0")


def test_conversion_removes_an_invalid_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.wav"
    destination = tmp_path / "result.mp3"
    source.write_bytes(b"source")
    monkeypatch.setattr(audio, "ensure_ffmpeg", lambda: None)

    def fake_run(command: list[str]) -> str:
        destination.write_bytes(b"not audio")
        return ""

    monkeypatch.setattr(audio, "_run", fake_run)
    monkeypatch.setattr(
        audio, "validate_audio", lambda path: (_ for _ in ()).throw(AudioError("invalid audio"))
    )

    with pytest.raises(AudioError, match="invalid audio"):
        audio.to_mp3(source, destination)
    assert not destination.exists()
