"""Offline checks for gesture recognition, routing and mode isolation."""

from __future__ import annotations

import sqlite3
import subprocess
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest

from picatchup import audio, bot, cli, db, latest, qa, users
from picatchup.config import Settings, load_settings
from picatchup.device import DeviceController
from picatchup.display import NoOpDisplay, frame_for
from picatchup.domain import Update
from picatchup.errors import ConfigError
from picatchup.pisugar import ClickSequence
from picatchup.status import DeviceStatus, Status, StatusFile


class LED:
    def show_status(self, status: Status) -> None:
        pass

    def close(self) -> None:
        pass


def wait_for(check: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 2
    while not check() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert check()


@pytest.mark.parametrize(
    "count,expected", [(1, ["press"]), (2, ["press", "press"]), (3, ["switch"]), (4, ["press"] * 4)]
)
def test_click_sequences_preserve_short_presses(count: int, expected: list[str]) -> None:
    events: list[str] = []
    clicks = ClickSequence(lambda: events.append("press"), lambda: events.append("switch"))
    for n in range(count):
        clicks.release(n * 0.15)
        clicks.flush(n * 0.15 + 0.1)
        assert events == []
    clicks.flush(2)
    clicks.flush(3)
    assert events == expected


def test_slow_clicks_do_not_switch_mode() -> None:
    events: list[str] = []
    clicks = ClickSequence(lambda: events.append("press"), lambda: events.append("switch"))
    for n in range(3):
        clicks.release(n)
        clicks.flush(n + 0.5)
    assert events == ["press"] * 3


def test_conversation_settings_never_fall_back_to_lesson_bot(settings: Settings) -> None:
    conversation = settings.for_mode("conversation")
    assert conversation.telegram_bot_token == ""
    assert conversation.db_path != settings.db_path
    assert conversation.inbox_dir.parent == settings.data_dir / "conversation"
    assert conversation.backup_remote == ""
    assert conversation.for_mode("conversation") == conversation


def test_same_bot_is_rejected(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setenv("CONVERSATION_TELEGRAM_BOT_TOKEN", settings.telegram_bot_token)
    with pytest.raises(ConfigError, match="separate bot"):
        load_settings()


def test_cli_allowlists_are_independent(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["init"]) == 0
    assert cli.main(["--mode", "conversation", "init"]) == 0
    assert cli.main(["user", "add", "111", "Lesson person"]) == 0
    assert cli.main(["--mode", "conversation", "user", "add", "222", "Conversation person"]) == 0
    capsys.readouterr()
    assert cli.main(["--mode", "conversation", "user", "list"]) == 0
    output = capsys.readouterr().out
    assert "222" in output and "111" not in output
    assert cli.main(["--mode", "conversation", "user", "remove", "222"]) == 0
    with closing(db.connect(settings.db_path)) as conn:
        assert len(users.recipients(conn)) == 1


def test_bot_authorization_offsets_and_search_are_isolated(
    settings: Settings,
    conn: sqlite3.Connection,
) -> None:
    users.add(conn, 111, "Lesson person")
    conversation = settings.for_mode("conversation")
    sent: list[str] = []

    def sender(text: str, chat_id: int) -> list[int]:
        sent.append(text)
        return [1]

    def answerer(*args: object) -> str:
        pytest.fail("unauthorized text reached the answerer")

    with closing(db.connect(conversation.db_path)) as other:
        db.create_tables(other)
        users.add(other, 222, "Conversation person")
        for connection, config, user in [(other, conversation, 111), (conn, settings, 222)]:
            bot.handle_update(
                connection,
                config,
                Update(1, user, user, "private", 1, "secret"),
                sender,
                lambda _: None,
                answerer,
            )
        assert sent == [bot.UNAUTHORIZED_TEXT] * 2
        bot.handle_update(
            other,
            conversation,
            Update(2, 222, 222, "private", 2, "/start"),
            sender,
            lambda _: None,
            answerer,
        )
        assert "conversazioni" in sent[-1] and "lezioni" not in sent[-1]
        bot._save_offset(other, 200)
        assert bot._load_offset(conn) == 0
        assert "conversazioni" in qa.answer_question(other, conversation, "decisioni").text


@pytest.fixture
def offline_device(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("picatchup.device._ready_detail", lambda _: "Mic: OK WiFi: OK")
    monkeypatch.setattr("picatchup.device.audio.check_input_device", lambda *_: "OK")
    monkeypatch.setattr("picatchup.device.PiSugarBattery.read_status", lambda _: (80, False))


@pytest.mark.parametrize(
    "token,detail", [("", "Configura bot conv."), ("conversation-token", "Whitelist vuota")]
)
def test_mode_switch_and_missing_configuration_fail_closed(
    settings: Settings,
    offline_device: None,
    token: str,
    detail: str,
) -> None:
    settings = replace(settings, conversation_bot_token=token)
    controller = DeviceController(settings, led=LED(), display=NoOpDisplay())
    controller.start()
    try:
        controller.switch_mode()
        wait_for(lambda: controller.snapshot.mode == "conversation")
        assert frame_for(controller.snapshot).line1 == "CONVERSAZIONE"
        controller.press()
        wait_for(lambda: controller.snapshot.status is Status.ERROR)
        assert controller.snapshot.detail == detail
        assert not controller.is_recording
        controller.switch_mode()
        wait_for(lambda: controller.snapshot.mode == "lesson")
    finally:
        controller.close()


def test_conversation_recording_routes_pipeline_and_ignores_busy_switches(
    settings: Settings,
    conn: sqlite3.Connection,
    offline_device: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(settings, conversation_bot_token="conversation-token")
    conversation = settings.for_mode("conversation")
    users.add(conn, 111, "Lesson person")
    with closing(db.connect(conversation.db_path)) as other:
        db.create_tables(other)
        users.add(other, 222, "Conversation person")
    processing = Event()
    resume = Event()
    recorded: list[Path] = []
    delivered: list[tuple[str, int]] = []
    prompts: list[str] = []

    def recorder(destination: Path, **options: object) -> Path:
        recorded.append(destination)
        stop = options["stop_event"]
        assert isinstance(stop, Event) and stop.wait(2)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"conversation audio")
        return destination

    def transcribe(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[3:6] == ["--mode", "conversation", "transcribe"]
        source = Path(command[6])
        assert source.parent == conversation.inbox_dir
        with closing(db.connect(conversation.db_path)) as other, other:
            other.execute(
                "INSERT INTO lessons (id, lesson_date, subject, audio_sha256, text, "
                "created_at) VALUES ('conversation', '2026-09-27', 'Conversazione', "
                "?, 'Discutiamo il progetto.', '2026-09-27T10:00:00')",
                (audio.sha256_of(source),),
            )
        processing.set()
        assert resume.wait(2)
        return subprocess.CompletedProcess(command, 0, "", "")

    def generate(prompt: str, **kwargs: object) -> str:
        prompts.append(prompt)
        return "Decisione: procedere."

    def send(*args: object, token: str, chat_id: int, **kwargs: object) -> int:
        delivered.append((token, chat_id))
        return 1

    monkeypatch.setattr("picatchup.device.subprocess.run", transcribe)
    monkeypatch.setattr(latest.groq_text, "generate", generate)
    monkeypatch.setattr(latest.telegram, "send_message", send)
    monkeypatch.setattr(latest.telegram, "send_document", send)
    monkeypatch.setattr(latest, "_send_pdf_to_nas", lambda _: pytest.fail("lesson NAS called"))
    controller = DeviceController(settings, recorder=recorder, led=LED(), display=NoOpDisplay())
    controller.start()
    try:
        controller.switch_mode()
        wait_for(lambda: controller.snapshot.mode == "conversation")
        controller.press()
        wait_for(lambda: controller.is_recording)
        controller.switch_mode()
        assert controller.snapshot.mode == "conversation"
        controller.press()
        assert processing.wait(2)
        controller.switch_mode()
        resume.set()
        wait_for(lambda: controller.snapshot.status is Status.SUCCESS)
        assert controller.snapshot.mode == "conversation"
        assert delivered == [("conversation-token", 222)] * 2
        assert "due o più persone" in prompts[0]
        assert recorded[0].exists()
        assert conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0] == 0
        with closing(db.connect(conversation.db_path)) as other:
            assert bot._latest_recap(other) == "Decisione: procedere."
        controller.switch_mode()
        wait_for(lambda: controller.snapshot.mode == "lesson")
    finally:
        resume.set()
        controller.close()


def test_mode_survives_status_serialization() -> None:
    snapshot = DeviceStatus(Status.RECORDING, mode="conversation", elapsed_seconds=42)
    restored = DeviceStatus.from_dict(snapshot.to_dict())
    assert restored == snapshot
    assert frame_for(restored).line2 == "Conversazione"
    assert DeviceStatus.from_dict({"status": "ready"}).mode == "lesson"


def test_external_lesson_status_does_not_replace_conversation_interface(
    settings: Settings,
    offline_device: None,
) -> None:
    status_file = StatusFile(settings.status_file)
    controller = DeviceController(settings, led=LED(), display=NoOpDisplay())
    controller.start()
    try:
        controller.switch_mode()
        wait_for(lambda: controller.snapshot.mode == "conversation")
        status_file.publish(Status.SUMMARIZING)
        assert controller._sync_external_status() is None
        assert controller.snapshot.mode == "conversation"
    finally:
        controller.close()


@pytest.mark.parametrize(
    "samples,expected",
    [
        ([(0.0, False), (0.1, True), (0.2, False), (0.7, False)], ["press"]),
        (
            [
                (0.0, False),
                (0.1, True),
                (0.2, False),
                (0.3, True),
                (0.4, False),
                (0.5, True),
                (0.6, False),
                (1.1, False),
            ],
            ["switch"],
        ),
        ([(0.0, False), (0.1, True), (2.0, False), (2.5, False)], []),
    ],
)
def test_pisugar_polling_routes_gestures_without_claiming_long_hold(
    monkeypatch: pytest.MonkeyPatch,
    samples: list[tuple[float, bool]],
    expected: list[str],
) -> None:
    import sys
    from types import SimpleNamespace

    from picatchup import pisugar

    events: list[str] = []

    class Bus:
        index = -1
        closed = False

        def read_byte_data(self, address: int, register: int) -> int:
            assert (address, register) == (0x57, 0x02)
            self.index += 1
            return int(samples[self.index][1])

        def close(self) -> None:
            self.closed = True

    bus = Bus()
    monkeypatch.setitem(sys.modules, "smbus", SimpleNamespace(SMBus=lambda _: bus))
    monkeypatch.setattr(pisugar, "time", SimpleNamespace(monotonic=lambda: samples[bus.index][0]))
    button = pisugar.PiSugarButton(
        lambda: events.append("press"), switch_mode=lambda: events.append("switch")
    )
    button._stop = SimpleNamespace(
        is_set=lambda: bus.index == len(samples) - 1, wait=lambda _: None
    )
    button._run()
    assert events == expected
    assert bus.closed


@pytest.mark.parametrize("mode", [[], {}, 1, None, "unknown"])
def test_malformed_mode_is_rejected(mode: object) -> None:
    with pytest.raises(ValueError, match="invalid device mode"):
        DeviceStatus.from_dict({"status": "ready", "mode": mode})
