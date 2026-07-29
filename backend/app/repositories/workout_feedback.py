"""Ownership-scoped persistence for resumable workout feedback flows."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.db.models import (
    Activity,
    ActivityFeedback,
    BodyArea,
    User,
    WorkoutFlowSession,
)
from app.domain.enums import (
    DiscomfortSeverity,
    HeartRateSource,
    HeartRateTemporalQuality,
    WorkoutFlowStep,
)
from app.repositories.errors import OwnedRecordNotFoundError


class WorkoutFeedbackRepository:
    """Persist one durable flow and one optional feedback record per activity."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_owner(
        self,
        *,
        user_id: uuid.UUID,
        for_update: bool = False,
    ) -> User | None:
        statement = select(User).where(User.id == user_id)
        if for_update:
            statement = statement.with_for_update()
        return cast(User | None, await self._session.scalar(statement))

    async def require_owner(
        self,
        *,
        user_id: uuid.UUID,
        for_update: bool = False,
    ) -> User:
        user = await self.get_owner(user_id=user_id, for_update=for_update)
        if user is None:
            raise OwnedRecordNotFoundError("workout feedback owner not found")
        return user

    async def get_flow(
        self,
        *,
        user_id: uuid.UUID,
        for_update: bool = False,
    ) -> WorkoutFlowSession | None:
        statement = select(WorkoutFlowSession).where(
            WorkoutFlowSession.user_id == user_id
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(
            WorkoutFlowSession | None,
            await self._session.scalar(statement),
        )

    async def require_flow(
        self,
        *,
        user_id: uuid.UUID,
        for_update: bool = False,
    ) -> WorkoutFlowSession:
        flow = await self.get_flow(user_id=user_id, for_update=for_update)
        if flow is None:
            raise OwnedRecordNotFoundError("workout feedback flow not found")
        return flow

    async def get_or_create_flow(
        self,
        *,
        user_id: uuid.UUID,
    ) -> tuple[WorkoutFlowSession, bool]:
        existing = await self.get_flow(user_id=user_id, for_update=True)
        if existing is not None:
            return existing, False
        await self.require_owner(user_id=user_id, for_update=True)
        flow = WorkoutFlowSession(user_id=user_id)
        try:
            async with self._session.begin_nested():
                self._session.add(flow)
                await self._session.flush()
        except IntegrityError:
            existing = await self.get_flow(user_id=user_id, for_update=True)
            if existing is None:
                raise
            return existing, False
        return flow, True

    async def begin_waiting_for_file(
        self,
        *,
        user_id: uuid.UUID,
    ) -> WorkoutFlowSession:
        flow, _ = await self.get_or_create_flow(user_id=user_id)
        flow.activity_id = None
        flow.state = WorkoutFlowStep.WAITING_FOR_FILE
        flow.pending_manual_average_heart_rate = None
        flow.pending_discomfort_description = None
        flow.return_to_onboarding = False
        flow.completed_at = None
        await self._session.flush()
        return flow

    async def begin_for_activity(
        self,
        *,
        user_id: uuid.UUID,
        activity_id: uuid.UUID,
        initial_state: WorkoutFlowStep,
        return_to_onboarding: bool,
    ) -> WorkoutFlowSession:
        await self.require_activity(
            user_id=user_id,
            activity_id=activity_id,
            for_update=True,
        )
        flow, _ = await self.get_or_create_flow(user_id=user_id)
        flow.activity_id = activity_id
        flow.state = initial_state
        flow.pending_manual_average_heart_rate = None
        flow.pending_discomfort_description = None
        flow.return_to_onboarding = return_to_onboarding
        flow.completed_at = None
        await self._session.flush()
        return flow

    async def set_state(
        self,
        *,
        flow: WorkoutFlowSession,
        state: WorkoutFlowStep,
        terminal_at: datetime | None = None,
    ) -> WorkoutFlowSession:
        flow.state = state
        flow.completed_at = terminal_at
        if state in {WorkoutFlowStep.COMPLETE, WorkoutFlowStep.CANCELLED}:
            flow.pending_manual_average_heart_rate = None
            flow.pending_discomfort_description = None
            flow.completed_at = terminal_at or utc_now()
        await self._session.flush()
        return flow

    async def set_pending_manual_heart_rate(
        self,
        *,
        flow: WorkoutFlowSession,
        beats_per_minute: int | None,
    ) -> WorkoutFlowSession:
        flow.pending_manual_average_heart_rate = beats_per_minute
        await self._session.flush()
        return flow

    async def set_pending_discomfort_description(
        self,
        *,
        flow: WorkoutFlowSession,
        description: str | None,
    ) -> WorkoutFlowSession:
        flow.pending_discomfort_description = description
        await self._session.flush()
        return flow

    async def get_activity(
        self,
        *,
        user_id: uuid.UUID,
        activity_id: uuid.UUID,
        for_update: bool = False,
    ) -> Activity | None:
        statement = select(Activity).where(
            Activity.user_id == user_id,
            Activity.id == activity_id,
            Activity.deleted_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(Activity | None, await self._session.scalar(statement))

    async def require_activity(
        self,
        *,
        user_id: uuid.UUID,
        activity_id: uuid.UUID,
        for_update: bool = False,
    ) -> Activity:
        activity = await self.get_activity(
            user_id=user_id,
            activity_id=activity_id,
            for_update=for_update,
        )
        if activity is None:
            raise OwnedRecordNotFoundError("workout feedback activity not found")
        return activity

    async def get_feedback(
        self,
        *,
        user_id: uuid.UUID,
        activity_id: uuid.UUID,
        for_update: bool = False,
    ) -> ActivityFeedback | None:
        statement = select(ActivityFeedback).where(
            ActivityFeedback.user_id == user_id,
            ActivityFeedback.activity_id == activity_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(
            ActivityFeedback | None,
            await self._session.scalar(statement),
        )

    async def get_or_create_feedback(
        self,
        *,
        user_id: uuid.UUID,
        activity_id: uuid.UUID,
    ) -> ActivityFeedback:
        await self.require_activity(
            user_id=user_id,
            activity_id=activity_id,
            for_update=True,
        )
        existing = await self.get_feedback(
            user_id=user_id,
            activity_id=activity_id,
            for_update=True,
        )
        if existing is not None:
            return existing
        feedback = ActivityFeedback(user_id=user_id, activity_id=activity_id)
        try:
            async with self._session.begin_nested():
                self._session.add(feedback)
                await self._session.flush()
        except IntegrityError:
            existing = await self.get_feedback(
                user_id=user_id,
                activity_id=activity_id,
                for_update=True,
            )
            if existing is None:
                raise
            return existing
        return feedback

    async def save_manual_heart_rate(
        self,
        *,
        user_id: uuid.UUID,
        activity_id: uuid.UUID,
        beats_per_minute: int | None,
    ) -> tuple[ActivityFeedback, Activity]:
        activity = await self.require_activity(
            user_id=user_id,
            activity_id=activity_id,
            for_update=True,
        )
        feedback = await self.get_or_create_feedback(
            user_id=user_id,
            activity_id=activity_id,
        )
        feedback.manual_average_heart_rate = beats_per_minute
        self._touch(feedback)
        if beats_per_minute is not None and self._manual_can_be_canonical(activity):
            activity.average_heart_rate = float(beats_per_minute)
            activity.average_heart_rate_source = HeartRateSource.USER_REPORTED
            activity.heart_rate_quality = HeartRateTemporalQuality.MANUAL
            activity.heart_rate_reliable = False
        elif (
            beats_per_minute is None
            and activity.average_heart_rate_source is HeartRateSource.USER_REPORTED
        ):
            activity.average_heart_rate = None
            activity.average_heart_rate_source = HeartRateSource.UNAVAILABLE
            activity.heart_rate_quality = HeartRateTemporalQuality.UNKNOWN
            activity.heart_rate_reliable = False
        await self._session.flush()
        return feedback, activity

    async def save_rpe(
        self,
        *,
        user_id: uuid.UUID,
        activity_id: uuid.UUID,
        value: int | None,
        label: str | None,
    ) -> ActivityFeedback:
        feedback = await self.get_or_create_feedback(
            user_id=user_id,
            activity_id=activity_id,
        )
        feedback.reported_rpe = value
        feedback.reported_rpe_label = label
        self._touch(feedback)
        await self._session.flush()
        return feedback

    async def save_discomfort(
        self,
        *,
        user_id: uuid.UUID,
        activity_id: uuid.UUID,
        reported: bool | None,
    ) -> ActivityFeedback:
        feedback = await self.get_or_create_feedback(
            user_id=user_id,
            activity_id=activity_id,
        )
        feedback.reported_discomfort = reported
        if reported is not True:
            feedback.discomfort_body_area = None
            feedback.discomfort_severity = None
            feedback.discomfort_description = None
        self._touch(feedback)
        await self._session.flush()
        return feedback

    async def save_body_area(
        self,
        *,
        user_id: uuid.UUID,
        activity_id: uuid.UUID,
        body_area: BodyArea | None,
    ) -> ActivityFeedback:
        feedback = await self.get_or_create_feedback(
            user_id=user_id,
            activity_id=activity_id,
        )
        feedback.discomfort_body_area = body_area
        feedback.discomfort_description = None
        feedback.discomfort_severity = None
        self._touch(feedback)
        await self._session.flush()
        return feedback

    async def save_discomfort_description(
        self,
        *,
        user_id: uuid.UUID,
        activity_id: uuid.UUID,
        description: str | None,
    ) -> ActivityFeedback:
        feedback = await self.get_or_create_feedback(
            user_id=user_id,
            activity_id=activity_id,
        )
        feedback.discomfort_description = description
        self._touch(feedback)
        await self._session.flush()
        return feedback

    async def save_discomfort_severity(
        self,
        *,
        user_id: uuid.UUID,
        activity_id: uuid.UUID,
        severity: DiscomfortSeverity | None,
    ) -> ActivityFeedback:
        feedback = await self.get_or_create_feedback(
            user_id=user_id,
            activity_id=activity_id,
        )
        feedback.discomfort_severity = severity
        self._touch(feedback)
        await self._session.flush()
        return feedback

    @staticmethod
    def _touch(feedback: ActivityFeedback) -> None:
        if feedback.feedback_created_at is None:
            feedback.feedback_created_at = utc_now()

    @staticmethod
    def _manual_can_be_canonical(activity: Activity) -> bool:
        if activity.heart_rate_reliable:
            return False
        if activity.average_heart_rate_source in {
            HeartRateSource.DERIVED,
        }:
            return False
        return True
