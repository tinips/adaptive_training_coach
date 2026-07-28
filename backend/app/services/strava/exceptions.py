"""Safe application-level Strava exceptions."""

from __future__ import annotations

from datetime import datetime


class StravaServiceError(RuntimeError):
    """Base service failure with a stable UI-safe error code."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class OAuthStateRejectedError(StravaServiceError):
    """OAuth state was missing, expired, consumed, or did not belong to the user."""

    def __init__(self) -> None:
        super().__init__(
            "oauth_state_rejected",
            "The Strava connection link is invalid or has expired.",
        )


class OAuthAuthorizationDeniedError(StravaServiceError):
    """The athlete denied provider authorization."""

    def __init__(self) -> None:
        super().__init__(
            "oauth_authorization_denied",
            "Strava authorization was not granted.",
        )


class OAuthScopeError(StravaServiceError):
    """The accepted scope cannot support activity synchronization."""

    def __init__(self, missing_scopes: frozenset[str]) -> None:
        super().__init__(
            "oauth_insufficient_scope",
            "Required Strava permissions were not granted.",
        )
        self.missing_scopes = missing_scopes


class StravaNotConnectedError(StravaServiceError):
    """No active connection exists for a user."""

    def __init__(self) -> None:
        super().__init__("strava_not_connected", "Strava is not connected.")


class StravaTokenRotationError(StravaServiceError):
    """Credentials could not be refreshed or atomically persisted."""

    def __init__(self) -> None:
        super().__init__(
            "strava_token_rotation_failed",
            "Strava credentials could not be refreshed.",
        )


class ConcurrentSyncError(StravaServiceError):
    """A sync job already owns the user's synchronization slot."""

    def __init__(self) -> None:
        super().__init__(
            "strava_sync_in_progress",
            "A Strava synchronization is already in progress.",
        )


class SyncCooldownError(StravaServiceError):
    """Manual synchronization was requested before the cooldown elapsed."""

    def __init__(self, retry_at: datetime) -> None:
        super().__init__(
            "strava_sync_cooldown",
            "Strava was synchronized recently.",
        )
        self.retry_at = retry_at


class DisconnectConfirmationRequiredError(StravaServiceError):
    """Disconnect was requested without an explicit confirmation."""

    def __init__(self) -> None:
        super().__init__(
            "strava_disconnect_confirmation_required",
            "Disconnect confirmation is required.",
        )


class WebhookVerificationError(StravaServiceError):
    """Webhook verification parameters did not exactly match configuration."""

    def __init__(self) -> None:
        super().__init__(
            "strava_webhook_verification_failed",
            "Webhook verification failed.",
        )
