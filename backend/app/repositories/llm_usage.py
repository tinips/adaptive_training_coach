"""Safe LLM usage metadata for rate limiting and diagnostics."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LLMProviderMode, LLMUsage
from app.domain.enums import LLMUsageStatus, OnboardingStep

ProviderMode = Literal["mock", "live"]


class LLMUsageRepository:
    """Persist token counts and outcomes, never prompts or free text."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        user_id: uuid.UUID,
        onboarding_step: OnboardingStep,
        provider_mode: ProviderMode,
        model: str | None,
        status: LLMUsageStatus,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        created_at: datetime | None = None,
    ) -> LLMUsage:
        usage = LLMUsage(
            user_id=user_id,
            onboarding_step=onboarding_step,
            provider_mode=LLMProviderMode(provider_mode),
            model=model,
            status=status,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        if created_at is not None:
            usage.created_at = created_at
        self._session.add(usage)
        await self._session.flush()
        return usage

    async def record_usage(
        self,
        **values: object,
    ) -> LLMUsage:
        """Compatibility alias for application services using a verbose name."""

        return await self.record(**values)  # type: ignore[arg-type]

    async def count_since(
        self,
        *,
        user_id: uuid.UUID,
        since: datetime,
        provider_mode: ProviderMode | None = None,
    ) -> int:
        """Count only this user's requests inside a rolling window."""

        statement = select(func.count(LLMUsage.id)).where(
            LLMUsage.user_id == user_id,
            LLMUsage.created_at >= since,
        )
        if provider_mode is not None:
            statement = statement.where(
                LLMUsage.provider_mode == LLMProviderMode(provider_mode)
            )
        value = await self._session.scalar(statement)
        return int(value or 0)

    async def update_outcome(
        self,
        *,
        user_id: uuid.UUID,
        usage_id: uuid.UUID,
        status: LLMUsageStatus,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> LLMUsage:
        """Finalize one owned pre-invocation usage reservation."""

        usage = await self._session.scalar(
            select(LLMUsage).where(
                LLMUsage.id == usage_id,
                LLMUsage.user_id == user_id,
            )
        )
        if usage is None:
            raise ValueError("owned llm usage reservation not found")
        usage.status = status
        usage.prompt_tokens = prompt_tokens
        usage.completion_tokens = completion_tokens
        await self._session.flush()
        return usage

    async def list_for_user(
        self,
        *,
        user_id: uuid.UUID,
        since: datetime | None = None,
        limit: int = 100,
    ) -> tuple[LLMUsage, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        statement = select(LLMUsage).where(LLMUsage.user_id == user_id)
        if since is not None:
            statement = statement.where(LLMUsage.created_at >= since)
        statement = statement.order_by(LLMUsage.created_at.desc()).limit(limit)
        result = await self._session.scalars(statement)
        return tuple(result.all())
