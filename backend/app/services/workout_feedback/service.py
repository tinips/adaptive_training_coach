"""Durable deterministic workout-feedback use cases."""

from __future__ import annotations

import re
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
    UserStatus,
    WorkoutFlowStep,
)
from app.repositories.errors import OwnedRecordNotFoundError
from app.repositories.users import UserRepository
from app.repositories.workout_feedback import WorkoutFeedbackRepository
from app.schemas.common import TelegramIdentity

_COMPLETED_PROFILE_STATUSES = {
    UserStatus.PROFILE_COMPLETED,
    UserStatus.BASELINE_PENDING,
    UserStatus.BASELINE_IMPORTING,
    UserStatus.BASELINE_READY,
    UserStatus.BASELINE_FAILED,
}
_TERMINAL_STATES = {
    WorkoutFlowStep.COMPLETE,
    WorkoutFlowStep.CANCELLED,
}
_RPE_OPTIONS: dict[str, tuple[int, str]] = {
    "VERY_EASY": (2, "Very easy"),
    "EASY": (4, "Easy"),
    "MODERATE": (6, "Moderate"),
    "HARD": (8, "Hard"),
    "VERY_HARD": (10, "Very hard"),
}
_BODY_AREA_ALIASES = {
    "SHOULDER": BodyArea.SHOULDER,
    "BACK": BodyArea.BACK,
    "HIP": BodyArea.HIP,
    "KNEE": BodyArea.KNEE,
    "ANKLE_FOOT": BodyArea.ANKLE_FOOT,
    "ANKLE_OR_FOOT": BodyArea.ANKLE_FOOT,
    "OTHER": BodyArea.OTHER,
}
_INTEGER_PATTERN = re.compile(r"[0-9]+")


class WorkoutFeedbackError(RuntimeError):
    """Safe, delivery-neutral workout-feedback failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ActivityFeedbackData(BaseModel):
    """Persisted subjective values safe for delivery rendering."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manual_average_heart_rate: int | None = None
    reported_rpe: int | None = None
    reported_rpe_label: str | None = None
    reported_discomfort: bool | None = None
    discomfort_body_area: BodyArea | None = None
    discomfort_severity: DiscomfortSeverity | None = None
    discomfort_description: str | None = None


class WorkoutFeedbackResult(BaseModel):
    """One durable flow snapshot returned to Telegram or another caller."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: uuid.UUID
    activity_id: uuid.UUID | None
    state: WorkoutFlowStep
    return_to_onboarding: bool
    pending_manual_average_heart_rate: int | None = Field(
        default=None,
        ge=30,
        le=250,
    )
    pending_discomfort_description: str | None = Field(
        default=None,
        max_length=500,
    )
    feedback: ActivityFeedbackData | None = None
    average_heart_rate: float | None = None
    average_heart_rate_source: HeartRateSource | None = None
    heart_rate_quality: HeartRateTemporalQuality | None = None
    heart_rate_reliable: bool | None = None
    completed: bool = False


class WorkoutFeedbackService:
    """Coordinate feedback state without relying on Telegram in-memory state."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def begin_waiting_upload(
        self,
        identity: TelegramIdentity,
    ) -> WorkoutFeedbackResult:
        """Begin or resume the post-onboarding wait for a supported file."""

        async with self._session_factory.begin() as session:
            user = await self._require_identity_user(session, identity)
            if user.status not in _COMPLETED_PROFILE_STATUSES:
                raise WorkoutFeedbackError("profile_incomplete")
            repository = WorkoutFeedbackRepository(session)
            await repository.require_owner(user_id=user.id, for_update=True)
            existing = await repository.get_flow(user_id=user.id, for_update=True)
            if existing is not None and existing.state not in _TERMINAL_STATES:
                return await self._result(repository, existing)
            flow = await repository.begin_waiting_for_file(user_id=user.id)
            return await self._result(repository, flow)

    async def start_for_activity(
        self,
        *,
        user_id: uuid.UUID,
        activity_id: uuid.UUID,
        return_to_onboarding: bool = False,
    ) -> WorkoutFeedbackResult:
        """Attach an owned activity and begin only the questions it needs."""

        async with self._session_factory.begin() as session:
            repository = WorkoutFeedbackRepository(session)
            await repository.require_owner(user_id=user_id, for_update=True)
            existing = await repository.get_flow(user_id=user_id, for_update=True)
            try:
                activity = await repository.require_activity(
                    user_id=user_id,
                    activity_id=activity_id,
                    for_update=True,
                )
            except OwnedRecordNotFoundError as exc:
                raise WorkoutFeedbackError("activity_not_found") from exc

            if existing is not None and existing.state not in _TERMINAL_STATES:
                if existing.activity_id == activity_id:
                    return await self._result(repository, existing)
                if (
                    existing.state is not WorkoutFlowStep.WAITING_FOR_FILE
                    or existing.activity_id is not None
                ):
                    raise WorkoutFeedbackError("workout_flow_already_active")
            if (
                existing is not None
                and existing.state is WorkoutFlowStep.COMPLETE
                and existing.activity_id == activity_id
            ):
                return await self._result(repository, existing)

            initial_state = (
                WorkoutFlowStep.RPE
                if self._has_reliable_average_heart_rate(activity)
                else WorkoutFlowStep.HR_OFFER
            )
            flow = await repository.begin_for_activity(
                user_id=user_id,
                activity_id=activity_id,
                initial_state=initial_state,
                return_to_onboarding=return_to_onboarding,
            )
            return await self._result(repository, flow)

    async def snapshot(
        self,
        identity: TelegramIdentity,
    ) -> WorkoutFeedbackResult | None:
        """Return the owned persisted flow after a restart, if one exists."""

        async with self._session_factory() as session:
            user = await self._require_identity_user(session, identity)
            repository = WorkoutFeedbackRepository(session)
            flow = await repository.get_flow(user_id=user.id)
            if flow is None:
                return None
            return await self._result(repository, flow)

    async def choose_manual_heart_rate(
        self,
        identity: TelegramIdentity,
        *,
        enter: bool,
        expected_state: WorkoutFlowStep = WorkoutFlowStep.HR_OFFER,
    ) -> WorkoutFeedbackResult:
        async with self._session_factory.begin() as session:
            repository, flow = await self._locked_flow(session, identity)
            replay = await self._replay(repository, flow, expected_state)
            if replay is not None:
                return replay
            next_state = WorkoutFlowStep.HR_ENTRY if enter else WorkoutFlowStep.RPE
            await repository.set_pending_manual_heart_rate(
                flow=flow,
                beats_per_minute=None,
            )
            await repository.set_state(flow=flow, state=next_state)
            return await self._result(repository, flow)

    async def continue_without_manual_heart_rate(
        self,
        identity: TelegramIdentity,
    ) -> WorkoutFeedbackResult:
        return await self.choose_manual_heart_rate(identity, enter=False)

    async def submit_manual_heart_rate(
        self,
        identity: TelegramIdentity,
        value: int | str,
    ) -> WorkoutFeedbackResult:
        beats_per_minute = self._manual_heart_rate(value)
        async with self._session_factory.begin() as session:
            repository, flow = await self._locked_flow(session, identity)
            if flow.state is not WorkoutFlowStep.HR_ENTRY:
                return await self._result(repository, flow)
            await repository.set_pending_manual_heart_rate(
                flow=flow,
                beats_per_minute=beats_per_minute,
            )
            await repository.set_state(flow=flow, state=WorkoutFlowStep.HR_CONFIRM)
            return await self._result(repository, flow)

    async def manual_heart_rate_confirmation(
        self,
        identity: TelegramIdentity,
        *,
        action: Literal["confirm", "change", "skip"],
        expected_state: WorkoutFlowStep = WorkoutFlowStep.HR_CONFIRM,
    ) -> WorkoutFeedbackResult:
        async with self._session_factory.begin() as session:
            repository, flow = await self._locked_flow(session, identity)
            replay = await self._replay(repository, flow, expected_state)
            if replay is not None:
                return replay
            if action == "change":
                await repository.set_pending_manual_heart_rate(
                    flow=flow,
                    beats_per_minute=None,
                )
                await repository.set_state(flow=flow, state=WorkoutFlowStep.HR_ENTRY)
                return await self._result(repository, flow)
            activity_id = self._activity_id(flow)
            if action == "confirm":
                pending = flow.pending_manual_average_heart_rate
                if pending is None:
                    raise WorkoutFeedbackError("manual_heart_rate_missing")
                await repository.save_manual_heart_rate(
                    user_id=flow.user_id,
                    activity_id=activity_id,
                    beats_per_minute=pending,
                )
            elif action == "skip":
                await repository.save_manual_heart_rate(
                    user_id=flow.user_id,
                    activity_id=activity_id,
                    beats_per_minute=None,
                )
            else:
                raise WorkoutFeedbackError("invalid_action")
            await repository.set_pending_manual_heart_rate(
                flow=flow,
                beats_per_minute=None,
            )
            await repository.set_state(flow=flow, state=WorkoutFlowStep.RPE)
            return await self._result(repository, flow)

    async def confirm_manual_heart_rate(
        self,
        identity: TelegramIdentity,
    ) -> WorkoutFeedbackResult:
        return await self.manual_heart_rate_confirmation(
            identity,
            action="confirm",
        )

    async def change_manual_heart_rate(
        self,
        identity: TelegramIdentity,
    ) -> WorkoutFeedbackResult:
        return await self.manual_heart_rate_confirmation(
            identity,
            action="change",
        )

    async def skip_manual_heart_rate(
        self,
        identity: TelegramIdentity,
    ) -> WorkoutFeedbackResult:
        return await self.manual_heart_rate_confirmation(
            identity,
            action="skip",
        )

    async def select_rpe(
        self,
        identity: TelegramIdentity,
        label: str,
        *,
        expected_state: WorkoutFlowStep = WorkoutFlowStep.RPE,
    ) -> WorkoutFeedbackResult:
        key = self._option_key(label)
        try:
            value, display_label = _RPE_OPTIONS[key]
        except KeyError as exc:
            raise WorkoutFeedbackError("invalid_rpe") from exc
        async with self._session_factory.begin() as session:
            repository, flow = await self._locked_flow(session, identity)
            replay = await self._replay(repository, flow, expected_state)
            if replay is not None:
                return replay
            await repository.save_rpe(
                user_id=flow.user_id,
                activity_id=self._activity_id(flow),
                value=value,
                label=display_label,
            )
            await repository.set_state(flow=flow, state=WorkoutFlowStep.DISCOMFORT)
            return await self._result(repository, flow)

    async def skip_rpe(
        self,
        identity: TelegramIdentity,
        *,
        expected_state: WorkoutFlowStep = WorkoutFlowStep.RPE,
    ) -> WorkoutFeedbackResult:
        async with self._session_factory.begin() as session:
            repository, flow = await self._locked_flow(session, identity)
            replay = await self._replay(repository, flow, expected_state)
            if replay is not None:
                return replay
            await repository.save_rpe(
                user_id=flow.user_id,
                activity_id=self._activity_id(flow),
                value=None,
                label=None,
            )
            await repository.set_state(flow=flow, state=WorkoutFlowStep.DISCOMFORT)
            return await self._result(repository, flow)

    async def select_discomfort(
        self,
        identity: TelegramIdentity,
        reported: bool | None,
        *,
        expected_state: WorkoutFlowStep = WorkoutFlowStep.DISCOMFORT,
    ) -> WorkoutFeedbackResult:
        if reported is not None and not isinstance(reported, bool):
            raise WorkoutFeedbackError("invalid_discomfort")
        async with self._session_factory.begin() as session:
            repository, flow = await self._locked_flow(session, identity)
            replay = await self._replay(repository, flow, expected_state)
            if replay is not None:
                return replay
            await repository.save_discomfort(
                user_id=flow.user_id,
                activity_id=self._activity_id(flow),
                reported=reported,
            )
            if reported is True:
                await repository.set_state(
                    flow=flow,
                    state=WorkoutFlowStep.BODY_AREA,
                )
            else:
                await repository.set_state(
                    flow=flow,
                    state=WorkoutFlowStep.COMPLETE,
                )
            return await self._result(repository, flow)

    async def report_no_discomfort(
        self,
        identity: TelegramIdentity,
    ) -> WorkoutFeedbackResult:
        return await self.select_discomfort(identity, False)

    async def report_discomfort(
        self,
        identity: TelegramIdentity,
    ) -> WorkoutFeedbackResult:
        return await self.select_discomfort(identity, True)

    async def skip_discomfort(
        self,
        identity: TelegramIdentity,
    ) -> WorkoutFeedbackResult:
        return await self.select_discomfort(identity, None)

    async def select_body_area(
        self,
        identity: TelegramIdentity,
        body_area: BodyArea | str | None,
        *,
        expected_state: WorkoutFlowStep = WorkoutFlowStep.BODY_AREA,
    ) -> WorkoutFeedbackResult:
        normalized = self._body_area(body_area)
        async with self._session_factory.begin() as session:
            repository, flow = await self._locked_flow(session, identity)
            replay = await self._replay(repository, flow, expected_state)
            if replay is not None:
                return replay
            await repository.save_body_area(
                user_id=flow.user_id,
                activity_id=self._activity_id(flow),
                body_area=normalized,
            )
            next_state = (
                WorkoutFlowStep.DESCRIPTION_ENTRY
                if normalized is BodyArea.OTHER
                else WorkoutFlowStep.SEVERITY
            )
            await repository.set_state(flow=flow, state=next_state)
            return await self._result(repository, flow)

    async def submit_discomfort_description(
        self,
        identity: TelegramIdentity,
        description: str,
    ) -> WorkoutFeedbackResult:
        normalized = self._description(description)
        async with self._session_factory.begin() as session:
            repository, flow = await self._locked_flow(session, identity)
            if flow.state is not WorkoutFlowStep.DESCRIPTION_ENTRY:
                return await self._result(repository, flow)
            await repository.set_pending_discomfort_description(
                flow=flow,
                description=normalized,
            )
            await repository.set_state(
                flow=flow,
                state=WorkoutFlowStep.DESCRIPTION_CONFIRM,
            )
            return await self._result(repository, flow)

    async def skip_body_area(
        self,
        identity: TelegramIdentity,
    ) -> WorkoutFeedbackResult:
        return await self.select_body_area(identity, None)

    async def discomfort_description_confirmation(
        self,
        identity: TelegramIdentity,
        *,
        action: Literal["confirm", "change", "skip"],
        expected_state: WorkoutFlowStep = WorkoutFlowStep.DESCRIPTION_CONFIRM,
    ) -> WorkoutFeedbackResult:
        async with self._session_factory.begin() as session:
            repository, flow = await self._locked_flow(session, identity)
            replay = await self._replay(repository, flow, expected_state)
            if replay is not None:
                return replay
            if action == "change":
                await repository.set_pending_discomfort_description(
                    flow=flow,
                    description=None,
                )
                await repository.set_state(
                    flow=flow,
                    state=WorkoutFlowStep.DESCRIPTION_ENTRY,
                )
                return await self._result(repository, flow)
            if action == "confirm":
                pending = flow.pending_discomfort_description
                if pending is None:
                    raise WorkoutFeedbackError("discomfort_description_missing")
                await repository.save_discomfort_description(
                    user_id=flow.user_id,
                    activity_id=self._activity_id(flow),
                    description=pending,
                )
            elif action == "skip":
                await repository.save_discomfort_description(
                    user_id=flow.user_id,
                    activity_id=self._activity_id(flow),
                    description=None,
                )
            else:
                raise WorkoutFeedbackError("invalid_action")
            await repository.set_pending_discomfort_description(
                flow=flow,
                description=None,
            )
            await repository.set_state(flow=flow, state=WorkoutFlowStep.SEVERITY)
            return await self._result(repository, flow)

    async def confirm_discomfort_description(
        self,
        identity: TelegramIdentity,
    ) -> WorkoutFeedbackResult:
        return await self.discomfort_description_confirmation(
            identity,
            action="confirm",
        )

    async def change_discomfort_description(
        self,
        identity: TelegramIdentity,
    ) -> WorkoutFeedbackResult:
        return await self.discomfort_description_confirmation(
            identity,
            action="change",
        )

    async def skip_discomfort_description(
        self,
        identity: TelegramIdentity,
    ) -> WorkoutFeedbackResult:
        return await self.discomfort_description_confirmation(
            identity,
            action="skip",
        )

    async def select_severity(
        self,
        identity: TelegramIdentity,
        severity: DiscomfortSeverity | str | None,
        *,
        expected_state: WorkoutFlowStep = WorkoutFlowStep.SEVERITY,
    ) -> WorkoutFeedbackResult:
        normalized = self._severity(severity)
        async with self._session_factory.begin() as session:
            repository, flow = await self._locked_flow(session, identity)
            replay = await self._replay(repository, flow, expected_state)
            if replay is not None:
                return replay
            await repository.save_discomfort_severity(
                user_id=flow.user_id,
                activity_id=self._activity_id(flow),
                severity=normalized,
            )
            await repository.set_state(flow=flow, state=WorkoutFlowStep.COMPLETE)
            return await self._result(repository, flow)

    async def skip_severity(
        self,
        identity: TelegramIdentity,
    ) -> WorkoutFeedbackResult:
        return await self.select_severity(identity, None)

    async def back(
        self,
        identity: TelegramIdentity,
        *,
        expected_state: WorkoutFlowStep | None = None,
    ) -> WorkoutFeedbackResult:
        async with self._session_factory.begin() as session:
            repository, flow = await self._locked_flow(session, identity)
            if expected_state is not None:
                replay = await self._replay(repository, flow, expected_state)
                if replay is not None:
                    return replay
            if flow.state in _TERMINAL_STATES:
                return await self._result(repository, flow)

            previous = {
                WorkoutFlowStep.WAITING_FOR_FILE: WorkoutFlowStep.CANCELLED,
                WorkoutFlowStep.HR_OFFER: WorkoutFlowStep.CANCELLED,
                WorkoutFlowStep.HR_ENTRY: WorkoutFlowStep.HR_OFFER,
                WorkoutFlowStep.HR_CONFIRM: WorkoutFlowStep.HR_ENTRY,
                WorkoutFlowStep.DISCOMFORT: WorkoutFlowStep.RPE,
                WorkoutFlowStep.BODY_AREA: WorkoutFlowStep.DISCOMFORT,
                WorkoutFlowStep.DESCRIPTION_ENTRY: WorkoutFlowStep.BODY_AREA,
                WorkoutFlowStep.DESCRIPTION_CONFIRM: (
                    WorkoutFlowStep.DESCRIPTION_ENTRY
                ),
                WorkoutFlowStep.SEVERITY: WorkoutFlowStep.BODY_AREA,
            }
            target: WorkoutFlowStep | None
            if flow.state is WorkoutFlowStep.RPE:
                activity = await repository.require_activity(
                    user_id=flow.user_id,
                    activity_id=self._activity_id(flow),
                )
                target = (
                    WorkoutFlowStep.CANCELLED
                    if self._has_reliable_average_heart_rate(activity)
                    else WorkoutFlowStep.HR_OFFER
                )
            else:
                target = previous.get(flow.state)
            if target is None:
                raise WorkoutFeedbackError("invalid_action")
            if flow.state is WorkoutFlowStep.HR_CONFIRM:
                await repository.set_pending_manual_heart_rate(
                    flow=flow,
                    beats_per_minute=None,
                )
            if flow.state is WorkoutFlowStep.DESCRIPTION_CONFIRM:
                await repository.set_pending_discomfort_description(
                    flow=flow,
                    description=None,
                )
            await repository.set_state(flow=flow, state=target)
            return await self._result(repository, flow)

    async def cancel(
        self,
        identity: TelegramIdentity,
    ) -> WorkoutFeedbackResult:
        async with self._session_factory.begin() as session:
            repository, flow = await self._locked_flow(session, identity)
            if flow.state not in _TERMINAL_STATES:
                await repository.set_state(
                    flow=flow,
                    state=WorkoutFlowStep.CANCELLED,
                )
            return await self._result(repository, flow)

    async def _locked_flow(
        self,
        session: AsyncSession,
        identity: TelegramIdentity,
    ) -> tuple[WorkoutFeedbackRepository, WorkoutFlowSession]:
        user = await self._require_identity_user(session, identity)
        repository = WorkoutFeedbackRepository(session)
        await repository.require_owner(user_id=user.id, for_update=True)
        try:
            flow = await repository.require_flow(
                user_id=user.id,
                for_update=True,
            )
        except OwnedRecordNotFoundError as exc:
            raise WorkoutFeedbackError("workout_flow_not_found") from exc
        return repository, flow

    async def _require_identity_user(
        self,
        session: AsyncSession,
        identity: TelegramIdentity,
    ) -> User:
        user = await UserRepository(session).get_by_telegram_id(
            identity.telegram_user_id
        )
        if user is None:
            raise WorkoutFeedbackError("user_not_found")
        return user

    async def _replay(
        self,
        repository: WorkoutFeedbackRepository,
        flow: WorkoutFlowSession,
        expected_state: WorkoutFlowStep,
    ) -> WorkoutFeedbackResult | None:
        if flow.state is expected_state:
            return None
        return await self._result(repository, flow)

    async def _result(
        self,
        repository: WorkoutFeedbackRepository,
        flow: WorkoutFlowSession,
    ) -> WorkoutFeedbackResult:
        feedback: ActivityFeedback | None = None
        activity: Activity | None = None
        if flow.activity_id is not None:
            feedback = await repository.get_feedback(
                user_id=flow.user_id,
                activity_id=flow.activity_id,
            )
            activity = await repository.get_activity(
                user_id=flow.user_id,
                activity_id=flow.activity_id,
            )
        return WorkoutFeedbackResult(
            user_id=flow.user_id,
            activity_id=flow.activity_id,
            state=flow.state,
            return_to_onboarding=flow.return_to_onboarding,
            pending_manual_average_heart_rate=(flow.pending_manual_average_heart_rate),
            pending_discomfort_description=flow.pending_discomfort_description,
            feedback=self._feedback_data(feedback),
            average_heart_rate=(
                activity.average_heart_rate if activity is not None else None
            ),
            average_heart_rate_source=(
                activity.average_heart_rate_source if activity is not None else None
            ),
            heart_rate_quality=(
                activity.heart_rate_quality if activity is not None else None
            ),
            heart_rate_reliable=(
                activity.heart_rate_reliable if activity is not None else None
            ),
            completed=flow.state in _TERMINAL_STATES,
        )

    @staticmethod
    def _feedback_data(
        feedback: ActivityFeedback | None,
    ) -> ActivityFeedbackData | None:
        if feedback is None:
            return None
        return ActivityFeedbackData(
            manual_average_heart_rate=feedback.manual_average_heart_rate,
            reported_rpe=feedback.reported_rpe,
            reported_rpe_label=feedback.reported_rpe_label,
            reported_discomfort=feedback.reported_discomfort,
            discomfort_body_area=feedback.discomfort_body_area,
            discomfort_severity=feedback.discomfort_severity,
            discomfort_description=feedback.discomfort_description,
        )

    @staticmethod
    def _has_reliable_average_heart_rate(activity: Activity) -> bool:
        return activity.average_heart_rate is not None and activity.heart_rate_reliable

    @staticmethod
    def _activity_id(flow: WorkoutFlowSession) -> uuid.UUID:
        if flow.activity_id is None:
            raise WorkoutFeedbackError("activity_not_selected")
        return flow.activity_id

    @staticmethod
    def _manual_heart_rate(value: int | str) -> int:
        if isinstance(value, bool):
            raise WorkoutFeedbackError("invalid_manual_heart_rate")
        if isinstance(value, int):
            beats_per_minute = value
        elif isinstance(value, str) and _INTEGER_PATTERN.fullmatch(value.strip()):
            beats_per_minute = int(value.strip())
        else:
            raise WorkoutFeedbackError("invalid_manual_heart_rate")
        if not 30 <= beats_per_minute <= 250:
            raise WorkoutFeedbackError("manual_heart_rate_out_of_range")
        return beats_per_minute

    @staticmethod
    def _option_key(value: str) -> str:
        return "_".join(value.strip().upper().replace("-", " ").split())

    @classmethod
    def _body_area(cls, value: BodyArea | str | None) -> BodyArea | None:
        if value is None:
            return None
        if isinstance(value, BodyArea):
            return value
        try:
            return _BODY_AREA_ALIASES[cls._option_key(value)]
        except KeyError as exc:
            raise WorkoutFeedbackError("invalid_body_area") from exc

    @staticmethod
    def _description(value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 500:
            raise WorkoutFeedbackError("invalid_discomfort_description")
        return normalized

    @classmethod
    def _severity(
        cls,
        value: DiscomfortSeverity | str | None,
    ) -> DiscomfortSeverity | None:
        if value is None:
            return None
        if isinstance(value, DiscomfortSeverity):
            return value
        try:
            return DiscomfortSeverity(cls._option_key(value))
        except ValueError as exc:
            raise WorkoutFeedbackError("invalid_discomfort_severity") from exc
