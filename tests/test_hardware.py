from __future__ import annotations

import time
from collections.abc import Callable
from threading import Event

from picatchup.hardware import LEDController, LEDState
from picatchup.status import Status


class FakeLED:
    def __init__(self) -> None:
        self.values: list[bool] = []
        self.closed = False

    def on(self) -> None:
        self.values.append(True)

    def off(self) -> None:
        self.values.append(False)

    def close(self) -> None:
        self.closed = True


def wait_until(check: Callable[[], bool], timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not check() and time.monotonic() < deadline:
        time.sleep(0.002)
    assert check()


def test_recording_blinks_until_idle_and_close_always_turns_led_off() -> None:
    backend = FakeLED()
    led = LEDController(backend, recording_interval=0.005)

    led.recording()
    wait_until(lambda: len(backend.values) >= 5)
    led.idle()
    count_after_idle = len(backend.values)
    time.sleep(0.02)

    assert led.state is LEDState.IDLE
    assert True in backend.values
    assert backend.values[-1] is False
    assert len(backend.values) == count_after_idle

    led.close()
    assert led.state is LEDState.CLOSED
    assert backend.values[-1] is False
    assert backend.closed


def test_saving_keeps_recording_blink_active_until_finalized() -> None:
    backend = FakeLED()
    led = LEDController(backend, recording_interval=0.005)

    led.recording()
    wait_until(lambda: len(backend.values) >= 3)
    led.show_status(Status.SAVING)

    assert led.state is LEDState.RECORDING
    led.success()
    assert led.wait_for_pattern(timeout=1)
    led.close()


def test_success_is_exactly_three_short_flashes() -> None:
    backend = FakeLED()
    delays: list[float] = []

    def no_delay(stop: Event, seconds: float) -> bool:
        delays.append(seconds)
        return stop.is_set()

    led = LEDController(backend, waiter=no_delay)
    baseline = len(backend.values)
    led.success()
    assert led.wait_for_pattern(timeout=1)

    transitions = backend.values[baseline:]
    assert transitions.count(True) == 3
    assert transitions[-1] is False
    assert delays == [LEDController.SUCCESS_INTERVAL] * 6
    led.close()


def test_fatal_pattern_has_a_bounded_rapid_then_slow_sequence() -> None:
    backend = FakeLED()
    delays: list[float] = []

    def no_delay(stop: Event, seconds: float) -> bool:
        delays.append(seconds)
        return stop.is_set()

    led = LEDController(backend, waiter=no_delay)
    led.fatal()
    assert led.wait_for_pattern(timeout=1)

    rapid_steps = LEDController.FATAL_RAPID_FLASHES * 2
    slow_steps = LEDController.FATAL_SLOW_FLASHES * 2
    assert delays == (
        [LEDController.FATAL_RAPID_INTERVAL] * rapid_steps
        + [LEDController.FATAL_SLOW_INTERVAL] * slow_steps
    )
    assert backend.values[-1] is False
    led.close()


def test_backend_failures_do_not_escape_the_controller() -> None:
    class BrokenLED:
        def on(self) -> None:
            raise OSError("unplugged")

        def off(self) -> None:
            raise OSError("unplugged")

        def close(self) -> None:
            raise OSError("unplugged")

    led = LEDController(BrokenLED(), waiter=lambda _stop, _seconds: False)
    led.success()
    assert led.wait_for_pattern(timeout=1)
    led.close()
