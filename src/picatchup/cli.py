"""The `picatchup` command.

This file only reads arguments and prints results. The actual work belongs in
the pipeline modules, so each new command here stays a handful of lines.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from picatchup import __version__, db
from picatchup.config import Settings, load_settings
from picatchup.errors import PiCatchUpError
from picatchup.log import setup_logging


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="picatchup", description="Catch up on missed lessons")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="create the data folders and the database")

    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        setup_logging(settings.log_level, settings.secrets)
        if args.command == "init":
            return cmd_init(settings)
    except PiCatchUpError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    raise AssertionError(f"command {args.command!r} has no handler")


def cmd_init(settings: Settings) -> int:
    """Set up an empty installation. Running it again changes nothing."""
    for folder in (settings.inbox_dir, settings.audio_dir, settings.backup_dir):
        folder.mkdir(parents=True, exist_ok=True)

    conn = db.connect(settings.db_path)
    try:
        db.create_tables(conn)
    finally:
        conn.close()

    print(f"database  {settings.db_path}")
    print(f"drop your recordings in  {settings.inbox_dir}")
    return 0
