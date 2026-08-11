"""Standalone one-button Raspberry Pi device loop.

GPIO callbacks only enqueue an event.  Recording, status-file writes and OLED
updates happen away from gpiozero's callback thread.
"""

from __future__ import annotations

import logging
import queue
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from threading import Event, Lock, Thread, current_thread
from types import FrameType
from typing import Protocol
from zoneinfo import ZoneInfo

from picatchup import audio, hardware, latest
from picatchup.config import Settings
from picatchup.display import Display, create_display
from picatchup.pisugar import PiSugarBattery, PiSugarButton
from picatchup.errors import RaspberryPiError
from picatchup.status import DeviceStatus, Status, StatusFile

log = logging.getLogger(__name__)

SUCCESS_VISIBLE_SECONDS = 4.0
ERROR_VISIBLE_SECONDS = 10.0
EXTERNAL_STATUS_INTERVAL = 1.0
BATTERY_UPDATE_INTERVAL = 30.0

Recorder = Callable[..., Path]


class StatusLED(Protocol):
    def show_status(self, status: Status) -> None: ...

    def close(self) -> None: ...


class StatusPublisher(Protocol):
    def publish(self, snapshot: DeviceStatus | Status, *, detail: str | None = None) -> None: ...


class _EventKind(Enum):
    PRESS = auto()
    RECORDING_DONE = auto()
    SHUTDOWN = auto()


@dataclass(frozen=True)
class _RecordingResult:
    destination: Path
    error: str | None = None


@dataclass(frozen=True)
class _DeviceEvent:
    kind: _EventKind
    result: _RecordingResult | None = None


class DeviceController:
    """Coordinate recording and the three small status outputs."""

    def __init__(
        self,
        settings: Settings,
        *,
        recorder: Recorder = audio.record,
        led: StatusLED | None = None,
        display: Display | None = None,
        publisher: StatusPublisher | None = None,
        reader: Callable[[], DeviceStatus | None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        elapsed_interval: float = 1.0,
    ) -> None:
        self.settings = settings
        self._recorder = recorder
        self._led = led or hardware.create_led(settings)
        self._display = display or create_display(settings)
        status_file = StatusFile(settings.status_file)
        self._publisher = publisher or status_file
        if reader is not None:
            self._reader = reader
        elif isinstance(self._publisher, StatusFile):
            self._reader = self._publisher.read
        else:
            self._reader = lambda: None
        self._monotonic = monotonic
        self._elapsed_interval = max(0.05, elapsed_interval)
        self._ready_detail = _ready_detail(settings)
        self._events: queue.Queue[_DeviceEvent] = queue.Queue()
        self._lock = Lock()
        self._update_lock = Lock()
        self._dispatcher: Thread | None = None
        self._recorder_thread: Thread | None = None
        self._recording_stop: Event | None = None
        self._recording_started: float | None = None
        self._processing = False
        self._battery = PiSugarBattery()
        self._battery_percent: int | None = None
        self._snapshot = DeviceStatus(Status.BOOTING)
        self._last_seen_snapshot = self._snapshot
        self._closed = False
        self._stopped = Event()

    @property
    def snapshot(self) -> DeviceStatus:
        with self._lock:
            return self._snapshot

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording_stop is not None

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("device controller is closed")
            if self._dispatcher is not None:
                return
            dispatcher = Thread(target=self._dispatch, name="picatchup-device", daemon=True)
            self._dispatcher = dispatcher
        self.update(Status.BOOTING)
        dispatcher.start()
        percent = self._battery.read_percent()
        if percent is not None:
            self._battery_percent = percent

        self.update(Status.READY, detail=self._ready_detail)

    def press(self) -> None:
        """GPIO-safe callback: enqueue and return without touching hardware."""
        with self._lock:
            running = self._dispatcher is not None and not self._closed
            processing = self._processing
        if running and not processing:
            self._events.put_nowait(_DeviceEvent(_EventKind.PRESS))

    def request_shutdown(self) -> None:
        """Signal-safe in practice: wake the dispatcher and do no waiting."""
        with self._lock:
            running = self._dispatcher is not None and not self._closed
        if running:
            self._events.put_nowait(_DeviceEvent(_EventKind.SHUTDOWN))
        else:
            self._stopped.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._stopped.wait(timeout)

    def update(
        self,
        status: Status,
        *,
        detail: str | None = None,
        elapsed_seconds: int | None = None,
        battery_percent: int | None = None,
    ) -> DeviceStatus:
        """Publish one coherent update to the file, OLED and LED."""
        with self._update_lock:
            if battery_percent is None:
                battery_percent = self._battery_percent

            snapshot = DeviceStatus(
                status,
                detail=detail,
                elapsed_seconds=elapsed_seconds,
                battery_percent=battery_percent,
            )
            with self._lock:
                previous_status = self._snapshot.status
                self._snapshot = snapshot
                self._last_seen_snapshot = snapshot
            try:
                self._publisher.publish(snapshot)
            except Exception as error:
                log.warning("device status publisher failed: %s", error)
            try:
                self._display.render(snapshot)
            except Exception as error:
                log.warning("device display failed: %s", error)
            if status is not previous_status:
                try:
                    self._led.show_status(status)
                except Exception as error:
                    log.warning("device status LED failed: %s", error)
            return snapshot

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            dispatcher = self._dispatcher
        if dispatcher is not None and dispatcher.is_alive():
            self.request_shutdown()
            if dispatcher is not current_thread():
                dispatcher.join()
        with self._lock:
            self._closed = True
            recorder = self._recorder_thread
            stop = self._recording_stop
        if stop is not None:
            stop.set()
        if recorder is not None and recorder.is_alive() and recorder is not current_thread():
            recorder.join()
        try:
            self._display.close()
        except Exception as error:
            log.warning("could not close device display: %s", error)
        try:
            self._led.close()
        except Exception as error:
            log.warning("could not close device LED: %s", error)
        self._stopped.set()

    def _dispatch(self) -> None:
        shutting_down = False
        next_elapsed: float | None = None
        return_ready_at: float | None = None
        next_external = self._monotonic() + EXTERNAL_STATUS_INTERVAL
        next_battery = self._monotonic() + BATTERY_UPDATE_INTERVAL
        try:
            while True:
                now = self._monotonic()
                deadlines = [next_external, next_battery]
                deadlines.extend(
                    deadline for deadline in (next_elapsed, return_ready_at) if deadline is not None
                )
                timeout = max(0.0, min(deadlines) - now)
                try:
                    event = self._events.get(timeout=timeout)
                except queue.Empty:
                    now = self._monotonic()
                    if next_elapsed is not None and now >= next_elapsed:
                        self._publish_elapsed()
                        next_elapsed = now + self._elapsed_interval
                    if now >= next_external:
                        external = self._sync_external_status()
                        next_external = now + EXTERNAL_STATUS_INTERVAL
                        if external is Status.SUCCESS:
                            return_ready_at = now + SUCCESS_VISIBLE_SECONDS
                        elif external is Status.ERROR:
                            return_ready_at = now + ERROR_VISIBLE_SECONDS
                    if now >= next_battery:
                        percent = self._battery.read_percent()
                        next_battery = now + BATTERY_UPDATE_INTERVAL

                        if percent is not None:
                            self._battery_percent = percent

                            with self._lock:
                                current = self._snapshot

                            self.update(
                                current.status,
                                detail=current.detail,
                                elapsed_seconds=current.elapsed_seconds,
                                battery_percent=percent,
                            )

                    if return_ready_at is not None and now >= return_ready_at:
                        self.update(Status.READY, detail=self._ready_detail)
                        return_ready_at = None
                    continue

                if event.kind is _EventKind.PRESS and not shutting_down:
                    return_ready_at = None
                    if self.is_recording:
                        self._request_recording_stop()
                        next_elapsed = None
                    else:
                        if self._start_recording():
                            next_elapsed = self._monotonic() + self._elapsed_interval
                        else:
                            next_elapsed = None
                            return_ready_at = self._monotonic() + ERROR_VISIBLE_SECONDS
                elif event.kind is _EventKind.RECORDING_DONE:
                    if event.result is not None:
                        finished = self._finish_recording(event.result)
                        visible = (
                            SUCCESS_VISIBLE_SECONDS
                            if finished is Status.SUCCESS
                            else ERROR_VISIBLE_SECONDS
                        )
                        return_ready_at = self._monotonic() + visible
                    next_elapsed = None
                    if shutting_down:
                        return
                elif event.kind is _EventKind.SHUTDOWN:
                    shutting_down = True
                    if self.is_recording:
                        self._request_recording_stop()
                        next_elapsed = None
                    else:
                        return
        finally:
            self._stopped.set()

    def _start_recording(self) -> bool:
        try:
            audio.check_input_device(
                self.settings.audio_input_format, self.settings.audio_input_device
            )
        except Exception as error:
            log.error("microphone preflight failed: %s", error)
            self.update(Status.ERROR, detail="Microfono non disponibile")
            return False
        stop = Event()
        destination = self._destination()
        started = self._monotonic()
        worker = Thread(
            target=self._record,
            args=(destination, stop),
            name="picatchup-recorder",
            daemon=True,
        )
        with self._lock:
            self._recording_stop = stop
            self._recording_started = started
            self._recorder_thread = worker
        self.update(Status.RECORDING, elapsed_seconds=0)
        worker.start()
        return True

    def _request_recording_stop(self) -> None:
        with self._lock:
            stop = self._recording_stop
        if stop is not None and not stop.is_set():
            stop.set()
            self.update(Status.SAVING)

    def _publish_elapsed(self) -> None:
        with self._lock:
            started = self._recording_started
            stop = self._recording_stop
        if started is None or stop is None or stop.is_set():
            return
        elapsed = max(0, int(self._monotonic() - started))
        self.update(Status.RECORDING, elapsed_seconds=elapsed)

    def _record(self, destination: Path, stop: Event) -> None:
        error: str | None = None
        try:
            self._recorder(
                destination,
                input_format=self.settings.audio_input_format,
                device=self.settings.audio_input_device,
                bitrate=self.settings.audio_bitrate,
                stop_event=stop,
            )
            if not destination.is_file() or destination.stat().st_size == 0:
                error = "recorder produced no audio"
        except Exception as caught:  # the device loop must survive a bad microphone
            technical_error = str(caught) or caught.__class__.__name__
            log.error("recording failed: %s", technical_error)
            error = "Microfono non disponibile"
        self._events.put(
            _DeviceEvent(_EventKind.RECORDING_DONE, _RecordingResult(destination, error))
        )

    def _finish_recording(self, result: _RecordingResult) -> Status:
        with self._lock:
            self._recording_stop = None
            self._recording_started = None
            self._recorder_thread = None

        if result.error is not None:
            self.update(Status.ERROR, detail=result.error)
            return Status.ERROR

        size_mb = result.destination.stat().st_size / 1_000_000
        log.info("saved %s (%.1f MB)", result.destination, size_mb)

        # L'audio è già al sicuro a questo punto. Gli errori successivi
        # non devono mai cancellare la registrazione originale.
        with self._lock:
            self._processing = True

        try:
            self.update(Status.TRANSCRIBING, detail=result.destination.name)

            transcribe = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "picatchup",
                    "transcribe",
                    str(result.destination),
                    "--subject",
                    "generale",
                ],
                cwd=str(Path(__file__).resolve().parents[2]),
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
            )

            if transcribe.returncode != 0:
                detail = (transcribe.stderr or transcribe.stdout or "").strip()
                log.error("automatic transcription failed: %s", detail[-800:])
                self.update(Status.ERROR, detail="Trascrizione fallita")
                return Status.ERROR

            log.info("automatic transcription completed: %s", result.destination.name)

            # Riassume SOLO la registrazione appena conclusa e invia
            # quel riassunto su Telegram.
            self.update(Status.SUMMARIZING, detail="Riassumo lezione...")

            latest.summarize_and_send(
                self.settings,
                result.destination,
            )

            log.info(
                "single-recording summary delivered: %s",
                result.destination.name,
            )
            self.update(Status.SUCCESS, detail="Riassunto inviato!")
            return Status.SUCCESS

        except subprocess.TimeoutExpired:
            log.error("automatic processing timed out")
            self.update(Status.ERROR, detail="Elaborazione scaduta")
            return Status.ERROR
        except Exception as error:
            log.exception("automatic processing failed: %s", error)
            self.update(Status.ERROR, detail="Elaborazione fallita")
            return Status.ERROR
        finally:
            with self._lock:
                self._processing = False
                self._battery = PiSugarBattery()
                self._battery_percent: int | None = None

    def _sync_external_status(self) -> Status | None:
        """Render a status written by daily/backup without republishing it."""
        external = self._reader()
        if external is None:
            return None
        with self._lock:
            if external == self._last_seen_snapshot:
                return None
            self._last_seen_snapshot = external
            if self._recording_stop is not None:
                return None
            rendered = external
            if external.status is Status.READY and not external.detail:
                rendered = DeviceStatus(Status.READY, detail=self._ready_detail)
            self._snapshot = rendered
        try:
            self._display.render(rendered)
        except Exception as error:
            log.warning("device display failed: %s", error)
        return rendered.status

    def _destination(self) -> Path:
        now = datetime.now(ZoneInfo(self.settings.timezone))
        stem = f"{now:%Y-%m-%d-%H%M%S}"
        destination = self.settings.inbox_dir / f"{stem}.mp3"
        number = 2
        while destination.exists():
            destination = self.settings.inbox_dir / f"{stem}-{number}.mp3"
            number += 1
        return destination


def _ready_detail(settings: Settings) -> str:
    microphone = "--"
    try:
        if settings.audio_input_format == "alsa":
            audio.check_input_device(
                settings.audio_input_format, settings.audio_input_device
            )
            microphone = "OK"
    except Exception:
        pass
    try:
        import subprocess

        result = subprocess.run(
            ["iw", "dev", "wlan0", "link"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        wifi = "OK" if "Connected to " in result.stdout else "--"
    except Exception:
        wifi = "--"
    return f"Mic: {microphone}  WiFi: {wifi}"


def listen(settings: Settings) -> None:
    """Run the standalone device until SIGINT or SIGTERM."""
    controller = DeviceController(settings)
    button: PiSugarButton | None = None
    previous_int = signal.getsignal(signal.SIGINT)
    previous_term = signal.getsignal(signal.SIGTERM)

    def stop(_signum: int, _frame: FrameType | None) -> None:
        controller.request_shutdown()

    controller.start()
    try:
        button = PiSugarButton(controller.press)
        button.start()
        log.info("PiSugar power button listener ready")
    except Exception as error:
        log.warning("PiSugar button unavailable: %s", error)
        button = None

    try:
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        controller.wait()
    except KeyboardInterrupt:
        controller.request_shutdown()
        controller.wait()
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
        if button is not None:
            try:
                button.close()
            except Exception as error:
                log.warning("could not close GPIO button: %s", error)
        controller.close()
