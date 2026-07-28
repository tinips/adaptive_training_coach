"""Browser OAuth and fast Strava webhook endpoints."""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Query,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.api.dependencies import get_runtime_settings, get_strava_coordinator
from app.bot import messages
from app.config import Settings
from app.integrations.strava.exceptions import StravaProviderError
from app.schemas.strava import StravaWebhookEvent
from app.services.strava.exceptions import (
    OAuthAuthorizationDeniedError,
    OAuthScopeError,
    OAuthStateRejectedError,
    WebhookVerificationError,
)
from app.services.strava.orchestrator import (
    ConnectTicketRejectedError,
    StravaConfigurationError,
    StravaCoordinator,
)

router = APIRouter(prefix="/integrations/strava", tags=["strava"])
logger = logging.getLogger(__name__)

Coordinator = Annotated[StravaCoordinator, Depends(get_strava_coordinator)]
RuntimeSettings = Annotated[Settings, Depends(get_runtime_settings)]


@router.get("/connect", response_model=None)
async def connect(
    ticket: Annotated[str, Query(min_length=1)],
    coordinator: Coordinator,
) -> RedirectResponse | HTMLResponse:
    """Consume an opaque app ticket and redirect to provider authorization."""

    try:
        initiation = await coordinator.begin_oauth(raw_ticket=ticket)
    except ConnectTicketRejectedError:
        return _oauth_failure("expired_state", status_code=400)
    except StravaConfigurationError:
        return _oauth_failure("provider_error", status_code=503)
    except Exception as exc:
        _log_safe_failure("connect", exc)
        return _oauth_failure("provider_error", status_code=500)
    return RedirectResponse(initiation.authorization_url, status_code=302)


@router.get("/callback", response_class=HTMLResponse)
async def callback(
    background_tasks: BackgroundTasks,
    coordinator: Coordinator,
    settings: RuntimeSettings,
    state: Annotated[str, Query(min_length=1)],
    code: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    """Validate provider callback state, save credentials, and request import."""

    try:
        completion = await coordinator.complete_oauth(
            raw_state=state,
            code=code,
            accepted_scope=scope,
            error=error,
        )
    except OAuthAuthorizationDeniedError:
        return _oauth_failure("access_denied", status_code=400)
    except OAuthScopeError:
        return _oauth_failure("insufficient_scope", status_code=400)
    except OAuthStateRejectedError:
        return _oauth_failure("invalid_state", status_code=400)
    except StravaConfigurationError:
        return _oauth_failure("provider_error", status_code=503)
    except StravaProviderError:
        return _oauth_failure("provider_error", status_code=502)
    except Exception as exc:
        _log_safe_failure("callback", exc)
        return _oauth_failure("provider_error", status_code=500)

    background_tasks.add_task(
        _initial_sync_safely,
        coordinator,
        completion.user_id,
    )
    return HTMLResponse(
        messages.oauth_success_page(
            initial_sync_started=True,
            telegram_bot_username=settings.telegram_bot_username,
        ),
        status_code=200,
    )


@router.get("/webhook")
async def verify_webhook(
    coordinator: Coordinator,
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    verify_token: Annotated[
        str | None,
        Query(alias="hub.verify_token"),
    ] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> JSONResponse:
    """Echo the exact provider challenge only after token verification."""

    try:
        response = coordinator.verify_webhook(
            mode=mode,
            verify_token=verify_token,
            challenge=challenge,
        )
    except (WebhookVerificationError, StravaConfigurationError):
        return JSONResponse(
            status_code=403,
            content={"status": "verification_failed"},
        )
    return JSONResponse(status_code=200, content=response)


@router.post("/webhook")
async def receive_webhook(
    event: StravaWebhookEvent,
    background_tasks: BackgroundTasks,
    coordinator: Coordinator,
) -> JSONResponse:
    """Persist/deduplicate before acknowledging, then process in background."""

    try:
        acceptance = await coordinator.accept_webhook(event=event)
    except StravaConfigurationError:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable"},
        )
    except Exception as exc:
        _log_safe_failure("webhook_accept", exc)
        return JSONResponse(
            status_code=500,
            content={"status": "unavailable"},
        )
    if (
        acceptance.status == "accepted"
        and acceptance.event_id is not None
        and acceptance.external_event_key is not None
    ):
        background_tasks.add_task(
            _process_webhook_safely,
            coordinator,
            acceptance.event_id,
            acceptance.external_event_key,
        )
    return JSONResponse(status_code=200, content={"status": acceptance.status})


async def _initial_sync_safely(
    coordinator: StravaCoordinator,
    user_id: UUID,
) -> None:
    try:
        await coordinator.initial_sync(user_id=user_id)
    except Exception as exc:
        _log_safe_failure("initial_sync", exc)


async def _process_webhook_safely(
    coordinator: StravaCoordinator,
    event_id: UUID,
    external_event_key: str,
) -> None:
    try:
        await coordinator.process_webhook(
            event_id=event_id,
            external_event_key=external_event_key,
        )
    except Exception as exc:
        _log_safe_failure("webhook_process", exc)


def _oauth_failure(reason: str, *, status_code: int) -> HTMLResponse:
    return HTMLResponse(
        messages.oauth_failure_page(reason),
        status_code=status_code,
    )


def _log_safe_failure(operation: str, error: Exception) -> None:
    logger.error(
        "Strava operation failed operation=%s type=%s",
        operation,
        type(error).__name__,
    )
