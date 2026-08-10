"""The outside world.

The only package allowed to open a network connection. Everything in here
takes ordinary values in and gives ordinary values back, so the rest of the
program can be read — and tested — without an internet connection.
"""

from __future__ import annotations
