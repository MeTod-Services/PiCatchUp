"""Everything we do to an audio file before it leaves the machine.

Recording, converting to mp3, measuring, splitting, checksumming. It is all
ffmpeg underneath: this file is the only place that knows the command line.

Nothing here talks to the network, so it is not a provider.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import signal
import subprocess
from pathlib import Path
from threading import Event

from picatchup.errors import AudioError

# Speech, not music: one channel at 16 kHz is what Whisper listens to anyway,
# and it turns two hours of lesson into a file you can actually upload.
CHANNELS = "1"
SAMPLE_RATE = "16000"

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
    ensure_ffmpeg()
    destination.parent.mkdir(parents=True, exist_ok=True)

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-f",
        input_format,
        "-i",
        device,
        "-ac",
        CHANNELS,
        "-ar",
        SAMPLE_RATE,
        "-c:a",
        "libmp3lame",
        "-b:a",
        bitrate,
    ]
    if minutes is not None:
        command += ["-t", str(int(minutes * 60))]
    command.append(str(destination))

    log.info("recording from %s:%s to %s", input_format, device, destination.name)
    try:
        process = subprocess.Popen(command, start_new_session=True)
    except OSError as error:
        raise AudioError(f"could not start ffmpeg: {error}") from None

    try:
        if stop_event is None:
            process.wait()
        else:
            while process.poll() is None:
                if stop_event.wait(timeout=0.2):
                    _stop_recording(process)
                    break
    except KeyboardInterrupt:
        _stop_recording(process)

    if not destination.is_file() or destination.stat().st_size == 0:
        raise AudioError(
            f"nothing was recorded. Check that {input_format}:{device} is the right microphone "
            "(list them with: arecord -l)"
        )
    return destination


def to_mp3(source: Path, destination: Path, *, bitrate: str = "48k") -> Path:
    """Convert any recording (a phone's .m4a, say) into our mp3 shape."""
    ensure_ffmpeg()
    destination.parent.mkdir(parents=True, exist_ok=True)
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
            SAMPLE_RATE,
            "-c:a",
            "libmp3lame",
            "-b:a",
            bitrate,
            str(destination),
        ]
    )
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


def _stop_recording(process: subprocess.Popen[bytes]) -> None:
    """Ask ffmpeg to finish the current frame and close the output cleanly."""
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise AudioError("ffmpeg would not stop; the recording may be truncated") from None


def _run(command: list[str]) -> str:
    try:
        finished = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as error:
        raise AudioError(f"could not run {command[0]}: {error}") from None
    if finished.returncode != 0:
        detail = " ".join((finished.stderr or "").split())[:300]
        raise AudioError(f"{command[0]} failed: {detail or 'no details given'}")
    return finished.stdout
