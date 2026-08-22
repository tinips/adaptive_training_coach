"""Minimal authenticated API for the iPhone HealthKit sync proof of concept."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_runtime_settings, get_session_factory
from app.config import Settings
from app.repositories.activities import ActivityImportValidationError
from app.schemas.mobile_sync import (
    HealthKitWorkoutSyncRequest,
    HealthKitWorkoutSyncResponse,
    MobilePairRequest,
    MobilePairResponse,
)
from app.services.mobile_sync import (
    MobileSyncAuthenticationError,
    MobileSyncDisabledError,
    MobileSyncPairingError,
    MobileSyncService,
)

router = APIRouter(prefix="/v1/mobile", tags=["mobile-sync"])

SessionFactory = Annotated[
    async_sessionmaker[AsyncSession],
    Depends(get_session_factory),
]
RuntimeSettings = Annotated[Settings, Depends(get_runtime_settings)]


def _service(
    session_factory: SessionFactory,
    settings: RuntimeSettings,
) -> MobileSyncService:
    return MobileSyncService(session_factory=session_factory, settings=settings)


MobileService = Annotated[MobileSyncService, Depends(_service)]


def _bearer_token(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Extract one opaque Bearer token without retaining the header value."""

    scheme, separator, value = (authorization or "").partition(" ")
    if scheme.casefold() != "bearer" or not separator or not value.strip():
        raise _invalid_mobile_credentials()
    return value.strip()


@router.post(
    "/pair",
    response_model=MobilePairResponse,
    responses={
        401: {"description": "Invalid or expired pairing code"},
        404: {"description": "Mobile sync is disabled"},
    },
)
async def pair_mobile(
    payload: MobilePairRequest,
    service: MobileService,
) -> MobilePairResponse:
    """Exchange a Telegram-issued code for an opaque iPhone bearer token."""

    try:
        access_token = await service.pair(
            pairing_code=payload.pairing_code,
            installation_id=payload.installation_id,
        )
    except MobileSyncDisabledError as error:
        raise _mobile_sync_disabled() from error
    except MobileSyncPairingError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired pairing code",
        ) from error
    return MobilePairResponse(access_token=access_token)


@router.post(
    "/healthkit/workouts:sync",
    response_model=HealthKitWorkoutSyncResponse,
    responses={
        401: {"description": "Invalid mobile credentials"},
        404: {"description": "Mobile sync is disabled"},
    },
)
async def sync_healthkit_workouts(
    payload: HealthKitWorkoutSyncRequest,
    service: MobileService,
    access_token: Annotated[str, Depends(_bearer_token)],
) -> HealthKitWorkoutSyncResponse:
    """Persist a bounded batch owned solely by the authenticated mobile token."""

    try:
        results = await service.sync_healthkit_workouts(
            access_token=access_token,
            workouts=payload.workouts,
        )
    except MobileSyncDisabledError as error:
        raise _mobile_sync_disabled() from error
    except MobileSyncAuthenticationError as error:
        raise _invalid_mobile_credentials() from error
    except ActivityImportValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid HealthKit workout payload",
        ) from error
    return HealthKitWorkoutSyncResponse(results=list(results))


def _mobile_sync_disabled() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Mobile sync is not enabled",
    )


def _invalid_mobile_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid mobile credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


__all__ = ["router"]
