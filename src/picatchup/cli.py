"""The `picatchup` command.

This file only reads arguments and prints results. The actual work belongs in
the pipeline modules, so each new command here stays a handful of lines.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Callable, Sequence
from contextlib import closing
from datetime import datetime
from pathlib import Path

from picatchup import (
    __version__,
    audio,
    backup,
    bot,
    buttons,
    daily,
    db,
    deliver,
    device,
    diagnostics,
    recap,
    transcribe,
    users,
)
from picatchup.config import Settings, load_settings, require
from picatchup.errors import PiCatchUpError
from picatchup.log import setup_logging
from picatchup.providers import telegram
from picatchup.util import today


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        base_settings = load_settings()
        settings = base_settings.for_mode(args.mode)
        setup_logging(settings.log_level, base_settings.secrets)
        handler = HANDLERS[args.command]
        return handler(settings, args)
    except PiCatchUpError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="picatchup", description="Catch up on missed lessons")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--mode",
        choices=("lesson", "conversation"),
        default="lesson",
        help="select separate data, bot and allowlist (default: lesson)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="create the data folders and the database")
    commands.add_parser("doctor", help="run offline installation and hardware diagnostics")
    commands.add_parser("status", help="show concise local device and database status")
    commands.add_parser("backup", help="create and rotate a safe SQLite backup")
    commands.add_parser("bot", help="run the allowlisted Telegram long-polling bot")

    record = commands.add_parser("record", help="record the microphone to an mp3 in the inbox")
    record.add_argument("--subject", help="the subject, e.g. Matematica")
    record.add_argument("--minutes", type=float, help="stop by itself after this long")
    record.add_argument("--date", type=a_date, help="the school day, default today")

    commands.add_parser("buttons", help="record with the legacy GPIO button listener")
    commands.add_parser("device", help="run the button, OLED and recording LED controller")

    transcribe_cmd = commands.add_parser("transcribe", help="turn one audio file into text")
    transcribe_cmd.add_argument("file", type=Path)
    transcribe_cmd.add_argument("--date", type=a_date, help="the school day, default from the name")
    transcribe_cmd.add_argument("--subject")

    recap_cmd = commands.add_parser("recap", help="write the summary of one day")
    recap_cmd.add_argument("date", nargs="?", type=a_date, help="default today")
    recap_cmd.add_argument("--force", action="store_true", help="rewrite one that already exists")

    send = commands.add_parser("send", help="send a day's recap on Telegram")
    send.add_argument("date", nargs="?", type=a_date, help="default today")
    send.add_argument("--force", action="store_true", help="send again to whoever already had it")

    daily_cmd = commands.add_parser("daily", help="inbox to Telegram, the whole thing (for cron)")
    daily_cmd.add_argument("--date", type=a_date, help="default today")
    daily_cmd.add_argument("--force", action="store_true")

    user = commands.add_parser("user", help="who may receive the recap")
    actions = user.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add", help="add someone, or bring them back")
    add.add_argument("telegram_id", type=int)
    add.add_argument("name")
    add.add_argument("--admin", action="store_true")
    actions.add_parser("list", help="show everyone")
    remove = actions.add_parser(
        "remove", help="delete a local allowlist entry and local delivery history"
    )
    remove.add_argument("telegram_id", type=int)
    actions.add_parser("ids", help="show the Telegram ids of whoever wrote to the bot")

    return parser


def a_date(value: str) -> str:
    """A YYYY-MM-DD argument, checked here so nothing downstream has to."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a date like 2026-09-15") from None


def cmd_init(settings: Settings, args: argparse.Namespace) -> int:
    """Set up an empty installation. Running it again changes nothing."""
    for folder in (settings.inbox_dir, settings.audio_dir, settings.backup_dir):
        folder.mkdir(parents=True, exist_ok=True)

    with closing(db.connect(settings.db_path)) as conn:
        db.create_tables(conn)

    print(f"database  {settings.db_path}")
    print(f"drop your recordings in  {settings.inbox_dir}")
    return 0


def cmd_doctor(settings: Settings, args: argparse.Namespace) -> int:
    checks = diagnostics.doctor(settings)
    print("PiCatchUp doctor\n")
    for check in checks:
        marker = "OK" if check.ok else "FAIL"
        print(f"[{marker:4}] {check.name}: {check.detail}")
    return 0 if all(check.ok for check in checks) else 1


def cmd_status(settings: Settings, args: argparse.Namespace) -> int:
    checks = diagnostics.status(settings)
    print("PiCatchUp\n")
    for check in checks:
        marker = "OK" if check.ok else "--"
        print(f"{check.name}: {marker} — {check.detail}")
    database = next((check for check in checks if check.name == "database"), None)
    return 0 if database is not None and database.ok else 1


def cmd_backup(settings: Settings, args: argparse.Namespace) -> int:
    path = backup.create_backup(settings)
    print(f"backup  {path}")
    return 0


def cmd_bot(settings: Settings, args: argparse.Namespace) -> int:
    with closing(db.connect(settings.db_path)) as conn:
        db.create_tables(conn)
        bot.run(conn, settings)
    return 0


def cmd_record(settings: Settings, args: argparse.Namespace) -> int:
    """Record until Ctrl-C. The file lands in the inbox, ready for `daily`."""
    lesson_date = args.date or today(settings.timezone)
    name = f"{lesson_date}-{datetime.now().strftime('%H%M%S')}"
    if args.subject:
        name = f"{name}-{args.subject.replace(' ', '_')}"
    destination = settings.inbox_dir / f"{name}.mp3"
    sequence = 2
    while destination.exists():
        destination = settings.inbox_dir / f"{name}-{sequence}.mp3"
        sequence += 1

    print(f"recording to {destination.name} — press Ctrl-C to stop")
    audio.record(
        destination,
        input_format=settings.audio_input_format,
        device=settings.audio_input_device,
        bitrate=settings.audio_bitrate,
        minutes=args.minutes,
    )
    size_mb = destination.stat().st_size / 1_000_000
    print(f"saved {destination} ({size_mb:.1f} MB)")
    print("next:  picatchup daily")
    return 0


def cmd_buttons(settings: Settings, args: argparse.Namespace) -> int:
    """Wait for GPIO presses until the service or the user stops us."""
    buttons.listen(settings)
    return 0


def cmd_device(settings: Settings, args: argparse.Namespace) -> int:
    device.listen(settings)
    return 0


def cmd_transcribe(settings: Settings, args: argparse.Namespace) -> int:
    with closing(db.connect(settings.db_path)) as conn:
        lesson = transcribe.transcribe_file(
            conn, settings, args.file, lesson_date=args.date, subject=args.subject
        )
    print(f"lesson    {lesson.lesson_date}  {lesson.subject or 'subject not given'}")
    print(f"duration  {lesson.duration_ms // 60000} minutes, {lesson.chunks} chunks")
    print()
    print(lesson.text)
    return 0


def cmd_recap(settings: Settings, args: argparse.Namespace) -> int:
    recap_date = args.date or today(settings.timezone)
    with closing(db.connect(settings.db_path)) as conn:
        written = recap.make_recap(conn, settings, recap_date, force=args.force)
    if written.reused:
        print("(already written — use --force to write it again)\n")
    print(written.text)
    return 0


def cmd_send(settings: Settings, args: argparse.Namespace) -> int:
    recap_date = args.date or today(settings.timezone)
    with closing(db.connect(settings.db_path)) as conn:
        result = deliver.send_recap(conn, settings, recap_date, force=args.force)
    return report_delivery(result)


def cmd_daily(settings: Settings, args: argparse.Namespace) -> int:
    with closing(db.connect(settings.db_path)) as conn:
        report = daily.run(conn, settings, recap_date=args.date, force=args.force)

    for name in report.transcribed:
        print(f"transcribed  {name}")
    for name in report.skipped:
        print(f"already had  {name}")
    for processed_date in report.processed_dates:
        print(f"processed    {processed_date}")
    for name, reason in report.failed:
        print(f"failed       {name}: {reason}", file=sys.stderr)
    if report.purged_audio:
        print(f"deleted      {report.purged_audio} recording(s) past their retention")
    if report.note:
        print(report.note)
        return 0 if not report.failed else 1

    if report.delivered is not None and report_delivery(report.delivered) == 1:
        return 1
    return 1 if report.failed else 0


def cmd_user(settings: Settings, args: argparse.Namespace) -> int:
    if args.action == "ids":
        token = require(settings.telegram_bot_token, "TELEGRAM_BOT_TOKEN")
        chats = telegram.recent_chats(token=token)
        if not chats:
            print("nobody has written to the bot recently.")
            print("open Telegram, send it a message, then run this again.")
            return 0
        for chat_id, name in chats:
            print(f"{chat_id}  {name}")
        return 0

    with closing(db.connect(settings.db_path)) as conn:
        if args.action == "add":
            users.add(conn, args.telegram_id, args.name, is_admin=args.admin)
            print(f"{args.name} will receive the recap")
            return 0
        if args.action == "remove":
            if not users.remove(conn, args.telegram_id):
                print(f"there is nobody with the id {args.telegram_id}", file=sys.stderr)
                return 1
            print(
                f"{args.telegram_id} removed from local access and local delivery history; "
                "messages already on Telegram are unchanged"
            )
            return 0
        return print_users(users.listing(conn))


def print_users(rows: Sequence[sqlite3.Row]) -> int:
    if not rows:
        print("nobody yet. Add someone: picatchup user add <telegram id> <name>")
        return 0
    for row in rows:
        labels = "".join(
            [" (admin)" if row["is_admin"] else "", " (inactive)" if not row["is_active"] else ""]
        )
        print(f"{row['telegram_user_id']}  {row['display_name']}{labels}")
    return 0


def report_delivery(result: deliver.Delivered) -> int:
    for name in result.sent_to:
        print(f"sent to      {name}")
    for name in result.already_had_it:
        print(f"already had  {name}")
    for name in result.failed:
        print(f"failed       {name}", file=sys.stderr)
    return 1 if result.failed else 0


HANDLERS: dict[str, Callable[[Settings, argparse.Namespace], int]] = {
    "init": cmd_init,
    "doctor": cmd_doctor,
    "status": cmd_status,
    "backup": cmd_backup,
    "bot": cmd_bot,
    "record": cmd_record,
    "buttons": cmd_buttons,
    "device": cmd_device,
    "transcribe": cmd_transcribe,
    "recap": cmd_recap,
    "send": cmd_send,
    "daily": cmd_daily,
    "user": cmd_user,
}
