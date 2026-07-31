"""Focused tests for direct Strava HTTP behavior and normalization."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from app.domain.enums import Discipline
from app.integrations.strava.client import StravaClient
from app.integrations.strava.exceptions import (
    StravaAuthenticationError,
    StravaRateLimitedError,
)
from app.schemas.strava import StravaActivitySummary


def activity_payload(
    activity_id: int,
    *,
    sport_type: str = "Run",
) -> dict[str, object]:
    return {
        "id": activity_id,
        "name": "Morning activity",
        "sport_type": sport_type,
        "start_date": "2026-07-27T07:00:00Z",
        "elapsed_time": 3600,
        "moving_time": None,
        "distance": None,
        "total_elevation_gain": None,
        "average_heartrate": None,
        "max_heartrate": None,
        "average_speed": None,
        "average_watts": None,
    }


def build_client(
    handler: httpx.MockTransport,
) -> tuple[StravaClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(
        transport=handler,
        base_url="https://www.strava.com",
    )
    return (
        StravaClient(
            client_id="client-id",
            client_secret="client-secret",
            redirect_uri="https://coach.example/integrations/strava/callback",
            http_client=http,
        ),
        http,
    )


@pytest.mark.asyncio
async def test_official_revoke_uses_oauth_revoke_basic_auth_and_token_form() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["form"] = parse_qs(request.content.decode())
        seen["authorization"] = request.headers["Authorization"]
        return httpx.Response(200, json={})

    client, http = build_client(httpx.MockTransport(handler))
    try:
        await client.revoke("private-access-token")
    finally:
        await http.aclose()

    expected_basic = base64.b64encode(b"client-id:client-secret").decode()
    assert seen == {
        "method": "POST",
        "path": "/oauth/revoke",
        "form": {"token": ["private-access-token"]},
        "authorization": f"Basic {expected_basic}",
    }


@pytest.mark.asyncio
async def test_activity_pagination_runs_until_empty_and_sends_cutoffs() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = int(request.url.params["page"])
        payload = [activity_payload(101)] if page == 1 else []
        return httpx.Response(
            200,
            json=payload,
            headers={
                "X-RateLimit-Limit": "200,2000",
                "X-RateLimit-Usage": "10,100",
                "X-ReadRateLimit-Limit": "100,1000",
                "X-ReadRateLimit-Usage": "5,50",
            },
        )

    client, http = build_client(httpx.MockTransport(handler))
    try:
        pages = [
            page
            async for page in client.iter_activity_pages(
                access_token="access",
                after=1000,
                before=2000,
                per_page=50,
            )
        ]
    finally:
        await http.aclose()

    assert len(pages) == 1
    assert [request.url.params["page"] for request in requests] == ["1", "2"]
    assert all(request.url.path == "/api/v3/athlete/activities" for request in requests)
    assert requests[0].url.params["after"] == "1000"
    assert requests[0].url.params["before"] == "2000"
    assert requests[0].url.params["per_page"] == "50"


@pytest.mark.asyncio
async def test_all_four_rate_headers_are_parsed() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[activity_payload(101)],
            headers={
                "X-RateLimit-Limit": "200,2000",
                "X-RateLimit-Usage": "180,1500",
                "X-ReadRateLimit-Limit": "100,1000",
                "X-ReadRateLimit-Usage": "91,700",
            },
        )

    client, http = build_client(httpx.MockTransport(handler))
    try:
        page = await client.get_activities_page(
            access_token="access",
            page=1,
            per_page=100,
        )
    finally:
        await http.aclose()

    assert page.rate_limits.overall_limit is not None
    assert page.rate_limits.overall_limit.model_dump() == {
        "short_term": 200,
        "daily": 2000,
    }
    assert page.rate_limits.overall_usage is not None
    assert page.rate_limits.overall_usage.daily == 1500
    assert page.rate_limits.read_limit is not None
    assert page.rate_limits.read_limit.short_term == 100
    assert page.rate_limits.read_usage is not None
    assert page.rate_limits.read_usage.short_term == 91
    assert page.rate_limits.is_near_limit()


def test_nullable_unknown_activity_is_normalized_without_inventing_values() -> None:
    summary = StravaActivitySummary.model_validate(
        activity_payload(999, sport_type="Pickleball")
    )

    normalized = summary.normalized()

    assert normalized.sport == Discipline.OTHER
    assert normalized.source_sport_type == "Pickleball"
    assert normalized.started_at == datetime(2026, 7, 27, 7, tzinfo=UTC)
    assert normalized.distance_meters is None
    assert normalized.average_heart_rate is None
    assert normalized.average_watts is None


def test_normalization_retains_modeled_provider_values_in_raw_summary() -> None:
    payload = activity_payload(1001, sport_type="MountainBikeRide")
    payload.update(
        {
            "type": "Ride",
            "timezone": "(GMT+01:00) Europe/Madrid",
            "moving_time": 3300,
            "distance": 21_500.5,
            "total_elevation_gain": 480.25,
            "average_heartrate": 148.5,
            "max_heartrate": 177.0,
            "average_speed": 6.2,
            "max_speed": 15.4,
            "average_cadence": 84.5,
            "average_watts": 235.0,
            "trainer": False,
            "commute": True,
            "manual": True,
        }
    )
    summary = StravaActivitySummary.model_validate(payload)

    normalized = summary.normalized()

    assert normalized.max_speed == 15.4
    assert normalized.average_cadence == 84.5
    assert normalized.raw_summary is not None
    assert set(normalized.raw_summary) == set(StravaActivitySummary.model_fields)
    assert normalized.raw_summary["average_watts"] == 235.0
    assert normalized.raw_summary["trainer"] is False
    assert normalized.raw_summary["commute"] is True
    assert normalized.raw_summary["manual"] is True
    assert normalized.raw_summary["max_speed"] == 15.4
    assert normalized.raw_summary["average_cadence"] == 84.5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "exception_type"),
    [
        (401, StravaAuthenticationError),
        (429, StravaRateLimitedError),
    ],
)
async def test_authentication_and_rate_errors_are_safe(
    status_code: int,
    exception_type: type[Exception],
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={"message": "upstream secret detail must not escape"},
            headers={
                "X-RateLimit-Limit": "200,2000",
                "X-RateLimit-Usage": "200,2000",
                "Retry-After": "42",
            },
        )

    client, http = build_client(httpx.MockTransport(handler))
    try:
        with pytest.raises(exception_type) as caught:
            await client.get_activities_page(
                access_token="access",
                page=1,
                per_page=100,
            )
    finally:
        await http.aclose()

    assert "upstream secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_refresh_accepts_rotated_token_pair() -> None:
    seen_form: dict[str, list[str]] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_form.update(parse_qs(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_at": 1785300000,
            },
        )

    client, http = build_client(httpx.MockTransport(handler))
    try:
        tokens = await client.refresh_token("old-refresh")
    finally:
        await http.aclose()

    assert seen_form["grant_type"] == ["refresh_token"]
    assert seen_form["refresh_token"] == ["old-refresh"]
    assert tokens.access_token == "new-access"
    assert tokens.refresh_token == "new-refresh"


@pytest.mark.asyncio
async def test_webhook_subscription_uses_push_subscriptions_endpoint() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(201, json={"id": 1234})
        return httpx.Response(204)

    client, http = build_client(httpx.MockTransport(handler))
    try:
        subscription_id = await client.create_webhook_subscription(
            callback_url="https://coach.example/integrations/strava/webhook",
            verify_token="verify-token",
        )
        await client.delete_webhook_subscription(subscription_id)
    finally:
        await http.aclose()

    assert subscription_id == 1234
    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", "/api/v3/push_subscriptions"),
        ("DELETE", "/api/v3/push_subscriptions/1234"),
    ]
    create_form = parse_qs(requests[0].content.decode())
    assert create_form["callback_url"] == [
        "https://coach.example/integrations/strava/webhook"
    ]
    assert create_form["verify_token"] == ["verify-token"]
