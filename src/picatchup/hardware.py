"""Hardware adapters for the button and status LED.

Imports from gpiozero happen only while opening real hardware.  The controller
itself depends on a tiny backend protocol and is therefore fully testable on a
development machine.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import StrEnum
from threading import Event, Lock, Thread, current_thread
from typing import Any, Protocol, cast

from picatchup.config import Settings
from picatchup.status import Status

log = logging.getLogger(__name__)


class LEDBackend(Protocol):
    def on(self) -> None: ...

    def off(self) -> None: ...

    def close(self) -> None: ...


class ButtonHandle(Protocol):
    def close(self) -> None: ...


class NoOpLEDBackend:
    def on(self) -> None:
        pass

    def off(self) -> None:
        pass

    def close(self) -> None:
        pass


class GPIOZeroLEDBackend:
    """Small wrapper that keeps gpiozero types out of the rest of the module."""

    def __init__(self, led: Any) -> None:
        self._led = led

    @classmethod
    def open(cls, pin: int) -> GPIOZeroLEDBackend:
        from gpiozero import LED  # type: ignore[import-not-found]

        return cls(LED(pin))

    def on(self) -> None:
        self._led.on()

    def off(self) -> None:
        self._led.off()

    def close(self) -> None:
        self._led.close()


class LEDState(StrEnum):
    IDLE = "idle"
    RECORDING = "recording"
    SUCCESS = "success"
    FATAL = "fatal"
    CLOSED = "closed"


Waiter = Callable[[Event, float], bool]


class LEDController:
    """Drive one LED with cancellable, non-blocking status patterns."""

    RECORDING_INTERVAL = 0.25
    SUCCESS_INTERVAL = 0.12
    FATAL_RAPID_INTERVAL = 0.08
    FATAL_SLOW_INTERVAL = 0.40
    FATAL_RAPID_FLASHES = 6
    FATAL_SLOW_FLASHES = 3

    def __init__(
        self,
        backend: LEDBackend,
        *,
        waiter: Waiter | None = None,
        recording_interval: float = RECORDING_INTERVAL,
    ) -> None:
        self._backend = backend
        self._wait = waiter or (lambda stop, seconds: stop.wait(seconds))
        self._recording_interval = recording_interval
        self._lock = Lock()
        self._stop = Event()
        self._worker: Thread | None = None
        self._state = LEDState.IDLE
        self._backend_failed = False
        self._safe_off()

    @property
    def state(self) -> LEDState:
        with self._lock:
            return self._state

    def show_status(self, status: Status) -> None:
        if status is Status.RECORDING:
            self.recording()
        elif status is Status.SAVING:
            # ffmpeg may still be flushing/finalizing after the stop press.
            # Keep the recording indication until RECORDING_DONE arrives.
            return
        elif status is Status.SUCCESS:
            self.success()
        elif status is Status.ERROR:
            self.fatal()
        else:
            self.idle()

    def idle(self) -> None:
        self._replace(LEDState.IDLE, None)

    def recording(self) -> None:
        self._replace(LEDState.RECORDING, self._recording_pattern)

    def success(self) -> None:
        self._replace(LEDState.SUCCESS, self._success_pattern)

    def fatal(self) -> None:
        self._replace(LEDState.FATAL, self._fatal_pattern)

    # Explicit names read well at call sites and preserve the short forms above.
    set_idle = idle
    set_recording = recording
    set_success = success
    set_fatal = fatal

    def wait_for_pattern(self, timeout: float | None = None) -> bool:
        with self._lock:
            worker = self._worker
        if worker is None:
            return True
        worker.join(timeout)
        return not worker.is_alive()

    def close(self) -> None:
        with self._lock:
            if self._state is LEDState.CLOSED:
                return
            self._state = LEDState.CLOSED
            stop = self._stop
            worker = self._worker
            self._worker = None
            stop.set()
        if worker is not None and worker is not current_thread():
            worker.join(timeout=1.0)
        try:
            self._backend.off()
        except Exception as error:
            log.warning("could not turn status LED off during shutdown: %s", error)
        try:
            self._backend.close()
        except Exception as error:
            log.warning("could not close status LED: %s", error)

    def _replace(
        self,
        state: LEDState,
        target: Callable[[Event], None] | None,
    ) -> None:
        with self._lock:
            if self._state is LEDState.CLOSED:
                return
            if self._state is state and self._worker is not None and self._worker.is_alive():
                return
            self._stop.set()
            stop = Event()
            self._stop = stop
            self._state = state
            self._worker = None
            self._safe_off()
            if target is not None:
                worker = Thread(
                    target=target,
                    args=(stop,),
                    name=f"picatchup-led-{state.value}",
                    daemon=True,
                )
                self._worker = worker
                worker.start()

    def _recording_pattern(self, stop: Event) -> None:
        try:
            while not stop.is_set():
                if not self._pattern_on(stop):
                    break
                if self._wait(stop, self._recording_interval):
                    break
                if not self._pattern_off(stop):
                    break
                if self._wait(stop, self._recording_interval):
                    break
        finally:
            self._finish_pattern(stop)

    def _success_pattern(self, stop: Event) -> None:
        self._finite_flashes(stop, self.SUCCESS_INTERVAL, 3)
        self._finish_pattern(stop)

    def _fatal_pattern(self, stop: Event) -> None:
        self._finite_flashes(stop, self.FATAL_RAPID_INTERVAL, self.FATAL_RAPID_FLASHES)
        if not stop.is_set():
            self._finite_flashes(stop, self.FATAL_SLOW_INTERVAL, self.FATAL_SLOW_FLASHES)
        self._finish_pattern(stop)

    def _finite_flashes(self, stop: Event, interval: float, count: int) -> None:
        for _ in range(count):
            if not self._pattern_on(stop):
                return
            if self._wait(stop, interval):
                return
            if not self._pattern_off(stop):
                return
            if self._wait(stop, interval):
                return

    def _pattern_on(self, stop: Event) -> bool:
        with self._lock:
            if self._stop is not stop or stop.is_set():
                return False
            self._safe_on()
            return True

    def _pattern_off(self, stop: Event) -> bool:
        with self._lock:
            if self._stop is not stop:
                return False
            self._safe_off()
            return True

    def _finish_pattern(self, stop: Event) -> None:
        self._pattern_off(stop)

    def _safe_on(self) -> None:
        if self._backend_failed:
            return
        try:
            self._backend.on()
        except Exception as error:
            self._backend_failed = True
            log.warning("could not turn status LED on: %s", error)

    def _safe_off(self) -> None:
        if self._backend_failed:
            return
        try:
            self._backend.off()
        except Exception as error:
            self._backend_failed = True
            log.warning("could not turn status LED off: %s", error)


def create_led(settings: Settings) -> LEDController:
    if not settings.gpio_enabled:
        return LEDController(NoOpLEDBackend())
    try:
        backend: LEDBackend = GPIOZeroLEDBackend.open(settings.gpio_led_pin)
    except Exception as error:
        log.warning("status LED unavailable on GPIO%d: %s", settings.gpio_led_pin, error)
        backend = NoOpLEDBackend()
    return LEDController(backend)


def open_button(pin: int, callback: Callable[[], None]) -> ButtonHandle:
    """Open one pull-up button without exposing gpiozero to callers."""
    from gpiozero import Button

    button: Any = Button(pin, pull_up=True, bounce_time=0.2)
    button.when_pressed = callback
    return cast(ButtonHandle, button)
