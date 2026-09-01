"""SQLAlchemy models for the onboarding and training-import vertical slice.

The models intentionally contain no business behavior.  PostgreSQL receives
JSONB for document-shaped fields while SQLite uses SQLAlchemy's portable JSON
type so focused repository tests can run without a database service.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    CHAR,
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utc_now
from app.domain.enums import (
    ActivitySource,
    AppleHealthImportStatus,
    AthleteCapabilityStatus,
    AthleteGender,
    CapabilityImportance,
    CapabilityKind,
    CatalogItemSource,
    CatalogItemStatus,
    CyclingType,
    Discipline,
    ExecutionOptionRole,
    GoalContextRole,
    GoalTemplateKind,
    HeartRateTemporalQuality,
    HikingType,
    LLMUsageStatus,
    OnboardingStatus,
    OnboardingStep,
    ProfileSettingsStep,
    RunningType,
    StrengthType,
    SwimmingEnvironment,
    SwimmingStroke,
    TrainingFileFormat,
    TrainingImportContext,
    UserStatus,
)


class LLMProviderMode(StrEnum):
    """Persisted model-provider mode without storing provider credentials."""

    MOCK = "mock"
    LIVE = "live"


def _enum_values(enum_class: type[StrEnum]) -> list[str]:
    """Persist enum values rather than Python member names."""

    return [member.value for member in enum_class]


def persisted_enum(
    enum_class: type[StrEnum],
    *,
    name: str,
    length: int | None = None,
) -> SAEnum:
    """Return one portable, constrained SQL enum representation."""

    return SAEnum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=_enum_values,
        length=length,
    )


def json_document() -> JSON:
    """Use JSONB on PostgreSQL and JSON on portable test databases."""

    return JSON().with_variant(JSONB(), "postgresql")


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Stable Telegram identity and product lifecycle state."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint(
            "telegram_user_id",
            name="uq_users_telegram_user_id",
        ),
        Index("ix_users_telegram_user_id", "telegram_user_id"),
    )

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_username: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language_code: Mapped[str] = mapped_column(
        String(16),
        default="en",
        server_default="en",
        nullable=False,
    )
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[UserStatus] = mapped_column(
        persisted_enum(UserStatus, name="user_status", length=32),
        default=UserStatus.NEW,
        server_default=UserStatus.NEW.value,
        nullable=False,
    )

    onboarding_session: Mapped[OnboardingSession | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        uselist=False,
    )
    profile_settings_session: Mapped[ProfileSettingsSession | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        uselist=False,
    )
    athlete_profile: Mapped[AthleteProfile | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        uselist=False,
    )
    training_goal: Mapped[TrainingGoal | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        uselist=False,
    )
    workouts: Mapped[list[Workout]] = relationship(
        back_populates="athlete",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
    self_reported_baseline: Mapped[AthleteSelfReportedBaseline | None] = relationship(
        back_populates="athlete",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        uselist=False,
    )
    weekly_training_plans: Mapped[list[WeeklyTrainingPlan]] = relationship(
        back_populates="athlete",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
    apple_health_import_jobs: Mapped[list[AppleHealthImportJob]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
    activity_source_links: Mapped[list[ActivitySourceLink]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
    heart_rate_observations: Mapped[list[WorkoutHeartRateObservation]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
    llm_usage: Mapped[list[LLMUsage]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )


class OnboardingSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable source of truth for resumable onboarding."""

    __tablename__ = "onboarding_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_onboarding_sessions_user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[OnboardingStatus] = mapped_column(
        persisted_enum(
            OnboardingStatus,
            name="onboarding_status",
            length=16,
        ),
        default=OnboardingStatus.ACTIVE,
        server_default=OnboardingStatus.ACTIVE.value,
        nullable=False,
    )
    current_step: Mapped[OnboardingStep] = mapped_column(
        persisted_enum(OnboardingStep, name="onboarding_step", length=32),
        default=OnboardingStep.CONSENT,
        server_default=OnboardingStep.CONSENT.value,
        nullable=False,
    )
    answers: Mapped[dict[str, object]] = mapped_column(
        json_document(),
        default=dict,
        nullable=False,
    )
    user: Mapped[User] = relationship(
        back_populates="onboarding_session",
        lazy="raise",
    )
    import_jobs: Mapped[list[AppleHealthImportJob]] = relationship(
        back_populates="onboarding_session",
        passive_deletes=True,
        lazy="raise",
    )


class ProfileSettingsSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Small durable checkpoint for a completed athlete's settings mini-flow."""

    __tablename__ = "profile_settings_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_profile_settings_sessions_user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    current_step: Mapped[ProfileSettingsStep] = mapped_column(
        persisted_enum(ProfileSettingsStep, name="profile_settings_step", length=32),
        default=ProfileSettingsStep.MENU,
        server_default=ProfileSettingsStep.MENU.value,
        nullable=False,
    )
    pending_answers: Mapped[dict[str, object]] = mapped_column(
        json_document(), default=dict, nullable=False
    )
    user: Mapped[User] = relationship(
        back_populates="profile_settings_session", lazy="raise"
    )


class AthleteProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Athlete demographics, primary discipline, and raw onboarding context."""

    __tablename__ = "athlete_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_athlete_profiles_user_id"),
        CheckConstraint(
            "birth_year IS NULL OR (birth_year >= 1940 AND birth_year <= 2008)",
            name="birth_year_range",
        ),
        CheckConstraint(
            "gender IS NULL OR gender IN ('MALE', 'FEMALE')",
            name="athlete_gender",
        ),
        CheckConstraint(
            "height_cm IS NULL OR (height_cm >= 120 AND height_cm <= 230)",
            name="height_cm_range",
        ),
        CheckConstraint(
            "weight_kg IS NULL OR (weight_kg >= 35 AND weight_kg <= 250)",
            name="weight_kg_range",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    birth_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    gender: Mapped[AthleteGender | None] = mapped_column(
        persisted_enum(AthleteGender, name="athlete_gender", length=24),
        nullable=True,
    )
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    availability_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    weekly_availability_jsonb: Mapped[dict[str, object] | None] = mapped_column(
        json_document(), nullable=True
    )
    health_limitations_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(
        back_populates="athlete_profile",
        lazy="raise",
    )


class TrainingGoal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The user's single current primary goal."""

    __tablename__ = "training_goals"
    __table_args__ = (UniqueConstraint("user_id", name="uq_training_goals_user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    main_goal: Mapped[str] = mapped_column(String(500), nullable=False)
    target_distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_elevation_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_pace_seconds_per_km: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    target_swim_pace_seconds_per_100m: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    target_average_speed_kph: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_finish_time_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    goal_metadata_jsonb: Mapped[dict[str, object] | None] = mapped_column(
        json_document(), nullable=True
    )
    secondary_priority: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    goal_template_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("goal_templates.id", ondelete="RESTRICT"),
        nullable=True,
    )
    supporting_goal_template_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("goal_templates.id", ondelete="RESTRICT"),
        nullable=True,
    )
    user: Mapped[User] = relationship(
        back_populates="training_goal",
        lazy="raise",
    )


class GoalTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Reusable primary or supporting goal understood by the product."""

    __tablename__ = "goal_templates"
    __table_args__ = (
        UniqueConstraint("code", name="uq_goal_templates_code"),
        CheckConstraint(
            "code = upper(code) AND length(code) BETWEEN 3 AND 64",
            name="goal_template_code",
        ),
        CheckConstraint("definition_version > 0", name="definition_version_positive"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[GoalTemplateKind] = mapped_column(
        persisted_enum(GoalTemplateKind, name="goal_template_kind", length=16),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[CatalogItemSource] = mapped_column(
        persisted_enum(CatalogItemSource, name="catalog_item_source", length=16),
        nullable=False,
    )
    status: Mapped[CatalogItemStatus] = mapped_column(
        persisted_enum(CatalogItemStatus, name="catalog_item_status", length=16),
        nullable=False,
    )
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class TrainingContext(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Planning context more specific than an imported workout discipline."""

    __tablename__ = "training_contexts"
    __table_args__ = (
        UniqueConstraint("code", name="uq_training_contexts_code"),
        CheckConstraint(
            "code = lower(code) AND length(code) BETWEEN 3 AND 64",
            name="training_context_code",
        ),
        CheckConstraint("definition_version > 0", name="definition_version_positive"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    discipline: Mapped[Discipline] = mapped_column(
        persisted_enum(Discipline, name="training_context_discipline", length=16),
        nullable=False,
    )
    source: Mapped[CatalogItemSource] = mapped_column(
        persisted_enum(CatalogItemSource, name="catalog_item_source", length=16),
        nullable=False,
    )
    status: Mapped[CatalogItemStatus] = mapped_column(
        persisted_enum(CatalogItemStatus, name="catalog_item_status", length=16),
        nullable=False,
    )
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class GoalTemplateContext(TimestampMixin, Base):
    __tablename__ = "goal_template_contexts"
    __table_args__ = (CheckConstraint("priority >= 0", name="priority_nonnegative"),)

    goal_template_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("goal_templates.id", ondelete="CASCADE"),
        primary_key=True,
    )
    training_context_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("training_contexts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[GoalContextRole] = mapped_column(
        persisted_enum(GoalContextRole, name="goal_context_role", length=16),
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False)


class Capability(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Global equipment, access, or facility capability."""

    __tablename__ = "capabilities"
    __table_args__ = (
        UniqueConstraint("code", name="uq_capabilities_code"),
        CheckConstraint(
            "code = lower(code) AND length(code) BETWEEN 3 AND 64",
            name="capability_code",
        ),
        CheckConstraint("definition_version > 0", name="definition_version_positive"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    kind: Mapped[CapabilityKind] = mapped_column(
        persisted_enum(CapabilityKind, name="capability_kind", length=16),
        nullable=False,
    )
    source: Mapped[CatalogItemSource] = mapped_column(
        persisted_enum(CatalogItemSource, name="catalog_item_source", length=16),
        nullable=False,
    )
    status: Mapped[CatalogItemStatus] = mapped_column(
        persisted_enum(CatalogItemStatus, name="catalog_item_status", length=16),
        nullable=False,
    )
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ContextExecutionOption(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One preferred or substitute way to execute a target context."""

    __tablename__ = "context_execution_options"
    __table_args__ = (
        UniqueConstraint(
            "target_context_id",
            "code",
            name="uq_context_execution_options_context_code",
        ),
        CheckConstraint("priority >= 0", name="priority_nonnegative"),
        CheckConstraint(
            "code = lower(code) AND length(code) BETWEEN 3 AND 64",
            name="execution_option_code",
        ),
    )

    target_context_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("training_contexts.id", ondelete="CASCADE"),
        nullable=False,
    )
    execution_context_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("training_contexts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[ExecutionOptionRole] = mapped_column(
        persisted_enum(ExecutionOptionRole, name="execution_option_role", length=16),
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    limitations: Mapped[list[str]] = mapped_column(
        json_document(), default=list, nullable=False
    )


class ExecutionOptionCapability(TimestampMixin, Base):
    __tablename__ = "execution_option_capabilities"

    execution_option_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("context_execution_options.id", ondelete="CASCADE"),
        primary_key=True,
    )
    capability_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("capabilities.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    importance: Mapped[CapabilityImportance] = mapped_column(
        persisted_enum(
            CapabilityImportance,
            name="execution_capability_importance",
            length=16,
        ),
        nullable=False,
    )


class AthleteCapability(TimestampMixin, Base):
    """One athlete's explicit current answer for a global capability."""

    __tablename__ = "athlete_capabilities"
    __table_args__ = (Index("ix_athlete_capabilities_athlete_id", "athlete_id"),)

    athlete_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    capability_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("capabilities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[AthleteCapabilityStatus] = mapped_column(
        persisted_enum(
            AthleteCapabilityStatus,
            name="athlete_capability_status",
            length=16,
        ),
        nullable=False,
    )


class Workout(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Universal workout identity; discipline metrics live in one detail row."""

    __tablename__ = "workouts"
    __table_args__ = (
        UniqueConstraint(
            "athlete_id",
            "source",
            "external_id",
            name="uq_workouts_athlete_source_external_id",
        ),
        Index("ix_workouts_athlete_started_at", "athlete_id", "started_at"),
        Index(
            "ix_workouts_athlete_discipline_started_at",
            "athlete_id",
            "discipline",
            "started_at",
        ),
        CheckConstraint("duration_seconds > 0", name="duration_positive"),
    )

    athlete_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    discipline: Mapped[Discipline] = mapped_column(
        persisted_enum(Discipline, name="workout_discipline", length=16),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    fitness_input_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[ActivitySource] = mapped_column(
        persisted_enum(ActivitySource, name="workout_source", length=16),
        nullable=False,
    )
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    athlete: Mapped[User] = relationship(back_populates="workouts", lazy="raise")
    running_details: Mapped[RunningWorkoutDetails | None] = relationship(
        back_populates="workout",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        uselist=False,
    )
    cycling_details: Mapped[CyclingWorkoutDetails | None] = relationship(
        back_populates="workout",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        uselist=False,
    )
    hiking_details: Mapped[HikingWorkoutDetails | None] = relationship(
        back_populates="workout",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        uselist=False,
    )
    swimming_details: Mapped[SwimmingWorkoutDetails | None] = relationship(
        back_populates="workout",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        uselist=False,
    )
    strength_details: Mapped[StrengthWorkoutDetails | None] = relationship(
        back_populates="workout",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        uselist=False,
    )
    other_details: Mapped[OtherWorkoutDetails | None] = relationship(
        back_populates="workout",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        uselist=False,
    )
    source_links: Mapped[list[ActivitySourceLink]] = relationship(
        back_populates="workout",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )
    import_jobs: Mapped[list[AppleHealthImportJob]] = relationship(
        back_populates="workout",
        passive_deletes=True,
        lazy="raise",
    )
    heart_rate_observations: Mapped[list[WorkoutHeartRateObservation]] = relationship(
        back_populates="workout",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )

    @property
    def user_id(self) -> uuid.UUID:
        """Compatibility alias for the pre-0004 ownership vocabulary."""

        return self.athlete_id

    @user_id.setter
    def user_id(self, value: uuid.UUID) -> None:
        self.athlete_id = value

    @property
    def sport(self) -> Discipline:
        """Compatibility alias; persistence uses ``discipline``."""

        return self.discipline

    @sport.setter
    def sport(self, value: Discipline) -> None:
        self.discipline = value

    @property
    def name(self) -> str | None:
        """Compatibility alias; persistence uses nullable ``title``."""

        return self.title

    @name.setter
    def name(self, value: str | None) -> None:
        self.title = value



class AthleteSelfReportedBaseline(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Current goal-scoped baseline supplied during onboarding.

    This record is explicitly self-reported and can be replaced after a goal change.
    """

    __tablename__ = "athlete_self_reported_baselines"
    __table_args__ = (
        UniqueConstraint(
            "athlete_id", name="uq_athlete_self_reported_baselines_athlete"
        ),
        CheckConstraint("form_version > 0", name="baseline_form_version_positive"),
    )

    athlete_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    goal_signature: Mapped[str] = mapped_column(String(128), nullable=False)
    form_version: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    baseline_jsonb: Mapped[dict[str, object]] = mapped_column(
        json_document(), nullable=False
    )
    athlete: Mapped[User] = relationship(
        back_populates="self_reported_baseline",
        lazy="raise",
    )


class WeeklyTrainingPlan(UUIDPrimaryKeyMixin, Base):
    """One immutable published Monday-to-Sunday plan for an athlete."""

    __tablename__ = "weekly_training_plans"
    __table_args__ = (
        UniqueConstraint(
            "athlete_id",
            "week_start",
            "revision",
            name="uq_weekly_training_plans_athlete_week_revision",
        ),
        CheckConstraint(
            "calculation_version > 0",
            name="calculation_version_positive",
        ),
        CheckConstraint(
            "prompt_version > 0",
            name="prompt_version_positive",
        ),
    )

    athlete_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    plan_jsonb: Mapped[dict[str, object]] = mapped_column(
        json_document(),
        nullable=False,
    )
    evidence_snapshot_jsonb: Mapped[dict[str, object]] = mapped_column(
        json_document(),
        nullable=False,
    )
    input_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    calculation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    planner_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    athlete: Mapped[User] = relationship(
        back_populates="weekly_training_plans",
        lazy="raise",
    )


class RunningWorkoutDetails(Base):
    """Metrics and subtype for a running workout."""

    __tablename__ = "running_workout_details"
    __table_args__ = (
        CheckConstraint(
            "distance_meters IS NULL OR distance_meters >= 0",
            name="distance_nonnegative",
        ),
        CheckConstraint(
            "moving_duration_seconds IS NULL OR moving_duration_seconds >= 0",
            name="moving_duration_nonnegative",
        ),
        CheckConstraint(
            "calories_kcal IS NULL OR calories_kcal >= 0",
            name="calories_nonnegative",
        ),
        CheckConstraint(
            "average_pace_seconds_per_km IS NULL OR average_pace_seconds_per_km >= 0",
            name="average_pace_nonnegative",
        ),
        CheckConstraint(
            "elevation_gain_meters IS NULL OR elevation_gain_meters >= 0",
            name="elevation_gain_nonnegative",
        ),
        CheckConstraint(
            "elevation_loss_meters IS NULL OR elevation_loss_meters >= 0",
            name="elevation_loss_nonnegative",
        ),
        CheckConstraint(
            "average_heart_rate IS NULL OR average_heart_rate >= 0",
            name="average_heart_rate_nonnegative",
        ),
        CheckConstraint(
            "max_heart_rate IS NULL OR max_heart_rate >= 0",
            name="max_heart_rate_nonnegative",
        ),
        CheckConstraint(
            "average_cadence_spm IS NULL OR average_cadence_spm >= 0",
            name="average_cadence_nonnegative",
        ),
        CheckConstraint(
            "max_cadence_spm IS NULL OR max_cadence_spm >= 0",
            name="max_cadence_nonnegative",
        ),
    )

    workout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workouts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    running_type: Mapped[RunningType] = mapped_column(
        persisted_enum(RunningType, name="running_type", length=16),
        nullable=False,
    )
    distance_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    moving_duration_seconds: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    calories_kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_pace_seconds_per_km: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    elevation_gain_meters: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    elevation_loss_meters: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    average_heart_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_heart_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_cadence_spm: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_cadence_spm: Mapped[float | None] = mapped_column(Float, nullable=True)

    workout: Mapped[Workout] = relationship(
        back_populates="running_details",
        lazy="raise",
    )


class CyclingWorkoutDetails(Base):
    """Metrics and subtype for a cycling workout."""

    __tablename__ = "cycling_workout_details"
    __table_args__ = (
        CheckConstraint(
            "distance_meters IS NULL OR distance_meters >= 0",
            name="distance_nonnegative",
        ),
        CheckConstraint(
            "moving_duration_seconds IS NULL OR moving_duration_seconds >= 0",
            name="moving_duration_nonnegative",
        ),
        CheckConstraint(
            "calories_kcal IS NULL OR calories_kcal >= 0",
            name="calories_nonnegative",
        ),
        CheckConstraint(
            "average_speed_kph IS NULL OR average_speed_kph >= 0",
            name="average_speed_nonnegative",
        ),
        CheckConstraint(
            "max_speed_kph IS NULL OR max_speed_kph >= 0",
            name="max_speed_nonnegative",
        ),
        CheckConstraint(
            "elevation_gain_meters IS NULL OR elevation_gain_meters >= 0",
            name="elevation_gain_nonnegative",
        ),
        CheckConstraint(
            "elevation_loss_meters IS NULL OR elevation_loss_meters >= 0",
            name="elevation_loss_nonnegative",
        ),
        CheckConstraint(
            "average_heart_rate IS NULL OR average_heart_rate >= 0",
            name="average_heart_rate_nonnegative",
        ),
        CheckConstraint(
            "max_heart_rate IS NULL OR max_heart_rate >= 0",
            name="max_heart_rate_nonnegative",
        ),
        CheckConstraint(
            "average_cadence_rpm IS NULL OR average_cadence_rpm >= 0",
            name="average_cadence_nonnegative",
        ),
        CheckConstraint(
            "max_cadence_rpm IS NULL OR max_cadence_rpm >= 0",
            name="max_cadence_nonnegative",
        ),
    )

    workout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workouts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    cycling_type: Mapped[CyclingType] = mapped_column(
        persisted_enum(CyclingType, name="cycling_type", length=16),
        nullable=False,
    )
    distance_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    moving_duration_seconds: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    calories_kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_speed_kph: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_speed_kph: Mapped[float | None] = mapped_column(Float, nullable=True)
    elevation_gain_meters: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    elevation_loss_meters: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    average_heart_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_heart_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_cadence_rpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_cadence_rpm: Mapped[float | None] = mapped_column(Float, nullable=True)

    workout: Mapped[Workout] = relationship(
        back_populates="cycling_details",
        lazy="raise",
    )


class HikingWorkoutDetails(Base):
    """Metrics and subtype for a hiking workout."""

    __tablename__ = "hiking_workout_details"
    __table_args__ = (
        CheckConstraint(
            "distance_meters IS NULL OR distance_meters >= 0",
            name="distance_nonnegative",
        ),
        CheckConstraint(
            "moving_duration_seconds IS NULL OR moving_duration_seconds >= 0",
            name="moving_duration_nonnegative",
        ),
        CheckConstraint(
            "calories_kcal IS NULL OR calories_kcal >= 0",
            name="calories_nonnegative",
        ),
        CheckConstraint(
            "average_pace_seconds_per_km IS NULL OR average_pace_seconds_per_km >= 0",
            name="average_pace_nonnegative",
        ),
        CheckConstraint(
            "elevation_gain_meters IS NULL OR elevation_gain_meters >= 0",
            name="elevation_gain_nonnegative",
        ),
        CheckConstraint(
            "elevation_loss_meters IS NULL OR elevation_loss_meters >= 0",
            name="elevation_loss_nonnegative",
        ),
        CheckConstraint(
            "average_heart_rate IS NULL OR average_heart_rate >= 0",
            name="average_heart_rate_nonnegative",
        ),
        CheckConstraint(
            "max_heart_rate IS NULL OR max_heart_rate >= 0",
            name="max_heart_rate_nonnegative",
        ),
    )

    workout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workouts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    hiking_type: Mapped[HikingType] = mapped_column(
        persisted_enum(HikingType, name="hiking_type", length=20),
        nullable=False,
    )
    distance_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    moving_duration_seconds: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    calories_kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_pace_seconds_per_km: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    elevation_gain_meters: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    elevation_loss_meters: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    average_heart_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_heart_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    workout: Mapped[Workout] = relationship(
        back_populates="hiking_details",
        lazy="raise",
    )


class SwimmingWorkoutDetails(Base):
    """Common metrics for a pool or open-water swim."""

    __tablename__ = "swimming_workout_details"
    __table_args__ = (
        CheckConstraint(
            "distance_meters IS NULL OR distance_meters >= 0",
            name="distance_nonnegative",
        ),
        CheckConstraint(
            "moving_duration_seconds IS NULL OR moving_duration_seconds >= 0",
            name="moving_duration_nonnegative",
        ),
        CheckConstraint(
            "calories_kcal IS NULL OR calories_kcal >= 0",
            name="calories_nonnegative",
        ),
        CheckConstraint(
            "average_pace_seconds_per_100m IS NULL "
            "OR average_pace_seconds_per_100m >= 0",
            name="average_pace_nonnegative",
        ),
        CheckConstraint(
            "average_heart_rate IS NULL OR average_heart_rate >= 0",
            name="average_heart_rate_nonnegative",
        ),
        CheckConstraint(
            "max_heart_rate IS NULL OR max_heart_rate >= 0",
            name="max_heart_rate_nonnegative",
        ),
    )

    workout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workouts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    swimming_environment: Mapped[SwimmingEnvironment] = mapped_column(
        persisted_enum(
            SwimmingEnvironment,
            name="swimming_environment",
            length=16,
        ),
        nullable=False,
    )
    distance_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    moving_duration_seconds: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    calories_kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_pace_seconds_per_100m: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    average_heart_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_heart_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    workout: Mapped[Workout] = relationship(
        back_populates="swimming_details",
        lazy="raise",
    )
    pool_details: Mapped[PoolSwimmingDetails | None] = relationship(
        back_populates="swimming_details",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        uselist=False,
    )


class PoolSwimmingDetails(Base):
    """Additional structure required for a pool swim."""

    __tablename__ = "pool_swimming_details"
    __table_args__ = (
        CheckConstraint(
            "pool_length_meters > 0",
            name="pool_length_positive",
        ),
        CheckConstraint(
            "total_lengths IS NULL OR total_lengths >= 0",
            name="total_lengths_nonnegative",
        ),
        CheckConstraint(
            "total_strokes IS NULL OR total_strokes >= 0",
            name="total_strokes_nonnegative",
        ),
    )

    workout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workouts.id", ondelete="CASCADE"),
        ForeignKey("swimming_workout_details.workout_id", ondelete="CASCADE"),
        primary_key=True,
    )
    pool_length_meters: Mapped[float] = mapped_column(Float, nullable=False)
    total_lengths: Mapped[int | None] = mapped_column(Integer, nullable=True)
    primary_stroke: Mapped[SwimmingStroke | None] = mapped_column(
        persisted_enum(SwimmingStroke, name="swimming_stroke", length=16),
        nullable=True,
    )
    total_strokes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    swimming_details: Mapped[SwimmingWorkoutDetails] = relationship(
        back_populates="pool_details",
        lazy="raise",
    )


class StrengthWorkoutDetails(Base):
    """Validated JSON exercise structure for one strength workout."""

    __tablename__ = "strength_workout_details"
    __table_args__ = (
        CheckConstraint(
            "calories_kcal IS NULL OR calories_kcal >= 0",
            name="calories_nonnegative",
        ),
    )

    workout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workouts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    strength_type: Mapped[StrengthType] = mapped_column(
        persisted_enum(StrengthType, name="strength_type", length=16),
        nullable=False,
    )
    calories_kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    session_focus: Mapped[str | None] = mapped_column(String(255), nullable=True)
    exercises_jsonb: Mapped[list[dict[str, object]]] = mapped_column(
        json_document(),
        default=list,
        nullable=False,
    )

    workout: Mapped[Workout] = relationship(
        back_populates="strength_details",
        lazy="raise",
    )


class OtherWorkoutDetails(Base):
    """User-readable fallback retaining raw sport labels and extra metrics."""

    __tablename__ = "other_workout_details"
    __table_args__ = (
        CheckConstraint(
            "distance_meters IS NULL OR distance_meters >= 0",
            name="distance_nonnegative",
        ),
        CheckConstraint(
            "average_heart_rate IS NULL OR average_heart_rate >= 0",
            name="average_heart_rate_nonnegative",
        ),
        CheckConstraint(
            "max_heart_rate IS NULL OR max_heart_rate >= 0",
            name="max_heart_rate_nonnegative",
        ),
        CheckConstraint(
            "calories_kcal IS NULL OR calories_kcal >= 0",
            name="calories_nonnegative",
        ),
    )

    workout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workouts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    activity_name: Mapped[str] = mapped_column(String(255), nullable=False)
    activity_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_sport: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_sub_sport: Mapped[str | None] = mapped_column(String(128), nullable=True)
    distance_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_heart_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_heart_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    calories_kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics_jsonb: Mapped[dict[str, object] | None] = mapped_column(
        json_document(),
        nullable=True,
    )

    workout: Mapped[Workout] = relationship(
        back_populates="other_details",
        lazy="raise",
    )


class AppleHealthImportJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable metadata and safe outcome for one Telegram Apple Health upload."""

    __tablename__ = "apple_health_import_jobs"
    __table_args__ = (
        Index(
            "uq_apple_health_import_jobs_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('RECEIVED', 'PROCESSING')"),
            sqlite_where=text("status IN ('RECEIVED', 'PROCESSING')"),
        ),
        Index(
            "ix_apple_health_import_jobs_user_created",
            "user_id",
            "created_at",
        ),
        Index(
            "ix_apple_health_import_jobs_user_file_sha256",
            "user_id",
            "file_sha256",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    context: Mapped[TrainingImportContext] = mapped_column(
        persisted_enum(
            TrainingImportContext,
            name="training_import_context",
            length=24,
        ),
        default=TrainingImportContext.POST_ONBOARDING,
        server_default=TrainingImportContext.POST_ONBOARDING.value,
        nullable=False,
    )
    onboarding_session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    workout_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workouts.id", ondelete="SET NULL"),
        nullable=True,
    )
    telegram_update_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    telegram_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    telegram_file_unique_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    display_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    temporary_path: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_format: Mapped[TrainingFileFormat] = mapped_column(
        persisted_enum(
            TrainingFileFormat,
            name="training_file_format",
            length=24,
        ),
        default=TrainingFileFormat.APPLE_HEALTH_ZIP,
        server_default=TrainingFileFormat.APPLE_HEALTH_ZIP.value,
        nullable=False,
    )
    status: Mapped[AppleHealthImportStatus] = mapped_column(
        persisted_enum(
            AppleHealthImportStatus,
            name="apple_health_import_status",
            length=16,
        ),
        default=AppleHealthImportStatus.RECEIVED,
        server_default=AppleHealthImportStatus.RECEIVED.value,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    workouts_found: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    activities_imported: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    activities_updated: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    activities_skipped: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    heart_rate_records_matched: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    warning_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    safe_error_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    user: Mapped[User] = relationship(
        back_populates="apple_health_import_jobs",
        lazy="raise",
    )
    onboarding_session: Mapped[OnboardingSession | None] = relationship(
        back_populates="import_jobs",
        lazy="raise",
    )
    workout: Mapped[Workout | None] = relationship(
        back_populates="import_jobs",
        lazy="raise",
    )
    source_links: Mapped[list[ActivitySourceLink]] = relationship(
        back_populates="import_job",
        passive_deletes=True,
        lazy="raise",
    )
    heart_rate_observations: Mapped[list[WorkoutHeartRateObservation]] = relationship(
        back_populates="import_job",
        passive_deletes=True,
        lazy="raise",
    )

    @property
    def activity_id(self) -> uuid.UUID | None:
        return self.workout_id

    @activity_id.setter
    def activity_id(self, value: uuid.UUID | None) -> None:
        self.workout_id = value


class ActivitySourceLink(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One owner-scoped provider key and its raw workout metadata."""

    __tablename__ = "activity_source_links"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source",
            "external_id",
            name="uq_activity_source_links_user_source_external_id",
        ),
        Index(
            "ix_activity_source_links_workout_id",
            "workout_id",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    workout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workouts.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[ActivitySource] = mapped_column(
        persisted_enum(
            ActivitySource,
            name="activity_source_link_source",
            length=16,
        ),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_sport: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_sub_sport: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_metadata_jsonb: Mapped[dict[str, object] | None] = mapped_column(
        json_document(),
        nullable=True,
    )
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    import_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("apple_health_import_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    user: Mapped[User] = relationship(
        back_populates="activity_source_links",
        lazy="raise",
    )
    workout: Mapped[Workout] = relationship(
        back_populates="source_links",
        lazy="raise",
    )
    import_job: Mapped[AppleHealthImportJob | None] = relationship(
        back_populates="source_links",
        lazy="raise",
    )

    @property
    def activity_id(self) -> uuid.UUID:
        return self.workout_id

    @activity_id.setter
    def activity_id(self, value: uuid.UUID) -> None:
        self.workout_id = value


class WorkoutHeartRateObservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Quality-labelled source observation retained for future recalculation."""

    __tablename__ = "workout_heart_rate_observations"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "source",
            "source_record_key",
            name="uq_workout_hr_observations_user_source_key",
        ),
        Index(
            "ix_workout_hr_observations_workout_started",
            "workout_id",
            "started_at",
        ),
        CheckConstraint("beats_per_minute > 0", name="beats_per_minute_positive"),
        CheckConstraint("ended_at >= started_at", name="observation_period_order"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    workout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workouts.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[ActivitySource] = mapped_column(
        persisted_enum(
            ActivitySource,
            name="heart_rate_observation_source",
            length=16,
        ),
        nullable=False,
    )
    source_record_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    beats_per_minute: Mapped[float] = mapped_column(Float, nullable=False)
    temporal_quality: Mapped[HeartRateTemporalQuality] = mapped_column(
        persisted_enum(
            HeartRateTemporalQuality,
            name="heart_rate_observation_quality",
            length=24,
        ),
        nullable=False,
    )
    import_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("apple_health_import_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    user: Mapped[User] = relationship(
        back_populates="heart_rate_observations",
        lazy="raise",
    )
    workout: Mapped[Workout] = relationship(
        back_populates="heart_rate_observations",
        lazy="raise",
    )
    import_job: Mapped[AppleHealthImportJob | None] = relationship(
        back_populates="heart_rate_observations",
        lazy="raise",
    )


class LLMUsage(UUIDPrimaryKeyMixin, Base):
    """Safe rate-limit and usage metadata; never raw prompts or answers."""

    __tablename__ = "llm_usage"
    __table_args__ = (
        Index("ix_llm_usage_user_created", "user_id", "created_at"),
        CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0",
            name="prompt_tokens_nonnegative",
        ),
        CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="completion_tokens_nonnegative",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    onboarding_step: Mapped[OnboardingStep] = mapped_column(
        persisted_enum(
            OnboardingStep,
            name="llm_onboarding_step",
            length=32,
        ),
        nullable=False,
    )
    feature: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_mode: Mapped[LLMProviderMode] = mapped_column(
        persisted_enum(
            LLMProviderMode,
            name="llm_provider_mode",
            length=8,
        ),
        nullable=False,
    )
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[LLMUsageStatus] = mapped_column(
        persisted_enum(
            LLMUsageStatus,
            name="llm_usage_status",
            length=24,
        ),
        nullable=False,
    )
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="llm_usage", lazy="raise")


# Internal compatibility aliases for the pre-0004 application vocabulary.
# New persistence code uses the workout names exclusively.
Activity = Workout
WorkoutSourceLink = ActivitySourceLink


__all__ = [
    "Activity",
    "ActivitySourceLink",
    "AppleHealthImportJob",
    "AthleteCapability",
    "AthleteProfile",
    "AthleteSelfReportedBaseline",
    "Capability",
    "ContextExecutionOption",
    "CyclingWorkoutDetails",
    "ExecutionOptionCapability",
    "GoalTemplate",
    "GoalTemplateContext",
    "HikingWorkoutDetails",
    "LLMProviderMode",
    "LLMUsage",
    "OnboardingSession",
    "OtherWorkoutDetails",
    "PoolSwimmingDetails",
    "RunningWorkoutDetails",
    "StrengthWorkoutDetails",
    "SwimmingWorkoutDetails",
    "TrainingContext",
    "TrainingGoal",
    "User",
    "WeeklyTrainingPlan",
    "Workout",
    "WorkoutHeartRateObservation",
    "WorkoutSourceLink",
]
