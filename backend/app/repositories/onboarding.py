"""Ownership-scoped persistence for resumable onboarding sessions."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.db.models import OnboardingSession
from app.domain.enums import OnboardingStatus, OnboardingStep
from app.repositories.errors import OwnedRecordNotFoundError


class OnboardingRepository:
    """Store only confirmed answers plus explicitly pending parse output."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_user(
        self,
        *,
        user_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
        for_update: bool = False,
    ) -> OnboardingSession | None:
        """Load a session only inside the authenticated user's scope."""

        statement = select(OnboardingSession).where(
            OnboardingSession.user_id == user_id,
        )
        if session_id is not None:
            statement = statement.where(OnboardingSession.id == session_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def require_for_user(
        self,
        *,
        user_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
        for_update: bool = False,
    ) -> OnboardingSession:
        """Return the owned session without revealing cross-user existence."""

        onboarding = await self.get_for_user(
            user_id=user_id,
            session_id=session_id,
            for_update=for_update,
        )
        if onboarding is None:
            raise OwnedRecordNotFoundError("onboarding session not found")
        return onboarding

    async def lock_for_user(
        self,
        *,
        user_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
    ) -> OnboardingSession:
        """Serialize final confirmation and repeated callback processing."""

        return await self.require_for_user(
            user_id=user_id,
            session_id=session_id,
            for_update=True,
        )

    async def get_or_create(
        self,
        *,
        user_id: uuid.UUID,
        current_step: OnboardingStep = OnboardingStep.CONSENT,
    ) -> tuple[OnboardingSession, bool]:
        """Create the user's one durable session, safe under duplicate starts."""

        existing = await self.get_for_user(user_id=user_id)
        if existing is not None:
            return existing, False

        onboarding = OnboardingSession(
            user_id=user_id,
            current_step=current_step,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(onboarding)
                await self._session.flush()
        except IntegrityError:
            existing = await self.get_for_user(user_id=user_id)
            if existing is None:
                raise
            return existing, False
        return onboarding, True

    async def save_progress(
        self,
        *,
        user_id: uuid.UUID,
        current_step: OnboardingStep,
        answers: dict[str, object],
        return_to_summary: bool | None = None,
    ) -> OnboardingSession:
        """Replace confirmed staging data after one deterministic transition."""

        onboarding = await self.require_for_user(user_id=user_id)
        onboarding.current_step = current_step
        onboarding.answers = dict(answers)
        if return_to_summary is not None:
            onboarding.return_to_summary = return_to_summary
        await self._session.flush()
        return onboarding

    async def set_pending_parse(
        self,
        *,
        user_id: uuid.UUID,
        onboarding_step: OnboardingStep,
        parsed_value: dict[str, object],
    ) -> OnboardingSession:
        """Store safe interpreted output without merging it into answers."""

        onboarding = await self.require_for_user(user_id=user_id)
        onboarding.pending_free_text_step = onboarding_step
        onboarding.pending_parsed_value = dict(parsed_value)
        await self._session.flush()
        return onboarding

    async def begin_free_text(
        self,
        *,
        user_id: uuid.UUID,
        onboarding_step: OnboardingStep,
    ) -> OnboardingSession:
        """Persist entry into an explicit Other/Write answer path."""

        onboarding = await self.require_for_user(user_id=user_id)
        onboarding.pending_free_text_step = onboarding_step
        onboarding.pending_parsed_value = None
        await self._session.flush()
        return onboarding

    async def clear_pending_parse(
        self,
        *,
        user_id: uuid.UUID,
    ) -> OnboardingSession:
        """Discard a pending interpretation after confirmation or rejection."""

        onboarding = await self.require_for_user(user_id=user_id)
        onboarding.pending_free_text_step = None
        onboarding.pending_parsed_value = None
        await self._session.flush()
        return onboarding

    async def complete(
        self,
        *,
        user_id: uuid.UUID,
        completed_at: datetime | None = None,
    ) -> OnboardingSession:
        """Mark final confirmation idempotently."""

        onboarding = await self.require_for_user(user_id=user_id)
        if onboarding.status is not OnboardingStatus.COMPLETED:
            onboarding.status = OnboardingStatus.COMPLETED
            onboarding.current_step = OnboardingStep.SUMMARY
            onboarding.completed_at = completed_at or utc_now()
            onboarding.pending_free_text_step = None
            onboarding.pending_parsed_value = None
            onboarding.return_to_summary = False
            await self._session.flush()
        return onboarding

    async def cancel(
        self,
        *,
        user_id: uuid.UUID,
    ) -> OnboardingSession:
        """Cancel the owned session without deleting confirmed staging data."""

        onboarding = await self.require_for_user(user_id=user_id)
        onboarding.status = OnboardingStatus.CANCELLED
        onboarding.pending_free_text_step = None
        onboarding.pending_parsed_value = None
        onboarding.return_to_summary = False
        await self._session.flush()
        return onboarding

    async def restart(
        self,
        *,
        user_id: uuid.UUID,
    ) -> OnboardingSession:
        """Reset a cancelled or incomplete session to a clean consent step."""

        onboarding = await self.require_for_user(user_id=user_id)
        onboarding.status = OnboardingStatus.ACTIVE
        onboarding.current_step = OnboardingStep.CONSENT
        onboarding.answers = {}
        onboarding.pending_free_text_step = None
        onboarding.pending_parsed_value = None
        onboarding.return_to_summary = False
        onboarding.completed_at = None
        await self._session.flush()
        return onboarding
