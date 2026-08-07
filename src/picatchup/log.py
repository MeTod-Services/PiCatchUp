"""Logging, with one safety net: API keys never reach the output.

Nothing here formats lesson content. The rule from PLAN.md still applies and no
filter can enforce it for you: log what happened, never what was said.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable


def setup_logging(level: str = "INFO", secrets: Iterable[str] = ()) -> None:
    """Send readable logs to stderr, hiding the given secret values."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(_HideSecrets(secrets))
    logging.basicConfig(level=level, handlers=[handler], force=True)


class _HideSecrets(logging.Filter):
    """Replace known secret values with *** in every log line."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.secrets:
            return True
        # Build the final message first, then clear the arguments. Replacing
        # inside a "key=%s" template would leave %s unfilled, and logging's
        # fallback for that prints the raw argument to stderr — which is the
        # exact thing we are preventing.
        message = record.getMessage()
        for secret in self.secrets:
            message = message.replace(secret, "***")
        record.msg = message
        record.args = ()
        return True
