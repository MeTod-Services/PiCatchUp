"""Offline operational checks for ``doctor`` and concise status output.

No probe in this module contacts Groq, Telegram, or any other network service.
Hardware checks either inspect the host or run local listing tools; in
particular, microphone checks never start a recording.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from picatchup import audio
from picatchup.config import Settings
from picatchup.status import Status
from picatchup.status import read_status as read_device_status

_REQUIRED_TABLES = frozenset(
    {"lessons", "chunks", "chunks_fts", "recaps", "users", "deliveries", "bot_state"}
)


@dataclass(frozen=True, slots=True)
class Check:
    """One human-readable health or status row."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The small, stable part of ``CompletedProcess`` needed by probes."""

    returncode: int
    output: str


class Probes(Protocol):
    """Injectable host operations, primarily for non-RPi unit tests."""

    def python_version(self) -> tuple[int, int, int]: ...

    def command_exists(self, command: str) -> bool: ...

    def run(self, command: Sequence[str]) -> CommandResult: ...

    def module_available(self, module: str) -> bool: ...

    def gpio_chip_available(self) -> bool: ...

    def i2c_buses(self) -> list[int]: ...


class SystemProbes:
    """Real, bounded local probes used outside tests."""

    def python_version(self) -> tuple[int, int, int]:
        return sys.version_info.major, sys.version_info.minor, sys.version_info.micro

    def command_exists(self, command: str) -> bool:
        return shutil.which(command) is not None

    def run(self, command: Sequence[str]) -> CommandResult:
        try:
            finished = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return CommandResult(124, str(error))
        return CommandResult(finished.returncode, f"{finished.stdout}\n{finished.stderr}")

    def module_available(self, module: str) -> bool:
        try:
            return importlib.util.find_spec(module) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            return False

    def gpio_chip_available(self) -> bool:
        return any(Path("/dev").glob("gpiochip*"))

    def i2c_buses(self) -> list[int]:
        buses: list[int] = []
        for path in Path("/dev").glob("i2c-*"):
            try:
                buses.append(int(path.name.removeprefix("i2c-")))
            except ValueError:
                continue
        return sorted(buses)


def doctor(settings: Settings, *, probes: Probes | None = None) -> list[Check]:
    """Return comprehensive, offline installation and hardware checks."""
    host = probes or SystemProbes()
    checks: list[Check] = []

    version = host.python_version()
    checks.append(
        Check(
            "Python >= 3.11",
            version >= (3, 11, 0),
            ".".join(str(part) for part in version),
        )
    )
    for command in ("ffmpeg", "ffprobe"):
        available = host.command_exists(command)
        checks.append(Check(command, available, "available" if available else "not installed"))
    checks.append(_fts5_check())
    checks.extend(_database_checks(settings.db_path))

    for name, path in (
        ("data directory", settings.data_dir),
        ("inbox directory", settings.inbox_dir),
        ("audio directory", settings.audio_dir),
        ("backup directory", settings.backup_dir),
    ):
        checks.append(_directory_check(name, path))

    groq_configured = bool(
        settings.groq_api_key and settings.transcription_model and settings.text_model
    )
    telegram_configured = bool(settings.telegram_bot_token)
    # Deliberately report presence only. Secret values and network calls do not
    # belong in diagnostics.
    checks.append(Check("Groq configuration (offline)", groq_configured, _yes_no(groq_configured)))
    checks.append(
        Check(
            "Telegram configuration (offline)",
            telegram_configured,
            _yes_no(telegram_configured),
        )
    )

    checks.append(_microphone_check(settings, host))
    checks.append(_network_check(host))
    checks.extend(_gpio_checks(settings, host))
    checks.extend(_oled_checks(settings, host))
    return checks


def status(settings: Settings, *, probes: Probes | None = None) -> list[Check]:
    """Return concise operational rows without exposing lesson or secret text."""
    host = probes or SystemProbes()
    rows = _database_status(settings.db_path)
    rows.append(_device_status_check(settings.status_file))
    rows.append(_microphone_check(settings, host))

    gpio_available, gpio_configuration = _gpio_checks(settings, host)
    gpio_ok = gpio_available.ok and gpio_configuration.ok
    if settings.gpio_enabled:
        rows.append(Check("button", gpio_ok, f"GPIO{settings.gpio_button_pin}"))
        rows.append(Check("LED", gpio_ok, f"GPIO{settings.gpio_led_pin}"))
    else:
        rows.append(Check("button", True, "disabled"))
        rows.append(Check("LED", True, "disabled"))

    _, oled = _oled_checks(settings, host)
    rows.append(Check("OLED", oled.ok, oled.detail))
    rows.append(_inbox_status(settings.inbox_dir))
    return rows


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _fts5_check() -> Check:
    try:
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute("CREATE VIRTUAL TABLE fts_probe USING fts5(content)")
    except sqlite3.Error as error:
        return Check("SQLite FTS5", False, f"unavailable: {error}")
    return Check("SQLite FTS5", True, "available")


def _readonly_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _database_checks(path: Path) -> list[Check]:
    if not path.is_file():
        return [
            Check("database", False, "missing"),
            Check("database schema", False, "database unavailable"),
            Check("database integrity", False, "database unavailable"),
        ]

    try:
        with closing(_readonly_connection(path)) as connection:
            names = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                )
            }
            integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error as error:
        return [
            Check("database", False, f"cannot open: {error}"),
            Check("database schema", False, "database unavailable"),
            Check("database integrity", False, "database unavailable"),
        ]

    missing = sorted(_REQUIRED_TABLES - names)
    integrity_ok = integrity_rows == [("ok",)]
    return [
        Check("database", True, "readable"),
        Check(
            "database schema",
            not missing,
            "complete" if not missing else f"missing: {', '.join(missing)}",
        ),
        Check(
            "database integrity",
            integrity_ok,
            "ok" if integrity_ok else "integrity_check failed",
        ),
    ]


def _directory_check(name: str, path: Path) -> Check:
    if not path.is_dir():
        return Check(name, False, "missing")
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".picatchup-doctor-", dir=path)
        os.close(descriptor)
        Path(temporary_name).unlink()
    except OSError as error:
        return Check(name, False, f"not writable: {error.strerror or error}")
    return Check(name, True, "writable")


def _microphone_check(settings: Settings, probes: Probes) -> Check:
    input_format = settings.audio_input_format.lower()
    device = settings.audio_input_device

    if input_format == "alsa":
        if not probes.command_exists("arecord"):
            return Check("microphone", False, "arecord is not installed")
        result = probes.run(("arecord", "-l"))
        output = result.output.lower()
        if result.returncode != 0 or "no soundcards found" in output:
            return Check("microphone", False, "no ALSA capture device found")
        if device == "auto":
            devices = re.findall(r"^card \d+:.*device \d+:", output, flags=re.MULTILINE)
            if not devices:
                return Check("microphone", False, "no ALSA capture device found")
            if len(devices) > 1:
                return Check(
                    "microphone",
                    False,
                    "multiple capture devices; set AUDIO_INPUT_DEVICE explicitly",
                )
            return Check("microphone", True, "one ALSA capture device available (auto)")
        if device != "default" and not _alsa_device_listed(device, output, probes):
            return Check("microphone", False, "configured ALSA capture device was not found")
        return Check("microphone", True, f"ALSA device {device} is available")

    if input_format in {"pulse", "pulseaudio"}:
        if not probes.command_exists("pactl"):
            return Check("microphone", False, "pactl is not installed")
        result = probes.run(("pactl", "list", "short", "sources"))
        if result.returncode != 0 or not result.output.strip():
            return Check("microphone", False, "PulseAudio has no available source")
        if device != "default" and device not in result.output:
            return Check("microphone", False, "configured PulseAudio source was not found")
        return Check("microphone", True, f"PulseAudio device {device} is available")

    return Check(
        "microphone",
        False,
        f"format {input_format or '(empty)'} has no non-recording diagnostic",
    )


def _network_check(probes: Probes) -> Check:
    """Check local route configuration without contacting the internet."""
    if not probes.command_exists("ip"):
        return Check("network configuration", False, "ip command is not installed")
    result = probes.run(("ip", "route", "show", "default"))
    configured = result.returncode == 0 and bool(result.output.strip())
    return Check(
        "network configuration",
        configured,
        "default route configured" if configured else "no default route configured",
    )


def _alsa_device_listed(device: str, hardware_output: str, probes: Probes) -> bool:
    """Match common ``hw:card,device`` names without opening the microphone."""
    short_name = device.lower().removeprefix("plug")
    if short_name.startswith("hw:"):
        card_and_device = short_name.removeprefix("hw:").split(",", maxsplit=1)
        if len(card_and_device) == 2 and all(part.isdigit() for part in card_and_device):
            card, capture_device = card_and_device
            return (
                f"card {card}:" in hardware_output
                and f"device {capture_device}:" in hardware_output
            )

    aliases = probes.run(("arecord", "-L"))
    if aliases.returncode != 0:
        return False
    alias_names = {
        line.strip().lower()
        for line in aliases.output.splitlines()
        if line and not line[0].isspace()
    }
    return device.lower() in alias_names


def _gpio_checks(settings: Settings, probes: Probes) -> tuple[Check, Check]:
    if not settings.gpio_enabled:
        disabled = Check("GPIO availability", True, "disabled by configuration")
        configuration = Check("button/LED configuration", True, "disabled by configuration")
        return disabled, configuration

    module_available = probes.module_available("gpiozero")
    chip_available = probes.gpio_chip_available()
    if not module_available:
        availability = Check(
            "GPIO availability",
            False,
            "gpiozero is not installed (expected on a non-Raspberry Pi)",
        )
    elif not chip_available:
        availability = Check(
            "GPIO availability",
            False,
            "no GPIO chip detected (expected on a non-Raspberry Pi)",
        )
    else:
        availability = Check("GPIO availability", True, "gpiozero and a GPIO chip are available")

    pins_valid = (
        0 <= settings.gpio_button_pin <= 27
        and 0 <= settings.gpio_led_pin <= 27
        and settings.gpio_button_pin != settings.gpio_led_pin
    )
    detail = (
        f"button GPIO{settings.gpio_button_pin}, LED GPIO{settings.gpio_led_pin}"
        if pins_valid
        else "button and LED need distinct BCM pins from 0 to 27"
    )
    return availability, Check("button/LED configuration", pins_valid, detail)


def _oled_checks(settings: Settings, probes: Probes) -> tuple[Check, Check]:
    if not settings.display_enabled:
        disabled = Check("I2C device/address", True, "display disabled by configuration")
        oled = Check("OLED availability", True, "disabled by configuration")
        return disabled, oled

    address = settings.oled_i2c_address
    if not 0x03 <= address <= 0x77:
        invalid = Check("I2C device/address", False, "address must be between 0x03 and 0x77")
        return invalid, Check("OLED availability", False, "invalid I2C address")

    buses = probes.i2c_buses()
    if not buses:
        i2c = Check(
            "I2C device/address",
            False,
            "no /dev/i2c-* bus detected (expected on a non-Raspberry Pi)",
        )
    elif not probes.command_exists("i2cdetect"):
        i2c = Check(
            "I2C device/address",
            False,
            f"bus available; install i2c-tools to verify 0x{address:02x}",
        )
    else:
        found = any(_i2c_address_present(probes, bus, address) for bus in buses)
        i2c = Check(
            "I2C device/address",
            found,
            f"0x{address:02x} {'detected' if found else 'not detected'}",
        )

    library_available = probes.module_available("luma.oled.device") or probes.module_available(
        "adafruit_ssd1306"
    )
    oled_ok = i2c.ok and library_available
    detail = "OLED Python library is not installed" if not library_available else i2c.detail
    return i2c, Check("OLED availability", oled_ok, detail)


def _i2c_address_present(probes: Probes, bus: int, address: int) -> bool:
    token = f"{address:02x}"
    result = probes.run(("i2cdetect", "-y", str(bus), token, token))
    if result.returncode != 0:
        return False
    words = result.output.lower().replace(":", " ").split()
    return token in words or "uu" in words


def _database_status(path: Path) -> list[Check]:
    if not path.is_file():
        unavailable = "database unavailable"
        return [
            Check("database", False, "missing"),
            Check("latest lesson", False, unavailable),
            Check("latest recap", False, unavailable),
            Check("Telegram users", False, unavailable),
        ]

    try:
        with closing(_readonly_connection(path)) as connection:
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            lesson = connection.execute(
                "SELECT lesson_date FROM lessons ORDER BY lesson_date DESC, created_at DESC LIMIT 1"
            ).fetchone()
            recap = connection.execute(
                "SELECT recap_date FROM recaps ORDER BY recap_date DESC, created_at DESC LIMIT 1"
            ).fetchone()
            active_users = int(
                connection.execute("SELECT COUNT(*) FROM users WHERE is_active = 1").fetchone()[0]
            )
    except sqlite3.Error as error:
        return [
            Check("database", False, f"unavailable: {error}"),
            Check("latest lesson", False, "database unavailable"),
            Check("latest recap", False, "database unavailable"),
            Check("Telegram users", False, "database unavailable"),
        ]

    integrity_ok = integrity == ("ok",)
    return [
        Check("database", integrity_ok, "ok" if integrity_ok else "quick_check failed"),
        Check("latest lesson", True, str(lesson[0]) if lesson is not None else "none"),
        Check("latest recap", True, str(recap[0]) if recap is not None else "none"),
        Check("Telegram users", True, f"{active_users} active"),
    ]


def _device_status_check(path: Path) -> Check:
    snapshot = read_device_status(path)
    if snapshot is None:
        return Check("device status", False, "not published")
    return Check("device status", snapshot.status is not Status.ERROR, snapshot.status.value)


def _inbox_status(path: Path) -> Check:
    if not path.is_dir():
        return Check("inbox", False, "missing")
    try:
        count = sum(
            1
            for candidate in path.iterdir()
            if candidate.is_file() and candidate.suffix.lower() in audio.KNOWN_SUFFIXES
        )
    except OSError as error:
        return Check("inbox", False, f"unavailable: {error}")
    return Check("inbox", True, f"{count} audio file(s)")
