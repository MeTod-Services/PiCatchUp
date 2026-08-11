"""Everything we do to an audio file before it leaves the machine.

Recording, converting to mp3, measuring, splitting, checksumming. It is all
ffmpeg underneath: this file is the only place that knows the command line.

Nothing here talks to the network, so it is not a provider.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from threading import Event, current_thread, main_thread
from typing import Any

from picatchup.errors import AudioError

# Speech, not music: one channel at 16 kHz is what Whisper listens to anyway,
# and it turns two hours of lesson into a file you can actually upload.
CHANNELS = "1"
INPUT_SAMPLE_RATE = "48000"
OUTPUT_SAMPLE_RATE = "16000"

# Audio we know how to send to Whisper. Anything else we convert first.
KNOWN_SUFFIXES = frozenset(
    {".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac", ".aac", ".mp4", ".webm", ".mpga"}
)

log = logging.getLogger(__name__)


def ensure_ffmpeg() -> None:
    """Fail early and clearly, rather than in the middle of a two-hour job."""
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise AudioError(
                f"{tool} is not installed. On Debian and Raspberry Pi OS:"
                " sudo apt install ffmpeg"
            )


def sha256_of(path: Path) -> str:
    """The file's fingerprint. Same contents, same number, so we never pay to
    transcribe the same recording twice."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def duration_ms(path: Path) -> int:
    """How long the recording is, in milliseconds."""
    output = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    try:
        return int(float(output.strip()) * 1000)
    except ValueError:
        raise AudioError(f"{path.name}: ffprobe could not measure its duration") from None


def validate_audio(path: Path) -> int:
    """Prove that ffprobe can open an audio file and that it has a duration."""
    if not path.is_file() or path.stat().st_size == 0:
        raise AudioError(f"{path.name}: no audio output was produced")
    measured = duration_ms(path)
    if measured <= 0:
        raise AudioError(f"{path.name}: the audio has no playable duration")
    return measured


def alsa_capture_devices() -> list[str]:
    """Return ALSA hardware capture ids reported by ``arecord -l``."""
    if shutil.which("arecord") is None:
        raise AudioError(
            "arecord is not installed. On Raspberry Pi OS: sudo apt install alsa-utils"
        )
    try:
        result = subprocess.run(
            ["arecord", "-l"], capture_output=True, text=True, check=False, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AudioError(f"could not list ALSA microphones: {error}") from None
    if result.returncode != 0:
        detail = " ".join((result.stderr or "").split())[:200]
        raise AudioError(f"could not list ALSA microphones: {detail or 'arecord failed'}")
    return [
        f"hw:{card},{device}"
        for card, device in re.findall(r"^card (\d+):.*device (\d+):", result.stdout, re.MULTILINE)
    ]


def resolve_input_device(input_format: str, device: str) -> str:
    """Backward-compatible name for the full input preflight."""
    return check_input_device(input_format, device)


def check_input_device(input_format: str, device: str) -> str:
    """Perform a non-recording preflight and return the ffmpeg device name."""
    if input_format != "alsa":
        return device
    devices = alsa_capture_devices()
    if device == "auto":
        if not devices:
            raise AudioError(
                "no ALSA capture device found (check the USB microphone and: arecord -l)"
            )
        if len(devices) > 1:
            choices = ", ".join(devices)
            raise AudioError(
                f"multiple ALSA capture devices found ({choices}); "
                "set AUDIO_INPUT_DEVICE explicitly"
            )
        return f"plughw:{devices[0].removeprefix('hw:')}"
    if device.startswith(("hw:", "plughw:")):
        hardware_name = device.removeprefix("plug")
        if hardware_name not in devices:
            raise AudioError(f"ALSA capture device {device} was not found (check: arecord -l)")
    elif not devices:
        raise AudioError("no ALSA capture device found (check the USB microphone and: arecord -l)")
    return device


def record(
    destination: Path,
    *,
    input_format: str,
    device: str,
    bitrate: str = "48k",
    minutes: float | None = None,
    stop_event: Event | None = None,
) -> Path:
    """Record the microphone until Ctrl-C, a stop event, or `minutes` pass.

    ffmpeg runs in its own session so the terminal's Ctrl-C does not reach it.
    We forward every stop as SIGINT and wait, because an mp3 whose writer was
    killed mid-frame is a lesson you have to record again. ``stop_event`` is
    used by the Raspberry Pi button listener.
    """
    if minutes is not None and minutes <= 0:
        raise AudioError("recording minutes must be greater than zero")
    ensure_ffmpeg()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise AudioError(f"refusing to overwrite existing recording: {destination.name}")
    temporary = destination.with_name(f".{destination.name}.part")
    temporary.unlink(missing_ok=True)
    device = check_input_device(input_format, device)

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-f",
        input_format,
    ]
    if input_format == "alsa":
        # These are input constraints; plughw performs conversion for simple
        # USB Audio Class microphones that expose another native format.
        command += ["-ac", CHANNELS, "-ar", INPUT_SAMPLE_RATE]
    command += [
        "-i",
        device,
        "-ac",
        CHANNELS,
        "-ar",
        OUTPUT_SAMPLE_RATE,
        "-c:a",
        "libmp3lame",
        "-b:a",
        bitrate,
    ]
    if minutes is not None:
        command += ["-t", str(minutes * 60)]
    command += ["-f", "mp3", str(temporary)]

    log.info("recording_started input=%s device=%s file=%s", input_format, device, destination.name)
    # Kept open across the child lifetime and closed on every exit path below.
    error_log = tempfile.TemporaryFile()  # noqa: SIM115
    try:
        process = subprocess.Popen(command, start_new_session=True, stderr=error_log)
    except OSError as error:
        error_log.close()
        temporary.unlink(missing_ok=True)
        raise AudioError(f"could not start ffmpeg: {error}") from None

    terminate = Event()
    previous_sigterm: Any = None
    if current_thread() is main_thread():
        previous_sigterm = signal.getsignal(signal.SIGTERM)

        def request_stop(signum: int, frame: object) -> None:
            terminate.set()

        signal.signal(signal.SIGTERM, request_stop)

    interrupted = False
    try:
        while process.poll() is None:
            requested = terminate.is_set()
            if stop_event is not None:
                requested = stop_event.wait(timeout=0.2) or requested
            else:
                requested = terminate.wait(timeout=0.2) or requested
            if requested:
                interrupted = _stop_recording(process)
                break
    except KeyboardInterrupt:
        interrupted = _stop_recording(process)
    except AudioError:
        temporary.unlink(missing_ok=True)
        error_log.close()
        raise
    finally:
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)

    error_log.seek(0)
    stderr = error_log.read().decode("utf-8", errors="replace")
    error_log.close()
    if process.returncode != 0 and not (interrupted and process.returncode == 255):
        temporary.unlink(missing_ok=True)
        detail = " ".join(stderr.split())[:300]
        raise AudioError(
            f"ffmpeg could not record from {input_format}:{device}: {detail or 'no details given'}"
        )
    try:
        validate_audio(temporary)
        temporary.replace(destination)
    except (AudioError, OSError) as error:
        temporary.unlink(missing_ok=True)
        if isinstance(error, AudioError):
            raise
        raise AudioError(f"could not finalize {destination.name}: {error}") from None
    log.info("recording_stopped file=%s", destination.name)
    log.info("audio_saved file=%s bytes=%d", destination.name, destination.stat().st_size)
    return destination


def to_mp3(source: Path, destination: Path, *, bitrate: str = "48k") -> Path:
    """Convert any recording (a phone's .m4a, say) into our mp3 shape."""
    ensure_ffmpeg()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    try:
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-i",
                str(source),
                "-vn",
                "-ac",
                CHANNELS,
                "-ar",
                OUTPUT_SAMPLE_RATE,
                "-c:a",
                "libmp3lame",
                "-b:a",
                bitrate,
                str(destination),
            ]
        )
        validate_audio(destination)
    except AudioError:
        destination.unlink(missing_ok=True)
        raise
    return destination


def split(source: Path, into: Path, *, minutes: int) -> list[Path]:
    """Cut a long recording into pieces small enough to upload.

    Returns the pieces in order. The caller works out where each one starts by
    measuring the ones before it — see `transcribe.py` — because the segment
    muxer cuts on frame boundaries, not exactly on the minute.
    """
    ensure_ffmpeg()
    into.mkdir(parents=True, exist_ok=True)
    pattern = into / "piece-%03d.mp3"
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-f",
            "segment",
            "-segment_time",
            str(minutes * 60),
            "-c",
            "copy",
            str(pattern),
        ]
    )
    pieces = sorted(into.glob("piece-*.mp3"))
    if not pieces:
        raise AudioError(f"{source.name}: ffmpeg produced no pieces")
    log.info("split %s into %d pieces", source.name, len(pieces))
    return pieces


def _stop_recording(process: subprocess.Popen[bytes]) -> bool:
    """Ask ffmpeg to finish and report whether SIGINT was actually sent."""
    if process.poll() is not None:
        return False
    try:
        process.send_signal(signal.SIGINT)
    except ProcessLookupError:
        return False
    except OSError as error:
        raise AudioError(f"could not stop ffmpeg cleanly: {error}") from None
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise AudioError("ffmpeg would not stop; the recording may be truncated") from None
    return True


def _run(command: list[str]) -> str:
    try:
        finished = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as error:
        raise AudioError(f"could not run {command[0]}: {error}") from None
    if finished.returncode != 0:
        detail = " ".join((finished.stderr or "").split())[:300]
        raise AudioError(f"{command[0]} failed: {detail or 'no details given'}")
    return finished.stdout
