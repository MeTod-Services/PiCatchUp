from __future__ import annotations

from picatchup.display import NoOpDisplay, SSD1306Display, frame_for
from picatchup.status import DeviceStatus, Status


def test_every_status_has_a_human_readable_128x64_frame() -> None:
    states: list[str] = []
    for status in Status:
        frame = frame_for(DeviceStatus(status))
        assert frame.line1
        assert len(frame.lines) == 4
        states.append("|".join(frame.lines))

    assert len(set(states)) == len(Status)


def test_recording_frame_formats_elapsed_time_and_optional_battery() -> None:
    frame = frame_for(DeviceStatus(Status.RECORDING, elapsed_seconds=125, battery_percent=84))

    assert frame.line3 == "00:02:05"
    assert frame.line4 == "Premi per stop"


class FakeDriver:
    def __init__(self) -> None:
        self.images: list[object] = []
        self.cleanup_count = 0

    def display(self, image: object) -> None:
        self.images.append(image)

    def cleanup(self) -> None:
        self.cleanup_count += 1


class FakeDraw:
    def __init__(self) -> None:
        self.texts: list[tuple[tuple[int, int], str]] = []
        self.clears = 0

    def rectangle(self, box: object, *, outline: int, fill: int) -> None:
        self.clears += 1

    def text(self, position: tuple[int, int], text: str, **options: object) -> None:
        self.texts.append((position, text))


def test_ssd1306_adapter_renders_text_without_pixel_assertions() -> None:
    driver = FakeDriver()
    draw = FakeDraw()
    image = object()
    display = SSD1306Display(driver, image, draw, object(), width=128, height=64)

    display.render(DeviceStatus(Status.TRANSCRIBING, detail="lesson.mp3"))

    rendered = [text for _position, text in draw.texts]
    assert rendered == ["Elaborazione", "Trascrizione...", "lesson.mp3", ""]
    assert draw.clears == 1
    assert driver.images == [image]

    display.close()
    assert driver.cleanup_count == 1


def test_noop_display_accepts_every_state() -> None:
    display = NoOpDisplay()
    for status in Status:
        display.render(DeviceStatus(status))
    display.close()
