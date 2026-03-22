"""Clockify API exception hierarchy."""


class ClockifyAPIError(Exception):
    """Base class for all Clockify API errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthError(ClockifyAPIError):
    """Raised when the API key is missing or invalid (HTTP 401/403)."""


class RateLimitError(ClockifyAPIError):
    """Raised when the API rate limit is exceeded (HTTP 429)."""


class NotFoundError(ClockifyAPIError):
    """Raised when a requested resource does not exist (HTTP 404)."""


class ServerError(ClockifyAPIError):
    """Raised for unexpected server-side errors (HTTP 5xx)."""
