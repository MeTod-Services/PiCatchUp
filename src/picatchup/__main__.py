"""Lets you run the CLI with `python -m picatchup` as well as `picatchup`."""

from __future__ import annotations

from picatchup.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
