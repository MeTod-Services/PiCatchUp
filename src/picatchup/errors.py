"""Application exception hierarchy.

Workers use ``retryable`` rather than parsing provider or database error messages.
Public error text must remain safe to log and must not contain configuration values.
"""

from __future__ import annotations


class PiCatchUpError(Exception):
    """Base class for expected application failures."""

    code = "picatchup_error"
    retryable = False

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message} Hint: {self.hint}"
        return self.message


class FatalError(PiCatchUpError):
    """An expected failure that retries cannot repair."""

    code = "fatal_error"


class RetryableError(PiCatchUpError):
    """A transient failure that may succeed when retried."""

    code = "retryable_error"
    retryable = True


class ConfigurationError(FatalError):
    """Configuration is absent, malformed, or unsafe."""

    code = "configuration_error"


class ValidationError(FatalError):
    """Untrusted input failed structural or semantic validation."""

    code = "validation_error"


class AuthorizationError(FatalError):
    """The caller is not authorized for the requested operation."""

    code = "authorization_error"


class NotFoundError(FatalError):
    """The requested entity does not exist in the caller's scope."""

    code = "not_found"


class ConflictError(FatalError):
    """The operation conflicts with current persistent state."""

    code = "conflict"


class ProviderError(RetryableError):
    """An external provider failed transiently."""

    code = "provider_error"


class FatalProviderError(FatalError):
    """A provider rejected a request which retries cannot repair (for example 4xx)."""

    code = "fatal_provider_error"


class ProviderResponseError(FatalProviderError):
    """A provider returned a response that violates its contract."""

    code = "provider_response_error"


class StorageError(RetryableError):
    """Persistent storage is temporarily unavailable."""

    code = "storage_error"


class FatalStorageError(FatalError):
    """Persistent storage is corrupt or permanently unavailable."""

    code = "fatal_storage_error"


__all__ = [
    "AuthorizationError",
    "ConfigurationError",
    "ConflictError",
    "FatalError",
    "FatalProviderError",
    "FatalStorageError",
    "NotFoundError",
    "PiCatchUpError",
    "ProviderError",
    "ProviderResponseError",
    "RetryableError",
    "StorageError",
    "ValidationError",
]
