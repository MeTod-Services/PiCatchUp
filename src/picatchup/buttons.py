"""Use one Raspberry Pi GPIO button to start and stop recording.

The button is wired between a BCM GPIO pin and ground. gpiozero enables the
internal pull-up resistor, so no external resistor is needed.
"""

from __future__ import annotations

import signal
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any
from zoneinfo import ZoneInfo

from picatchup import audio
from picatchup.config import Settings
from picatchup.errors import PiCatchUpError, RaspberryPiError

Recorder = Callable[..., Path]
Output = Callable[[str], None]


class RecordingToggle:
    """Turn successive button presses into start, stop, start, stop, ..."""

    def __init__(
        self,
        settings: Settings,
        *,
        recorder: Recorder = audio.record,
        output: Output = print,
    ) -> None:
        self.settings = settings
        self._recorder = recorder
        self._output = output
        self._lock = Lock()
        self._stop_event: Event | None = None
        self._worker: Thread | None = None

    def press(self) -> None:
        """Start a recording, or request a clean stop if one is in progress."""
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                if self._stop_event is not None and not self._stop_event.is_set():
                    self._output("stopping recording...")
                    self._stop_event.set()
                return

            stop_event = Event()
            destination = self._destination()
            worker = Thread(
                target=self._record,
                args=(destination, stop_event),
                name="picatchup-recorder",
            )
            self._stop_event = stop_event
            self._worker = worker
            self._output(f"recording to {destination.name} — press the button to stop")
            worker.start()

    def stop_and_wait(self) -> None:
        """Finish an active recording before the listener exits."""
        with self._lock:
            stop_event = self._stop_event
            worker = self._worker
        if worker is not None and worker.is_alive():
            if stop_event is not None and not stop_event.is_set():
                self._output("stopping recording...")
                stop_event.set()
            worker.join()

    def _destination(self) -> Path:
        now = datetime.now(ZoneInfo(self.settings.timezone))
        stem = f"{now:%Y-%m-%d-%H%M%S}"
        destination = self.settings.inbox_dir / f"{stem}.mp3"
        number = 2
        while destination.exists():
            destination = self.settings.inbox_dir / f"{stem}-{number}.mp3"
            number += 1
        return destination

    def _record(self, destination: Path, stop_event: Event) -> None:
        try:
            self._recorder(
                destination,
                input_format=self.settings.audio_input_format,
                device=self.settings.audio_input_device,
                bitrate=self.settings.audio_bitrate,
                stop_event=stop_event,
            )
        except PiCatchUpError as error:
            self._output(f"error: {error}")
        except Exception as error:  # callbacks must not silently kill the listener
            self._output(f"unexpected recording error: {error}")
        else:
            size_mb = destination.stat().st_size / 1_000_000
            self._output(f"saved {destination} ({size_mb:.1f} MB)")


def listen(settings: Settings) -> None:
    """Listen forever for presses on the configured BCM GPIO pin."""
    try:
        from gpiozero import Button  # type: ignore[import-not-found]
    except ImportError:
        raise RaspberryPiError(
            "GPIO support is not installed. Run: pip install -e '.[rpi]'"
        ) from None

    controller = RecordingToggle(settings)
    try:
        button: Any = Button(
            settings.gpio_button_pin,
            pull_up=True,
            bounce_time=0.2,
        )
    except Exception as error:
        raise RaspberryPiError(
            f"could not open GPIO{settings.gpio_button_pin}: {error}"
        ) from None

    button.when_pressed = controller.press
    print(
        f"ready — button on GPIO{settings.gpio_button_pin} and GND; "
        "press once to record, again to stop"
    )
    shutdown = Event()
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}

    def request_shutdown(signum: int, frame: object) -> None:
        shutdown.set()

    for number in previous:
        signal.signal(number, request_shutdown)
    try:
        shutdown.wait()
    finally:
        button.close()
        controller.stop_and_wait()
        for number, handler in previous.items():
            signal.signal(number, handler)
