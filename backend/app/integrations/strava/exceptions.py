"""Safe, narrowly scoped Strava integration exceptions."""

from __future__ import annotations

from app.schemas.strava import StravaRateLimits


class StravaProviderError(RuntimeError):
    """Base provider failure that never embeds an upstream response body."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class StravaAuthenticationError(StravaProviderError):
    """Access or refresh credentials were rejected."""

    def __init__(self) -> None:
        super().__init__(
            "strava_authentication_failed",
            "Strava authentication failed.",
        )


class StravaRateLimitedError(StravaProviderError):
    """Strava rejected a request because a quota was exhausted."""

    def __init__(
        self,
        *,
        rate_limits: StravaRateLimits,
        retry_after_seconds: int | None,
    ) -> None:
        super().__init__("strava_rate_limited", "Strava rate limit reached.")
        self.rate_limits = rate_limits
        self.retry_after_seconds = retry_after_seconds


class StravaResponseError(StravaProviderError):
    """Strava returned a malformed or unsupported payload."""

    def __init__(self) -> None:
        super().__init__(
            "strava_invalid_response",
            "Strava returned an invalid response.",
        )


class StravaUnavailableError(StravaProviderError):
    """A network failure or retryable upstream response occurred."""

    def __init__(self, *, status_code: int | None = None) -> None:
        super().__init__(
            "strava_unavailable",
            "Strava is temporarily unavailable.",
        )
        self.status_code = status_code
