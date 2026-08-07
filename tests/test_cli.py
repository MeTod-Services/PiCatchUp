from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from picatchup.cli import main


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


def test_the_database_is_usable_after_init(isolated_data_dir: Path) -> None:
    main(["init"])

    conn = sqlite3.connect(isolated_data_dir / "picatchup.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0] == 0
    finally:
        conn.close()
