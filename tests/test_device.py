from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from threading import Event

import pytest

from picatchup.config import Settings
from picatchup.device import DeviceController
from picatchup.status import DeviceStatus, Status, StatusFile


class FakeLED:
    def __init__(self) -> None:
        self.statuses: list[Status] = []
        self.closed = False

    def show_status(self, status: Status) -> None:
        self.statuses.append(status)

    def close(self) -> None:
        self.closed = True


class FakeDisplay:
    def __init__(self) -> None:
        self.snapshots: list[DeviceStatus] = []
        self.closed = False

    def render(self, snapshot: DeviceStatus) -> None:
        self.snapshots.append(snapshot)

    def close(self) -> None:
        self.closed = True


def wait_until(check: Callable[[], bool], timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not check() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert check()


def test_button_workflow_is_async_and_updates_all_outputs(
    settings: Settings, tmp_path: Path
) -> None:
    recording_started = Event()
    led = FakeLED()
    display = FakeDisplay()
    status_file = StatusFile(tmp_path / "status.json")

    def recorder(destination: Path, **options: object) -> Path:
        stop = options["stop_event"]
        assert isinstance(stop, Event)
        recording_started.set()
        assert stop.wait(timeout=2)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"audio")
        return destination

    # Accelerating the monotonic clock keeps the elapsed-time test fast.  The
    # production interval remains one real second.
    controller = DeviceController(
        settings,
        recorder=recorder,
        led=led,
        display=display,
        publisher=status_file,
        monotonic=lambda: time.monotonic() * 20,
        elapsed_interval=0.05,
    )
    controller.start()
    assert [item.status for item in display.snapshots[:2]] == [Status.BOOTING, Status.READY]

    controller.press()
    assert recording_started.wait(timeout=1)
    wait_until(lambda: controller.snapshot.status is Status.RECORDING)
    wait_until(lambda: (controller.snapshot.elapsed_seconds or 0) >= 1)

    controller.press()
    wait_until(lambda: controller.snapshot.status in {Status.SAVING, Status.SUCCESS})
    wait_until(lambda: controller.snapshot.status is Status.SUCCESS)

    saved = list(settings.inbox_dir.glob("*.mp3"))
    assert len(saved) == 1
    published = status_file.read()
    assert published is not None
    assert published.status is Status.SUCCESS
    assert Status.RECORDING in led.statuses
    assert Status.SAVING in led.statuses
    assert led.statuses[-1] is Status.SUCCESS

    controller.close()
    assert led.closed
    assert display.closed


def test_success_returns_to_ready_after_visible_delay(settings: Settings) -> None:
    led = FakeLED()
    display = FakeDisplay()

    def recorder(destination: Path, **options: object) -> Path:
        stop = options["stop_event"]
        assert isinstance(stop, Event)
        assert stop.wait(timeout=1)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"audio")
        return destination

    controller = DeviceController(
        settings,
        recorder=recorder,
        led=led,
        display=display,
        publisher=StatusFile(settings.status_file),
        monotonic=lambda: time.monotonic() * 200,
    )
    controller.start()
    controller.press()
    wait_until(lambda: controller.snapshot.status is Status.RECORDING)
    controller.press()
    wait_until(lambda: controller.snapshot.status is Status.SUCCESS)
    wait_until(lambda: controller.snapshot.status is Status.READY)
    controller.close()


def test_external_pipeline_status_reaches_the_display(settings: Settings) -> None:
    led = FakeLED()
    display = FakeDisplay()
    status_file = StatusFile(settings.status_file)
    controller = DeviceController(
        settings,
        led=led,
        display=display,
        publisher=status_file,
        monotonic=lambda: time.monotonic() * 200,
    )
    controller.start()

    status_file.publish(DeviceStatus(Status.TRANSCRIBING, detail="1 / 4"))
    wait_until(lambda: controller.snapshot.status is Status.TRANSCRIBING, timeout=2.0)

    assert display.snapshots[-1].detail == "1 / 4"
    controller.close()


def test_missing_microphone_never_enters_recording_state(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    led = FakeLED()
    display = FakeDisplay()
    called = False

    def missing(input_format: str, device: str) -> str:
        raise OSError("no USB microphone")

    def recorder(destination: Path, **options: object) -> Path:
        nonlocal called
        called = True
        return destination

    monkeypatch.setattr("picatchup.device.audio.check_input_device", missing)
    controller = DeviceController(
        settings,
        recorder=recorder,
        led=led,
        display=display,
        publisher=StatusFile(settings.status_file),
    )
    controller.start()
    controller.press()
    wait_until(lambda: controller.snapshot.status is Status.ERROR)

    assert not called
    assert Status.RECORDING not in led.statuses
    controller.close()


def test_recording_failure_becomes_error_without_killing_controller(settings: Settings) -> None:
    led = FakeLED()
    display = FakeDisplay()

    def broken_recorder(destination: Path, **options: object) -> Path:
        raise OSError("microphone disconnected")

    controller = DeviceController(
        settings,
        recorder=broken_recorder,
        led=led,
        display=display,
        publisher=StatusFile(settings.status_file),
    )
    controller.start()
    controller.press()
    wait_until(lambda: controller.snapshot.status is Status.ERROR)

    assert controller.snapshot.detail == "Microfono non disponibile"
    assert led.statuses[-1] is Status.ERROR
    controller.close()


def test_close_during_recording_requests_a_clean_stop(settings: Settings) -> None:
    started = Event()
    stopped = Event()
    led = FakeLED()
    display = FakeDisplay()

    def recorder(destination: Path, **options: object) -> Path:
        stop = options["stop_event"]
        assert isinstance(stop, Event)
        started.set()
        assert stop.wait(timeout=2)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"complete")
        stopped.set()
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
    assert started.wait(timeout=1)

    controller.close()

    assert stopped.is_set()
    assert controller.wait(timeout=0)
    assert led.closed and display.closed


def test_status_file_round_trip_and_malformed_input_are_safe(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "device.json"
    publisher = StatusFile(path)
    expected = DeviceStatus(Status.SENDING, detail="daily recap")

    publisher.publish(expected)
    assert publisher.read() == expected
    assert not list(path.parent.glob("*.tmp"))

    path.write_text("not JSON", encoding="utf-8")
    assert publisher.read() is None
