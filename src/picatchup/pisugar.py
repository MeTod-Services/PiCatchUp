from __future__ import annotations

import logging
import time
from collections.abc import Callable
from threading import Event, Thread

log = logging.getLogger(__name__)


class PiSugarButton:
    def __init__(
        self,
        callback: Callable[[], None],
        *,
        bus_number: int = 1,
        address: int = 0x57,
        max_click_seconds: float = 1.0,
    ) -> None:
        self._callback = callback
        self._bus_number = bus_number
        self._address = address
        self._max_click_seconds = max_click_seconds

        self._stop = Event()
        self._thread = Thread(
            target=self._run,
            name="picatchup-pisugar-button",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        try:
            import smbus
        except ImportError:
            log.error("python3-smbus non installato")
            return

        bus = smbus.SMBus(self._bus_number)

        pressed_at: float | None = None
        previous = False

        try:
            while not self._stop.is_set():
                try:
                    value = bus.read_byte_data(self._address, 0x02)
                    pressed = bool(value & 0x01)
                except OSError as error:
                    # PiSugar can occasionally NACK an I2C read.
                    log.debug("PiSugar I2C temporary error: %s", error)
                    self._stop.wait(0.05)
                    continue

                now = time.monotonic()

                if pressed and not previous:
                    pressed_at = now

                elif not pressed and previous:
                    if pressed_at is not None:
                        duration = now - pressed_at

                        if duration < self._max_click_seconds:
                            log.info(
                                "PiSugar short press %.2fs",
                                duration,
                            )
                            try:
                                self._callback()
                            except Exception:
                                log.exception("PiSugar button callback failed")
                        else:
                            log.info(
                                "PiSugar long press %.2fs ignored by PiCatchUp",
                                duration,
                            )

                    pressed_at = None

                previous = pressed
                self._stop.wait(0.03)

        finally:
            bus.close()


class PiSugarBattery:
    def __init__(
        self,
        *,
        bus_number: int = 1,
        address: int = 0x57,
    ) -> None:
        self._bus_number = bus_number
        self._address = address

    def read_status(self) -> tuple[int | None, bool | None]:
        """Return battery percentage and external-power state."""
        try:
            import smbus
        except ImportError:
            log.warning("python3-smbus non installato")
            return None, None

        try:
            bus = smbus.SMBus(self._bus_number)
            try:
                percent = bus.read_byte_data(self._address, 0x2A)
                control = bus.read_byte_data(self._address, 0x02)
            finally:
                bus.close()
        except OSError as error:
            log.debug("PiSugar status read failed: %s", error)
            return None, None

        if not 0 <= percent <= 100:
            log.warning(
                "PiSugar battery returned invalid percentage: %s",
                percent,
            )
            percent = None

        external_power = bool(control & 0x80)
        return percent, external_power

    def read_percent(self) -> int | None:
        try:
            import smbus
        except ImportError:
            log.warning("python3-smbus non installato")
            return None

        try:
            bus = smbus.SMBus(self._bus_number)
            try:
                value = bus.read_byte_data(self._address, 0x2A)
            finally:
                bus.close()
        except OSError as error:
            log.debug("PiSugar battery read failed: %s", error)
            return None

        if 0 <= value <= 100:
            return value

        log.warning("PiSugar battery returned invalid percentage: %s", value)
        return None
