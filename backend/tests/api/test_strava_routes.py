"""HTTP boundary tests for opaque OAuth and fast webhook routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.dependencies import get_runtime_settings, get_strava_coordinator
from app.api.routes.strava import router
from app.config import Settings
from app.schemas.strava import StravaWebhookEvent
from app.services.strava.exceptions import (
    OAuthAuthorizationDeniedError,
    WebhookVerificationError,
)
from app.services.strava.oauth import OAuthCompletion, OAuthInitiation
from app.services.strava.orchestrator import ConnectTicketRejectedError
from app.services.strava.webhook import WebhookAcceptance, WebhookOutcome

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)


class CoordinatorFake:
    def __init__(self) -> None:
        self.begin_tickets: list[str] = []
        self.callback_states: list[str] = []
        self.callback_values: list[tuple[str | None, str | None, str | None]] = []
        self.synced_users: list[UUID] = []
        self.processed_events: list[UUID] = []
        self.begin_error: Exception | None = None
        self.callback_error: Exception | None = None
        self.acceptance = WebhookAcceptance(
            status="accepted",
            event_id=uuid4(),
            external_event_key="canonical-event-key",
        )

    async def begin_oauth(self, *, raw_ticket: str) -> OAuthInitiation:
        self.begin_tickets.append(raw_ticket)
        if self.begin_error is not None:
            raise self.begin_error
        return OAuthInitiation(
            authorization_url="https://www.strava.com/oauth/authorize?state=opaque",
            expires_at=NOW + timedelta(minutes=10),
        )

    async def complete_oauth(
        self,
        *,
        raw_state: str,
        code: str | None,
        accepted_scope: str | None,
        error: str | None,
    ) -> OAuthCompletion:
        self.callback_states.append(raw_state)
        self.callback_values.append((code, accepted_scope, error))
        if self.callback_error is not None:
            raise self.callback_error
        return OAuthCompletion(
            user_id=UUID("11111111-1111-1111-1111-111111111111"),
            strava_athlete_id=42,
            accepted_scopes=frozenset({"read", "activity:read_all"}),
            initial_sync_requested=False,
        )

    async def initial_sync(self, *, user_id: UUID) -> object:
        self.synced_users.append(user_id)
        return object()

    def verify_webhook(
        self,
        *,
        mode: str | None,
        verify_token: str | None,
        challenge: str | None,
    ) -> dict[str, str]:
        if mode != "subscribe" or verify_token != "verify-token" or challenge is None:
            raise WebhookVerificationError()
        return {"hub.challenge": challenge}

    async def accept_webhook(
        self,
        *,
        event: StravaWebhookEvent,
    ) -> WebhookAcceptance:
        del event
        return self.acceptance

    async def process_webhook(
        self,
        *,
        event_id: UUID,
        external_event_key: str,
    ) -> WebhookOutcome:
        assert external_event_key == "canonical-event-key"
        self.processed_events.append(event_id)
        return WebhookOutcome(status="processed", event_id=event_id)


@pytest.fixture
def app_and_coordinator() -> tuple[FastAPI, CoordinatorFake]:
    application = FastAPI()
    application.include_router(router)
    coordinator = CoordinatorFake()
    application.dependency_overrides[get_strava_coordinator] = lambda: coordinator
    application.dependency_overrides[get_runtime_settings] = lambda: Settings(
        environment="test",
        strava_enabled=True,
        database_url="sqlite+aiosqlite:///:memory:",
        telegram_bot_username="adaptive_coach_test_bot",
    )
    return application, coordinator


@pytest.mark.asyncio
async def test_connect_consumes_only_opaque_ticket_and_redirects(
    app_and_coordinator: tuple[FastAPI, CoordinatorFake],
) -> None:
    application, coordinator = app_and_coordinator
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        response = await client.get(
            "/integrations/strava/connect",
            params={"ticket": "opaque-one-time-ticket"},
        )

    assert response.status_code == 302
    assert response.headers["location"].startswith(
        "https://www.strava.com/oauth/authorize"
    )
    assert coordinator.begin_tickets == ["opaque-one-time-ticket"]


@pytest.mark.asyncio
async def test_connect_rejection_returns_safe_english_html(
    app_and_coordinator: tuple[FastAPI, CoordinatorFake],
) -> None:
    application, coordinator = app_and_coordinator
    coordinator.begin_error = ConnectTicketRejectedError()
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/integrations/strava/connect",
            params={"ticket": "expired"},
        )

    assert response.status_code == 400
    assert "expired" in response.text.lower()
    assert "traceback" not in response.text.lower()


@pytest.mark.asyncio
async def test_callback_saves_then_requests_background_initial_sync(
    app_and_coordinator: tuple[FastAPI, CoordinatorFake],
) -> None:
    application, coordinator = app_and_coordinator
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/integrations/strava/callback",
            params={
                "state": "provider-state",
                "code": "authorization-code",
                "scope": "read,activity:read_all",
            },
        )

    expected_user = UUID("11111111-1111-1111-1111-111111111111")
    assert response.status_code == 200
    assert "Strava connected" in response.text
    assert "Open Telegram" in response.text
    assert 'href="https://t.me/adaptive_coach_test_bot"' in response.text
    assert coordinator.callback_states == ["provider-state"]
    assert coordinator.callback_values == [
        ("authorization-code", "read,activity:read_all", None)
    ]
    assert coordinator.synced_users == [expected_user]


@pytest.mark.asyncio
async def test_callback_denial_never_exposes_internal_details(
    app_and_coordinator: tuple[FastAPI, CoordinatorFake],
) -> None:
    application, coordinator = app_and_coordinator
    coordinator.callback_error = OAuthAuthorizationDeniedError()
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/integrations/strava/callback",
            params={"state": "provider-state", "error": "access_denied"},
        )

    assert response.status_code == 400
    assert "declined" in response.text.lower()
    assert "OAuthAuthorizationDeniedError" not in response.text


@pytest.mark.asyncio
async def test_webhook_verification_is_exact(
    app_and_coordinator: tuple[FastAPI, CoordinatorFake],
) -> None:
    application, _ = app_and_coordinator
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        verified = await client.get(
            "/integrations/strava/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "verify-token",
                "hub.challenge": "challenge-value",
            },
        )
        rejected = await client.get(
            "/integrations/strava/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "verify-token-wrong",
                "hub.challenge": "challenge-value",
            },
        )

    assert verified.status_code == 200
    assert verified.json() == {"hub.challenge": "challenge-value"}
    assert rejected.status_code == 403
    assert rejected.json() == {"status": "verification_failed"}


@pytest.mark.asyncio
async def test_webhook_acknowledges_after_accept_then_processes_background(
    app_and_coordinator: tuple[FastAPI, CoordinatorFake],
) -> None:
    application, coordinator = app_and_coordinator
    transport = httpx.ASGITransport(app=application)
    payload = {
        "object_type": "activity",
        "object_id": 8080,
        "aspect_type": "create",
        "owner_id": 42,
        "event_time": int(NOW.timestamp()),
        "updates": {},
        "subscription_id": 7,
    }
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/integrations/strava/webhook",
            json=payload,
        )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert coordinator.processed_events == [coordinator.acceptance.event_id]


@pytest.mark.asyncio
async def test_duplicate_webhook_is_not_processed_again(
    app_and_coordinator: tuple[FastAPI, CoordinatorFake],
) -> None:
    application, coordinator = app_and_coordinator
    coordinator.acceptance = WebhookAcceptance(
        status="duplicate",
        event_id=None,
    )
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/integrations/strava/webhook",
            json={
                "object_type": "activity",
                "object_id": 8080,
                "aspect_type": "update",
                "owner_id": 42,
                "event_time": int(NOW.timestamp()),
                "updates": {},
            },
        )

    assert response.status_code == 200
    assert response.json() == {"status": "duplicate"}
    assert coordinator.processed_events == []
