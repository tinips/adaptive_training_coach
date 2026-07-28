"""Ownership-scoped account queries and transactional local deletion."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import ConnectionStatus
from app.repositories.baselines import BaselineRepository
from app.repositories.onboarding import OnboardingRepository
from app.repositories.strava import StravaRepository
from app.repositories.users import UserRepository
from app.schemas.common import TelegramIdentity
from app.services.profiles import ProfileService


class AccountServiceError(RuntimeError):
    """Safe account-layer error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AccountQueryService:
    """Produce delivery-neutral account/profile/baseline/Strava views."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory
        self._profiles = ProfileService(session_factory)

    async def resolve_user_id(
        self,
        identity: TelegramIdentity,
    ) -> uuid.UUID | None:
        async with self._session_factory() as session:
            user = await UserRepository(session).get_by_telegram_id(
                identity.telegram_user_id
            )
            return user.id if user is not None else None

    async def lifecycle(
        self,
        identity: TelegramIdentity,
    ) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            user = await UserRepository(session).get_by_telegram_id(
                identity.telegram_user_id
            )
            if user is None:
                return None
            return {"user_id": user.id, "status": user.status}

    async def profile(
        self,
        identity: TelegramIdentity,
    ) -> dict[str, Any] | None:
        user_id = await self.resolve_user_id(identity)
        if user_id is None:
            return None
        profile = await self._profiles.get(user_id=user_id)
        if profile is None:
            return None
        data = profile.model_dump(mode="python")
        async with self._session_factory() as session:
            onboarding = await OnboardingRepository(session).get_for_user(
                user_id=user_id
            )
            if onboarding is not None:
                for key in (
                    "primary_sport",
                    "goal_type",
                    "goal_priority",
                ):
                    description = onboarding.answers.get(f"{key}_other_description")
                    if description:
                        data[f"{key}_other_description"] = description
        return data

    async def baseline(
        self,
        identity: TelegramIdentity,
    ) -> dict[str, Any] | None:
        user_id = await self.resolve_user_id(identity)
        if user_id is None:
            return None
        async with self._session_factory() as session:
            baseline = await BaselineRepository(session).get_latest(user_id=user_id)
            if baseline is None:
                return None
            disciplines: list[dict[str, Any]] = []
            recencies: list[float] = []
            for item in baseline.discipline_baselines:
                metrics = dict(item.metrics)
                recency = metrics.get("data_recency_days")
                if isinstance(recency, (int, float)):
                    recencies.append(float(recency))
                disciplines.append(
                    {
                        "discipline": item.discipline,
                        "level_label": item.level_label,
                        "confidence": item.confidence,
                        "sessions_count": item.sessions_count,
                        "active_weeks": item.active_weeks,
                        "total_duration_seconds": item.total_duration_seconds,
                        "average_weekly_duration_seconds": (
                            item.average_weekly_duration_seconds
                        ),
                        "total_distance_meters": item.total_distance_meters,
                        "average_weekly_distance_meters": (
                            item.average_weekly_distance_meters
                        ),
                        "longest_session_seconds": item.longest_session_seconds,
                        "longest_distance_meters": item.longest_distance_meters,
                        "recent_session_count": item.recent_session_count,
                        "metrics": metrics,
                    }
                )
            return {
                "source": baseline.source,
                "status": baseline.status,
                "analysis_start": baseline.analysis_start,
                "analysis_end": baseline.analysis_end,
                "overall_confidence": baseline.overall_confidence,
                "activity_count": sum(item["sessions_count"] for item in disciplines),
                "data_freshness_days": min(recencies) if recencies else None,
                "disciplines": disciplines,
            }

    async def strava(
        self,
        identity: TelegramIdentity,
    ) -> dict[str, Any] | None:
        user_id = await self.resolve_user_id(identity)
        if user_id is None:
            return None
        async with self._session_factory() as session:
            repository = StravaRepository(session)
            connection = await repository.get_connection(user_id=user_id)
            job = await repository.get_latest_sync_job(user_id=user_id)
            if connection is None:
                return {
                    "connected": False,
                    "can_disconnect": False,
                    "connection_status": ConnectionStatus.DISCONNECTED,
                    "accepted_scopes": [],
                    "last_successful_sync_at": None,
                    "sync_status": job.status if job is not None else None,
                }
            connected = connection.connection_status is ConnectionStatus.CONNECTED
            return {
                "connected": connected,
                "can_disconnect": (
                    connection.connection_status is not ConnectionStatus.DISCONNECTED
                ),
                "connection_status": connection.connection_status,
                "accepted_scopes": list(connection.accepted_scopes),
                "last_successful_sync_at": connection.last_successful_sync_at,
                "sync_status": job.status if job is not None else None,
            }


class AccountService:
    """Delete all local personal data in one database transaction."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def delete(self, *, user_id: uuid.UUID) -> bool:
        async with self._session_factory.begin() as session:
            return await UserRepository(session).delete(user_id=user_id)
