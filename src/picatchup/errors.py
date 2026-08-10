"""Errors we raise on purpose.

The CLI catches `PiCatchUpError` and prints it as a plain message instead of a
traceback, so anything raised here should read like a sentence a person can act
on. Anything else crashing is a bug and should show its traceback.
"""

from __future__ import annotations


class PiCatchUpError(Exception):
    """Something went wrong in a way we anticipated."""


class ConfigError(PiCatchUpError):
    """A setting is missing or makes no sense."""


class AudioError(PiCatchUpError):
    """ffmpeg is missing, or it refused a file we handed it."""


class RaspberryPiError(PiCatchUpError):
    """GPIO support is missing, unavailable, or misconfigured."""


class ProviderError(PiCatchUpError):
    """The outside world (Groq, Telegram) said no, or could not be reached."""
