"""Versioned, user-owned deterministic baseline persistence."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import AthleteBaseline, DisciplineBaseline, User
from app.domain.enums import (
    BaselineSource,
    BaselineStatus,
    Discipline,
    LevelLabel,
)
from app.repositories.errors import OwnedRecordNotFoundError

EnumMemberT = TypeVar("EnumMemberT", bound=StrEnum)


class BaselineRepository:
    """Append baseline versions and enforce ownership on every read."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        generated_at: datetime,
        analysis_start: datetime,
        analysis_end: datetime,
        source: object,
        status: object,
        overall_confidence: float,
        disciplines: list[dict[str, object]],
    ) -> AthleteBaseline:
        """Append one calculation after serializing versions on its owner row."""

        baseline_source = self._coerce_baseline_source(source)
        baseline_status = self._coerce_baseline_status(status)
        await self._lock_owner(user_id)
        version = await self._next_version(user_id)
        baseline = AthleteBaseline(
            user_id=user_id,
            version=version,
            generated_at=generated_at,
            analysis_start=analysis_start,
            analysis_end=analysis_end,
            source=baseline_source,
            status=baseline_status,
            overall_confidence=overall_confidence,
            discipline_baselines=[
                self._discipline_record(values) for values in disciplines
            ],
        )
        self._session.add(baseline)
        await self._session.flush()
        return baseline

    async def get_latest(
        self,
        *,
        user_id: uuid.UUID,
    ) -> AthleteBaseline | None:
        """Return the newest baseline for exactly one owner."""

        statement = (
            select(AthleteBaseline)
            .options(selectinload(AthleteBaseline.discipline_baselines))
            .where(AthleteBaseline.user_id == user_id)
            .order_by(
                AthleteBaseline.version.desc(),
                AthleteBaseline.generated_at.desc(),
            )
            .limit(1)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_for_user(
        self,
        *,
        user_id: uuid.UUID,
        baseline_id: uuid.UUID,
    ) -> AthleteBaseline | None:
        """Never resolve a personal baseline by record ID alone."""

        statement = (
            select(AthleteBaseline)
            .options(selectinload(AthleteBaseline.discipline_baselines))
            .where(
                AthleteBaseline.id == baseline_id,
                AthleteBaseline.user_id == user_id,
            )
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        *,
        user_id: uuid.UUID,
        limit: int = 20,
    ) -> tuple[AthleteBaseline, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        statement = (
            select(AthleteBaseline)
            .options(selectinload(AthleteBaseline.discipline_baselines))
            .where(AthleteBaseline.user_id == user_id)
            .order_by(AthleteBaseline.version.desc())
            .limit(limit)
        )
        result = await self._session.scalars(statement)
        return tuple(result.unique().all())

    async def _next_version(self, user_id: uuid.UUID) -> int:
        current = await self._session.scalar(
            select(func.max(AthleteBaseline.version)).where(
                AthleteBaseline.user_id == user_id,
            ),
        )
        return (current or 0) + 1

    async def _lock_owner(self, user_id: uuid.UUID) -> None:
        """Serialize version allocation per user on PostgreSQL."""

        owner_id = await self._session.scalar(
            select(User.id).where(User.id == user_id).with_for_update(),
        )
        if owner_id is None:
            raise OwnedRecordNotFoundError("user not found")

    @staticmethod
    def _discipline_record(
        values: dict[str, object],
    ) -> DisciplineBaseline:
        allowed = {
            "discipline",
            "level_label",
            "confidence",
            "sessions_count",
            "active_weeks",
            "total_duration_seconds",
            "average_weekly_duration_seconds",
            "total_distance_meters",
            "average_weekly_distance_meters",
            "longest_session_seconds",
            "longest_distance_meters",
            "recent_session_count",
            "metrics",
        }
        unexpected = values.keys() - allowed
        if unexpected:
            names = ", ".join(sorted(unexpected))
            raise ValueError(f"unexpected discipline baseline fields: {names}")
        missing = {
            "discipline",
            "level_label",
            "confidence",
            "sessions_count",
            "active_weeks",
            "total_duration_seconds",
            "average_weekly_duration_seconds",
            "recent_session_count",
            "metrics",
        } - values.keys()
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"missing discipline baseline fields: {names}")
        normalized = dict(values)
        normalized["discipline"] = BaselineRepository._coerce_member(
            Discipline,
            normalized["discipline"],
        )
        normalized["level_label"] = BaselineRepository._coerce_member(
            LevelLabel,
            normalized["level_label"],
        )
        metrics = normalized.get("metrics")
        if isinstance(metrics, dict):
            normalized["metrics"] = dict(metrics)
        return DisciplineBaseline(**normalized)

    @staticmethod
    def _coerce_baseline_source(value: object) -> BaselineSource:
        return BaselineRepository._coerce_member(BaselineSource, value)

    @staticmethod
    def _coerce_baseline_status(value: object) -> BaselineStatus:
        return BaselineRepository._coerce_member(BaselineStatus, value)

    @staticmethod
    def _coerce_member(
        enum_class: type[EnumMemberT],
        value: object,
    ) -> EnumMemberT:
        if isinstance(value, enum_class):
            return value
        if isinstance(value, StrEnum):
            return enum_class(value.value)
        return enum_class(str(value))


__all__: Sequence[str] = ("BaselineRepository",)
