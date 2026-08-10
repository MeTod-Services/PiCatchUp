"""OLED status display with a hardware-free fallback.

The SSD1306, I2C and Pillow packages are imported only by ``open``.  Rendering
reuses one bus, image and drawing context; it never scans the I2C bus.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from picatchup.config import Settings
from picatchup.status import DeviceStatus, Status

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DisplayFrame:
    """Exactly four readable rows for a 128×64 display."""

    line1: str
    line2: str = ""
    line3: str = ""
    line4: str = ""

    @property
    def lines(self) -> tuple[str, str, str, str]:
        return (self.line1, self.line2, self.line3, self.line4)


def frame_for(snapshot: DeviceStatus) -> DisplayFrame:
    """Turn status into small-screen-specific text without fake percentages."""
    battery = (
        f"Batt: {min(100, max(0, snapshot.battery_percent))}%"
        if snapshot.battery_percent is not None
        else "Batt: --"
    )
    detail = snapshot.detail or ""
    if snapshot.status is Status.BOOTING:
        return DisplayFrame("PiCatchUp", "", "Avvio...")
    if snapshot.status is Status.READY:
        return DisplayFrame(
            "PiCatchUp PRONTO",
            detail or "Mic: --  WiFi: --",
            battery,
            "Premi pulsante",
        )
    if snapshot.status is Status.RECORDING:
        elapsed = max(0, snapshot.elapsed_seconds or 0)
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        indicator = "● REC" if elapsed % 2 == 0 else "  REC"
        return DisplayFrame(
            indicator,
            detail or "Lezione",
            f"{hours:02d}:{minutes:02d}:{seconds:02d}",
            "Premi per stop",
        )
    if snapshot.status is Status.SAVING:
        return DisplayFrame("Salvataggio", "registrazione...")
    if snapshot.status is Status.TRANSCRIBING:
        return DisplayFrame("Elaborazione", "Trascrizione...", detail)
    if snapshot.status is Status.SUMMARIZING:
        return DisplayFrame("Elaborazione", "Creo riassunto...")
    if snapshot.status is Status.SENDING:
        return DisplayFrame("Elaborazione", "Invio Telegram...")
    if snapshot.status is Status.SUCCESS:
        return DisplayFrame(
            "FATTO",
            detail or "Riassunto inviato",
            "",
            datetime.now().strftime("%H:%M"),
        )
    return DisplayFrame("ERRORE", detail[:20], detail[20:40])


class Display(Protocol):
    def render(self, snapshot: DeviceStatus) -> None: ...

    def close(self) -> None: ...


class NoOpDisplay:
    def render(self, snapshot: DeviceStatus) -> None:
        pass

    def close(self) -> None:
        pass


class SSD1306Display:
    """Pillow renderer for an already-open SSD1306 display."""

    def __init__(
        self,
        driver: Any,
        image: Any,
        draw: Any,
        font: Any,
        *,
        width: int,
        height: int,
    ) -> None:
        self._driver = driver
        self._image = image
        self._draw = draw
        self._font = font
        self.width = width
        self.height = height
        self._closed = False
        self._failed_at: float | None = None

    @classmethod
    def open(cls, *, address: int, width: int = 128, height: int = 64) -> SSD1306Display:
        from luma.core.interface.serial import i2c  # type: ignore[import-not-found]
        from luma.oled.device import ssd1306  # type: ignore[import-not-found]
        from PIL import Image, ImageDraw, ImageFont  # type: ignore[import-not-found]

        # Open the configured address exactly once.  In particular, do not scan:
        # PiSugar and the OLED may share this bus.
        serial = i2c(port=1, address=address)
        driver = ssd1306(serial, width=width, height=height)
        image = Image.new("1", (width, height))
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 12)
        except OSError:
            font = ImageFont.load_default()
        return cls(driver, image, draw, font, width=width, height=height)

    def render(self, snapshot: DeviceStatus) -> None:
        if self._closed:
            return
        if self._failed_at is not None and time.monotonic() - self._failed_at < 30:
            return
        frame = frame_for(snapshot)
        try:
            self._draw.rectangle((0, 0, self.width, self.height), outline=0, fill=0)
            max_characters = max(1, self.width // 6)
            lines = tuple(line[:max_characters] for line in frame.lines)
            positions = (0, 16, 32, max(48, self.height - 12))
            for text, y in zip(lines, positions, strict=True):
                self._draw.text((0, y), text, font=self._font, fill=255)
            self._driver.display(self._image)
            self._failed_at = None
        except Exception as error:
            self._failed_at = time.monotonic()
            log.warning("display_error: could not update OLED display: %s", error)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._driver.cleanup()
        except Exception as error:
            log.warning("could not clear OLED display: %s", error)


# A descriptive alias for callers that do not care which SSD1306 library is used.
OLEDDisplay = SSD1306Display


def create_display(settings: Settings) -> Display:
    if not settings.display_enabled:
        return NoOpDisplay()
    try:
        return SSD1306Display.open(
            address=settings.oled_i2c_address,
            width=settings.oled_width,
            height=settings.oled_height,
        )
    except Exception as error:
        log.warning("OLED display unavailable at 0x%02x: %s", settings.oled_i2c_address, error)
        return NoOpDisplay()
