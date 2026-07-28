"""Direct HTTP integration with the documented Strava API."""

from app.integrations.strava.client import StravaClient
from app.integrations.strava.exceptions import (
    StravaAuthenticationError,
    StravaProviderError,
    StravaRateLimitedError,
    StravaResponseError,
    StravaUnavailableError,
)

__all__ = [
    "StravaAuthenticationError",
    "StravaClient",
    "StravaProviderError",
    "StravaRateLimitedError",
    "StravaResponseError",
    "StravaUnavailableError",
]
