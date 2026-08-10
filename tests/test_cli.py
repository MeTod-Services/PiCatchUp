from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from picatchup import cli
from picatchup.cli import main
from picatchup.diagnostics import Check


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run the CLI against a throwaway folder, never the developer's own data."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


def test_init_creates_the_database_and_the_folders(isolated_data_dir: Path) -> None:
    assert main(["init"]) == 0

    assert (isolated_data_dir / "picatchup.db").is_file()
    assert (isolated_data_dir / "inbox").is_dir()
    assert (isolated_data_dir / "audio").is_dir()
    assert (isolated_data_dir / "backups").is_dir()


def test_init_twice_is_harmless(isolated_data_dir: Path) -> None:
    assert main(["init"]) == 0
    assert main(["init"]) == 0


def test_init_needs_no_api_keys(isolated_data_dir: Path) -> None:
    """Setting up must work before anyone has signed up to Groq."""
    assert main(["init"]) == 0


def test_a_broken_env_file_prints_a_message_not_a_traceback(
    isolated_data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".env").write_text("this line is not a setting\n", encoding="utf-8")

    assert main(["init"]) == 1
    assert "expected KEY=value" in capsys.readouterr().err


def test_unknown_command_is_refused(isolated_data_dir: Path) -> None:
    with pytest.raises(SystemExit):
        main(["transcribe"])


def test_doctor_reports_checks_and_sets_a_failure_exit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli.diagnostics,
        "doctor",
        lambda settings: [Check("database", True, "ok"), Check("OLED", False, "missing")],
    )

    assert main(["doctor"]) == 1
    output = capsys.readouterr().out
    assert "[OK" in output and "[FAIL" in output


def test_status_succeeds_when_database_is_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli.diagnostics,
        "status",
        lambda settings: [Check("database", True, "ok"), Check("OLED", False, "disabled")],
    )

    assert main(["status"]) == 0


def test_backup_command_creates_a_snapshot(isolated_data_dir: Path) -> None:
    assert main(["init"]) == 0
    assert main(["backup"]) == 0
    assert len(list((isolated_data_dir / "backups").glob("picatchup-*.db"))) == 1


def test_manual_record_never_overwrites_a_same_second_name(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_record(destination: Path, **options: object) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"audio")
        return destination

    monkeypatch.setattr(cli.audio, "record", fake_record)

    assert main(["record", "--subject", "Matematica"]) == 0
    assert main(["record", "--subject", "Matematica"]) == 0
    recordings = list((isolated_data_dir / "inbox").glob("*.mp3"))
    assert len(recordings) == 2
    assert len({path.name for path in recordings}) == 2


def test_buttons_command_starts_the_gpio_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_listen(settings: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli.buttons, "listen", fake_listen)

    assert main(["buttons"]) == 0
    assert called


def test_device_command_starts_the_hardware_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_listen(settings: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli.device, "listen", fake_listen)

    assert main(["device"]) == 0
    assert called


def test_bot_command_starts_long_polling(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def fake_run(conn: sqlite3.Connection, settings: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli.bot, "run", fake_run)

    assert main(["bot"]) == 0
    assert called


def test_the_database_is_usable_after_init(isolated_data_dir: Path) -> None:
    main(["init"])

    conn = sqlite3.connect(isolated_data_dir / "picatchup.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0] == 0
    finally:
        conn.close()
