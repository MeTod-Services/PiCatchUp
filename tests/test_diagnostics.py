from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from picatchup.config import Settings
from picatchup.diagnostics import Check, CommandResult, doctor, status
from picatchup.status import DeviceStatus, Status, StatusFile
from picatchup.util import new_id, utc_now


class FakeProbes:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def python_version(self) -> tuple[int, int, int]:
        return (3, 11, 9)

    def command_exists(self, command: str) -> bool:
        return command in {"ffmpeg", "ffprobe", "pactl", "arecord", "i2cdetect", "ip"}

    def run(self, command: Sequence[str]) -> CommandResult:
        invoked = tuple(command)
        self.commands.append(invoked)
        if invoked[0] == "pactl":
            return CommandResult(0, "1\tdefault\tmodule-alsa-card.c\n")
        if invoked[0] == "arecord":
            return CommandResult(0, "card 1: USB, device 0: USB Audio")
        if invoked[0] == "i2cdetect":
            return CommandResult(0, "30: -- -- -- -- -- -- -- -- -- -- -- -- 3c -- -- --")
        if invoked[0] == "ip":
            return CommandResult(0, "default via 192.0.2.1 dev wlan0")
        raise AssertionError(f"unexpected command: {invoked}")

    def module_available(self, module: str) -> bool:
        return module in {"gpiozero", "luma.oled.device"}

    def gpio_chip_available(self) -> bool:
        return True

    def i2c_buses(self) -> list[int]:
        return [1]


class NonRaspberryPiProbes(FakeProbes):
    def module_available(self, module: str) -> bool:
        return False

    def gpio_chip_available(self) -> bool:
        return False

    def i2c_buses(self) -> list[int]:
        return []


def _by_name(rows: list[Check]) -> dict[str, Check]:
    return {row.name: row for row in rows}


def test_doctor_covers_offline_dependencies_database_and_hardware(
    settings: Settings, conn: sqlite3.Connection
) -> None:
    probes = FakeProbes()

    rows = doctor(settings, probes=probes)
    checks = _by_name(rows)

    expected = {
        "Python >= 3.11",
        "ffmpeg",
        "ffprobe",
        "SQLite FTS5",
        "database",
        "database schema",
        "database integrity",
        "data directory",
        "inbox directory",
        "audio directory",
        "backup directory",
        "Groq configuration (offline)",
        "Telegram configuration (offline)",
        "microphone",
        "network configuration",
        "GPIO availability",
        "button/LED configuration",
        "I2C device/address",
        "OLED availability",
    }
    assert expected <= checks.keys()
    assert all(checks[name].ok for name in expected)
    assert {checks["Groq configuration (offline)"].detail} == {"yes"}
    assert {checks["Telegram configuration (offline)"].detail} == {"yes"}
    assert all(command[0] not in {"curl", "wget"} for command in probes.commands)

    rendered = "\n".join(f"{row.name}: {row.detail}" for row in rows)
    assert settings.groq_api_key not in rendered
    assert settings.telegram_bot_token not in rendered


def test_doctor_accepts_auto_when_exactly_one_alsa_microphone_exists(
    settings: Settings, conn: sqlite3.Connection
) -> None:
    alsa = replace(settings, audio_input_format="alsa", audio_input_device="auto")

    check = _by_name(doctor(alsa, probes=FakeProbes()))["microphone"]

    assert check.ok
    assert "auto" in check.detail


def test_doctor_degrades_clearly_on_a_non_raspberry_pi(
    settings: Settings, conn: sqlite3.Connection
) -> None:
    checks = _by_name(doctor(settings, probes=NonRaspberryPiProbes()))

    assert not checks["GPIO availability"].ok
    assert "non-Raspberry Pi" in checks["GPIO availability"].detail
    assert not checks["I2C device/address"].ok
    assert "non-Raspberry Pi" in checks["I2C device/address"].detail
    assert not checks["OLED availability"].ok


def test_status_is_concise_and_never_includes_lesson_or_recap_text(
    settings: Settings, conn: sqlite3.Connection
) -> None:
    conn.execute(
        "INSERT INTO lessons "
        "(id, lesson_date, subject, audio_sha256, text, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (new_id(), "2026-09-15", "Matematica", "sha", "private lesson words", utc_now()),
    )
    conn.execute(
        "INSERT INTO recaps (id, recap_date, text, model, created_at) VALUES (?, ?, ?, ?, ?)",
        (new_id(), "2026-09-15", "private recap words", "model", utc_now()),
    )
    conn.execute(
        "INSERT INTO users (id, telegram_user_id, display_name, created_at) VALUES (?, ?, ?, ?)",
        (new_id(), 123456, "Student", utc_now()),
    )
    conn.commit()
    settings.inbox_dir.joinpath("waiting.mp3").write_bytes(b"audio")
    settings.inbox_dir.joinpath("ignore.txt").write_text("not audio", encoding="utf-8")
    StatusFile(settings.status_file).publish(
        DeviceStatus(Status.READY, detail=settings.groq_api_key)
    )

    rows = status(settings, probes=FakeProbes())
    checks = _by_name(rows)

    assert checks["database"].ok
    assert checks["device status"] == Check("device status", True, "ready")
    assert checks["inbox"].detail == "1 audio file(s)"
    assert checks["latest lesson"].detail == "2026-09-15"
    assert checks["latest recap"].detail == "2026-09-15"
    assert checks["Telegram users"].detail == "1 active"
    assert checks["microphone"].ok
    assert checks["OLED"].ok
    assert checks["button"].ok
    assert checks["LED"].ok

    rendered = "\n".join(row.detail for row in rows)
    assert "private lesson words" not in rendered
    assert "private recap words" not in rendered
    assert settings.groq_api_key not in rendered
    assert settings.telegram_bot_token not in rendered


def test_status_reports_a_missing_database_without_creating_it(
    settings: Settings, tmp_path: Path
) -> None:
    missing = tmp_path / "does-not-exist.db"
    settings = replace(settings, db_path=missing)

    checks = _by_name(status(settings, probes=FakeProbes()))

    assert not checks["database"].ok
    assert checks["database"].detail == "missing"
    assert not missing.exists()
