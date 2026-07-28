"""Async direct HTTP client for current documented Strava endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import httpx
from pydantic import SecretStr, TypeAdapter, ValidationError

from app.integrations.strava.exceptions import (
    StravaAuthenticationError,
    StravaProviderError,
    StravaRateLimitedError,
    StravaResponseError,
    StravaUnavailableError,
)
from app.schemas.strava import (
    StravaActivityPage,
    StravaActivitySummary,
    StravaRateLimits,
    StravaTokenResponse,
)

STRAVA_ORIGIN = "https://www.strava.com"
AUTHORIZE_PATH = "/oauth/authorize"
TOKEN_PATH = "/oauth/token"
REVOKE_PATH = "/oauth/revoke"
ACTIVITIES_PATH = "/api/v3/athlete/activities"
ACTIVITY_PATH = "/api/v3/activities/{activity_id}"
SUBSCRIPTIONS_PATH = "/api/v3/push_subscriptions"


class StravaClient:
    """Small production client with injectable HTTP transport for tests."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: SecretStr | str,
        redirect_uri: str,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 15,
    ) -> None:
        if not client_id:
            raise ValueError("A Strava client ID is required.")
        if not redirect_uri:
            raise ValueError("A Strava redirect URI is required.")
        self._client_id = client_id
        self._client_secret = (
            client_secret.get_secret_value()
            if isinstance(client_secret, SecretStr)
            else client_secret
        )
        if not self._client_secret:
            raise ValueError("A Strava client secret is required.")
        self._redirect_uri = redirect_uri
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            base_url=STRAVA_ORIGIN,
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def aclose(self) -> None:
        """Close an internally-created HTTP client."""

        if self._owns_client:
            await self._http.aclose()

    def authorization_url(
        self,
        *,
        state: str,
        scopes: Sequence[str],
        approval_prompt: str = "auto",
    ) -> str:
        """Build the browser URL for Strava's authorization endpoint."""

        if not state:
            raise ValueError("OAuth state is required.")
        if not scopes:
            raise ValueError("At least one Strava scope is required.")
        return str(
            httpx.URL(
                f"{STRAVA_ORIGIN}{AUTHORIZE_PATH}",
                params={
                    "client_id": self._client_id,
                    "redirect_uri": self._redirect_uri,
                    "response_type": "code",
                    "approval_prompt": approval_prompt,
                    "scope": ",".join(scopes),
                    "state": state,
                },
            )
        )

    async def exchange_code(self, code: str) -> StravaTokenResponse:
        """Exchange a one-time authorization code for provider tokens."""

        if not code:
            raise ValueError("An authorization code is required.")
        response = await self._request(
            "POST",
            TOKEN_PATH,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        return self._parse_model(response, StravaTokenResponse)

    async def refresh_token(self, refresh_token: str) -> StravaTokenResponse:
        """Refresh credentials, including a rotated refresh token."""

        if not refresh_token:
            raise ValueError("A refresh token is required.")
        response = await self._request(
            "POST",
            TOKEN_PATH,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        return self._parse_model(response, StravaTokenResponse)

    async def revoke(self, access_token: str) -> None:
        """Revoke provider access with HTTP Basic auth and a token form field."""

        if not access_token:
            raise ValueError("An access token is required.")
        await self._request(
            "POST",
            REVOKE_PATH,
            data={"token": access_token},
            auth=httpx.BasicAuth(self._client_id, self._client_secret),
        )

    async def get_activities_page(
        self,
        *,
        access_token: str,
        page: int,
        per_page: int,
        after: int | None = None,
        before: int | None = None,
    ) -> StravaActivityPage:
        """Fetch one activity-summary page and parse rate-limit headers."""

        if page < 1:
            raise ValueError("page must be at least 1.")
        if not 1 <= per_page <= 200:
            raise ValueError("per_page must be in [1, 200].")
        params: dict[str, int] = {"page": page, "per_page": per_page}
        if after is not None:
            params["after"] = after
        if before is not None:
            params["before"] = before
        response = await self._request(
            "GET",
            ACTIVITIES_PATH,
            params=params,
            headers=self._bearer_headers(access_token),
        )
        try:
            activities = TypeAdapter(list[StravaActivitySummary]).validate_python(
                response.json()
            )
        except (ValueError, ValidationError) as exc:
            raise StravaResponseError() from exc
        return StravaActivityPage(
            activities=activities,
            rate_limits=StravaRateLimits.from_headers(response.headers),
        )

    async def iter_activity_pages(
        self,
        *,
        access_token: str,
        per_page: int,
        after: int | None = None,
        before: int | None = None,
    ) -> AsyncIterator[StravaActivityPage]:
        """Yield activity pages until Strava returns an empty page."""

        page_number = 1
        while True:
            page = await self.get_activities_page(
                access_token=access_token,
                after=after,
                before=before,
                page=page_number,
                per_page=per_page,
            )
            if not page.activities:
                return
            yield page
            page_number += 1

    async def get_activity(
        self,
        *,
        access_token: str,
        activity_id: int,
    ) -> tuple[StravaActivitySummary, StravaRateLimits]:
        """Fetch one activity after a create/update webhook."""

        response = await self._request(
            "GET",
            ACTIVITY_PATH.format(activity_id=activity_id),
            headers=self._bearer_headers(access_token),
        )
        return (
            self._parse_model(response, StravaActivitySummary),
            StravaRateLimits.from_headers(response.headers),
        )

    async def create_webhook_subscription(
        self,
        *,
        callback_url: str,
        verify_token: str,
    ) -> int:
        """Create a push subscription and return its provider ID."""

        response = await self._request(
            "POST",
            SUBSCRIPTIONS_PATH,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "callback_url": callback_url,
                "verify_token": verify_token,
            },
        )
        payload = self._json_object(response)
        subscription_id = payload.get("id")
        if not isinstance(subscription_id, int):
            raise StravaResponseError()
        return subscription_id

    async def delete_webhook_subscription(self, subscription_id: int) -> None:
        """Delete a configured push subscription."""

        await self._request(
            "DELETE",
            f"{SUBSCRIPTIONS_PATH}/{subscription_id}",
            params={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )

    async def list_webhook_subscriptions(self) -> list[dict[str, Any]]:
        """List configured subscriptions without transforming provider metadata."""

        response = await self._request(
            "GET",
            SUBSCRIPTIONS_PATH,
            params={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise StravaResponseError() from exc
        if not isinstance(payload, list) or not all(
            isinstance(item, dict) for item in payload
        ):
            raise StravaResponseError()
        return payload

    @staticmethod
    def _bearer_headers(access_token: str) -> dict[str, str]:
        if not access_token:
            raise ValueError("An access token is required.")
        return {"Authorization": f"Bearer {access_token}"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.Response:
        try:
            response = await self._http.request(
                method,
                path,
                params=params,
                data=data,
                headers=headers,
                auth=auth,
            )
        except httpx.RequestError as exc:
            raise StravaUnavailableError() from exc
        rate_limits = StravaRateLimits.from_headers(response.headers)
        if response.status_code == 401:
            raise StravaAuthenticationError()
        if response.status_code == 429:
            raw_retry_after = response.headers.get("Retry-After")
            try:
                retry_after = int(raw_retry_after) if raw_retry_after else None
            except ValueError:
                retry_after = None
            raise StravaRateLimitedError(
                rate_limits=rate_limits,
                retry_after_seconds=retry_after,
            )
        if response.status_code >= 500:
            raise StravaUnavailableError(status_code=response.status_code)
        if response.status_code >= 400:
            raise StravaProviderError(
                f"strava_http_{response.status_code}",
                "Strava rejected the request.",
            )
        return response

    @staticmethod
    def _parse_model[ModelT](
        response: httpx.Response,
        model_type: type[ModelT],
    ) -> ModelT:
        try:
            return TypeAdapter(model_type).validate_python(response.json())
        except (ValueError, ValidationError) as exc:
            raise StravaResponseError() from exc

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise StravaResponseError() from exc
        if not isinstance(payload, dict):
            raise StravaResponseError()
        return payload
