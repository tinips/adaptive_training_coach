"""SQLAlchemy models for the onboarding and Strava vertical slice.

The models intentionally contain no business behavior.  PostgreSQL receives
JSONB for document-shaped fields while SQLite uses SQLAlchemy's portable JSON
type so focused repository tests can run without a database service.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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
    AthleteEquipmentStatus,
    AthleteGender,
    BaselinePreferenceStatus,
    BaselineSource,
    BaselineStatus,
    CoachTone,
    ConnectionStatus,
    CyclingType,
    DayOfWeek,
    DetailLevel,
    Discipline,
    DiscomfortSeverity,
    EquipmentPriority,
    EquipmentResourceCategory,
    EquipmentSubstitutionQuality,
    EquipmentTrainingStage,
    HikingType,
    LevelLabel,
    LLMUsageStatus,
    OAuthProvider,
    OnboardingStatus,
    OnboardingStep,
    PrimarySport,
    ProfileSettingsStep,
    RunningType,
    StrengthType,
    SwimmingEnvironment,
    SwimmingStroke,
    SyncStatus,
    SyncType,
    TrainingFileFormat,
    TrainingGoalStatus,
    UserStatus,
    WebhookAspectType,
    WebhookObjectType,
    WebhookProcessingStatus,
    WorkoutFlowStep,
)


class EquipmentType(StrEnum):
    """Normalized equipment selected during onboarding."""

    RUNNING_SHOES = "RUNNING_SHOES"
    ROAD_BIKE = "ROAD_BIKE"
    MOUNTAIN_BIKE = "MOUNTAIN_BIKE"
    INDOOR_BIKE_TRAINER = "INDOOR_BIKE_TRAINER"
    SWIMMING_POOL = "SWIMMING_POOL"
    GYM = "GYM"
    RESISTANCE_BANDS = "RESISTANCE_BANDS"
    SPORTS_WATCH = "SPORTS_WATCH"
    HEART_RATE_CHEST_STRAP = "HEART_RATE_CHEST_STRAP"
    OTHER = "OTHER"


class EquipmentAccessType(StrEnum):
    """How reliably an athlete can access a selected equipment item."""

    REGULAR = "REGULAR"
    IRREGULAR = "IRREGULAR"
    NO_REGULAR_ACCESS = "NO_REGULAR_ACCESS"


class BodyArea(StrEnum):
    """Non-diagnostic body-area normalization for user-reported limitations."""

    SHOULDER = "SHOULDER"
    BACK = "BACK"
    HIP = "HIP"
    KNEE = "KNEE"
    ANKLE_FOOT = "ANKLE_FOOT"
    OTHER = "OTHER"


class HealthConstraintType(StrEnum):
    """Safe categories that do not assert a medical diagnosis."""

    CURRENT = "CURRENT"
    HISTORICAL = "HISTORICAL"
    BOTH = "BOTH"


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
    availability_rules: Mapped[list[AvailabilityRule]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
    equipment_access: Mapped[list[EquipmentAccess]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
    health_constraints: Mapped[list[HealthConstraint]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
    coach_preference: Mapped[CoachPreference | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        uselist=False,
    )
    baseline_preference: Mapped[BaselinePreference | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        uselist=False,
    )
    oauth_states: Mapped[list[OAuthState]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
    strava_connection: Mapped[StravaConnection | None] = relationship(
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
    activity_feedback: Mapped[list[ActivityFeedback]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
    workout_flow_session: Mapped[WorkoutFlowSession | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        uselist=False,
    )
    strava_sync_jobs: Mapped[list[StravaSyncJob]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
    strava_webhook_events: Mapped[list[StravaWebhookEvent]] = relationship(
        back_populates="user",
        passive_deletes=True,
        lazy="raise",
    )
    athlete_baselines: Mapped[list[AthleteBaseline]] = relationship(
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
        CheckConstraint("age >= 16 AND age <= 100", name="age_range"),
        CheckConstraint(
            "birth_year IS NULL OR (birth_year >= 1940 AND birth_year <= 2008)",
            name="birth_year_range",
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
    age: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    birth_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    gender: Mapped[AthleteGender | None] = mapped_column(
        persisted_enum(AthleteGender, name="athlete_gender", length=24),
        nullable=True,
    )
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    primary_sport: Mapped[PrimarySport] = mapped_column(
        persisted_enum(PrimarySport, name="primary_sport", length=24),
        nullable=False,
    )
    availability_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    equipment_recommendation_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    equipment_text: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    target_outcome: Mapped[str] = mapped_column(String(500), nullable=False)
    secondary_priority: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    original_description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TrainingGoalStatus] = mapped_column(
        persisted_enum(
            TrainingGoalStatus,
            name="training_goal_status",
            length=16,
        ),
        default=TrainingGoalStatus.CONFIRMED,
        server_default=TrainingGoalStatus.CONFIRMED.value,
        nullable=False,
    )
    equipment_context_revision: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )

    user: Mapped[User] = relationship(
        back_populates="training_goal",
        lazy="raise",
    )


class EquipmentResource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Stable reference catalog for equipment and access resources."""

    __tablename__ = "equipment_resources"
    __table_args__ = (UniqueConstraint("code", name="uq_equipment_resources_code"),)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[EquipmentResourceCategory] = mapped_column(
        persisted_enum(
            EquipmentResourceCategory, name="equipment_resource_category", length=24
        ),
        nullable=False,
    )


class EquipmentGoalType(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Database-held deterministic matcher for a supported goal/event."""

    __tablename__ = "equipment_goal_types"
    __table_args__ = (UniqueConstraint("code", name="uq_equipment_goal_types_code"),)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    match_terms: Mapped[list[str]] = mapped_column(json_document(), nullable=False)
    match_priority: Mapped[int] = mapped_column(
        SmallInteger, default=100, nullable=False
    )


class EquipmentStageWindow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "equipment_stage_windows"
    __table_args__ = (
        UniqueConstraint(
            "goal_type_id", "stage", name="uq_equipment_stage_windows_goal_stage"
        ),
    )
    goal_type_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("equipment_goal_types.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage: Mapped[EquipmentTrainingStage] = mapped_column(
        persisted_enum(
            EquipmentTrainingStage, name="equipment_training_stage", length=24
        ),
        nullable=False,
    )
    minimum_days_until_event: Mapped[int] = mapped_column(Integer, nullable=False)
    maximum_days_until_event: Mapped[int | None] = mapped_column(Integer, nullable=True)


class EquipmentResourceRequirement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "equipment_resource_requirements"
    __table_args__ = (
        UniqueConstraint(
            "goal_type_id", "resource_id", name="uq_equipment_requirement_goal_resource"
        ),
    )
    goal_type_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("equipment_goal_types.id", ondelete="CASCADE"),
        nullable=False,
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("equipment_resources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    priority: Mapped[EquipmentPriority] = mapped_column(
        persisted_enum(EquipmentPriority, name="equipment_priority", length=16),
        nullable=False,
    )
    required_stage: Mapped[EquipmentTrainingStage] = mapped_column(
        persisted_enum(
            EquipmentTrainingStage, name="equipment_requirement_stage", length=24
        ),
        nullable=False,
    )
    condition_text: Mapped[str | None] = mapped_column(String(300), nullable=True)
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class EquipmentResourceSubstitution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "equipment_resource_substitutions"
    __table_args__ = (
        UniqueConstraint(
            "required_resource_id",
            "substitute_resource_id",
            name="uq_equipment_substitution_pair",
        ),
    )
    required_resource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("equipment_resources.id", ondelete="CASCADE"),
        nullable=False,
    )
    substitute_resource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("equipment_resources.id", ondelete="CASCADE"),
        nullable=False,
    )
    quality: Mapped[EquipmentSubstitutionQuality] = mapped_column(
        persisted_enum(
            EquipmentSubstitutionQuality,
            name="equipment_substitution_quality",
            length=24,
        ),
        nullable=False,
    )


class AthleteGoalEquipmentStatus(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "athlete_goal_equipment_statuses"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "training_goal_id",
            "goal_revision",
            "resource_id",
            name="uq_athlete_goal_equipment_status",
        ),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    training_goal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("training_goals.id", ondelete="CASCADE"),
        nullable=False,
    )
    goal_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("equipment_resources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[AthleteEquipmentStatus] = mapped_column(
        persisted_enum(
            AthleteEquipmentStatus, name="athlete_equipment_status", length=16
        ),
        nullable=False,
    )


class AthleteGoalEquipmentInterpretation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "athlete_goal_equipment_interpretations"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "training_goal_id",
            "goal_revision",
            name="uq_athlete_goal_equipment_interpretation",
        ),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    training_goal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("training_goals.id", ondelete="CASCADE"),
        nullable=False,
    )
    goal_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    interpretation: Mapped[dict[str, object]] = mapped_column(
        json_document(), nullable=False
    )


class AvailabilityRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-day training availability confirmed during onboarding."""

    __tablename__ = "availability_rules"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "day_of_week",
            name="uq_availability_rules_user_day",
        ),
        CheckConstraint(
            "available_minutes IS NULL OR available_minutes > 0",
            name="positive_available_minutes",
        ),
        CheckConstraint(
            "is_variable OR available_minutes IS NOT NULL",
            name="variable_or_minutes",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    day_of_week: Mapped[DayOfWeek] = mapped_column(
        persisted_enum(DayOfWeek, name="day_of_week", length=16),
        nullable=False,
    )
    available_minutes: Mapped[int | None] = mapped_column(
        SmallInteger,
        nullable=True,
    )
    is_variable: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )

    user: Mapped[User] = relationship(
        back_populates="availability_rules",
        lazy="raise",
    )


class EquipmentAccess(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Equipment availability, including optional recurring access days."""

    __tablename__ = "equipment_access"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "equipment_type",
            name="uq_equipment_access_user_equipment",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    equipment_type: Mapped[EquipmentType] = mapped_column(
        persisted_enum(EquipmentType, name="equipment_type", length=32),
        nullable=False,
    )
    access_type: Mapped[EquipmentAccessType] = mapped_column(
        persisted_enum(
            EquipmentAccessType,
            name="equipment_access_type",
            length=24,
        ),
        default=EquipmentAccessType.REGULAR,
        server_default=EquipmentAccessType.REGULAR.value,
        nullable=False,
    )
    access_days: Mapped[list[str] | None] = mapped_column(
        json_document(),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(
        back_populates="equipment_access",
        lazy="raise",
    )


class HealthConstraint(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A non-diagnostic, user-reported current or historical limitation."""

    __tablename__ = "health_constraints"
    __table_args__ = (
        Index("ix_health_constraints_user_id", "user_id"),
        CheckConstraint(
            "is_current OR is_historical",
            name="current_or_historical",
        ),
        CheckConstraint(
            "(constraint_type = 'CURRENT' AND is_current "
            "AND NOT is_historical) OR "
            "(constraint_type = 'HISTORICAL' AND is_historical "
            "AND NOT is_current) OR "
            "(constraint_type = 'BOTH' AND is_current AND is_historical)",
            name="timing_matches_flags",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    body_area: Mapped[BodyArea | None] = mapped_column(
        persisted_enum(BodyArea, name="body_area", length=24),
        nullable=True,
    )
    constraint_type: Mapped[HealthConstraintType] = mapped_column(
        persisted_enum(
            HealthConstraintType,
            name="health_constraint_type",
            length=32,
        ),
        nullable=False,
    )
    normalized_description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    is_historical: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )

    user: Mapped[User] = relationship(
        back_populates="health_constraints",
        lazy="raise",
    )


class CoachPreference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Confirmed response-style preferences."""

    __tablename__ = "coach_preferences"
    __table_args__ = (UniqueConstraint("user_id", name="uq_coach_preferences_user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    tone: Mapped[CoachTone] = mapped_column(
        persisted_enum(CoachTone, name="coach_tone", length=32),
        nullable=False,
    )
    detail_level: Mapped[DetailLevel] = mapped_column(
        persisted_enum(DetailLevel, name="detail_level", length=16),
        nullable=False,
    )

    user: Mapped[User] = relationship(
        back_populates="coach_preference",
        lazy="raise",
    )


class BaselinePreference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Selected way to establish the athlete baseline."""

    __tablename__ = "baseline_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_baseline_preferences_user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    selected_source: Mapped[BaselineSource] = mapped_column(
        persisted_enum(BaselineSource, name="baseline_source", length=32),
        nullable=False,
    )
    status: Mapped[BaselinePreferenceStatus] = mapped_column(
        persisted_enum(
            BaselinePreferenceStatus,
            name="baseline_preference_status",
            length=24,
        ),
        default=BaselinePreferenceStatus.SELECTED,
        server_default=BaselinePreferenceStatus.SELECTED.value,
        nullable=False,
    )

    user: Mapped[User] = relationship(
        back_populates="baseline_preference",
        lazy="raise",
    )


class OAuthState(UUIDPrimaryKeyMixin, Base):
    """Hashed, expiring, single-use OAuth callback state."""

    __tablename__ = "oauth_states"
    __table_args__ = (
        UniqueConstraint("state_hash", name="uq_oauth_states_state_hash"),
        Index("ix_oauth_states_user_expires", "user_id", "expires_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[OAuthProvider] = mapped_column(
        persisted_enum(OAuthProvider, name="oauth_provider", length=24),
        nullable=False,
    )
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="oauth_states", lazy="raise")


class StravaConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Encrypted Strava credentials and connection lifecycle."""

    __tablename__ = "strava_connections"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_strava_connections_user_id"),
        UniqueConstraint(
            "strava_athlete_id",
            name="uq_strava_connections_athlete_id",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    strava_athlete_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    accepted_scopes: Mapped[list[str]] = mapped_column(
        json_document(),
        default=list,
        nullable=False,
    )
    encrypted_access_token: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    encrypted_refresh_token: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    access_token_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    connection_status: Mapped[ConnectionStatus] = mapped_column(
        persisted_enum(
            ConnectionStatus,
            name="connection_status",
            length=24,
        ),
        default=ConnectionStatus.CONNECTED,
        server_default=ConnectionStatus.CONNECTED.value,
        nullable=False,
    )
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user: Mapped[User] = relationship(
        back_populates="strava_connection",
        lazy="raise",
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
    feedback: Mapped[ActivityFeedback | None] = relationship(
        back_populates="workout",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        uselist=False,
    )
    workout_flow_sessions: Mapped[list[WorkoutFlowSession]] = relationship(
        back_populates="workout",
        passive_deletes=True,
        lazy="raise",
    )
    import_jobs: Mapped[list[AppleHealthImportJob]] = relationship(
        back_populates="workout",
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
            "pack_weight_kg IS NULL OR pack_weight_kg >= 0",
            name="pack_weight_nonnegative",
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
    pack_weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)

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
            "average_swolf IS NULL OR average_swolf >= 0",
            name="average_swolf_nonnegative",
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
    average_swolf: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_strokes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    swimming_details: Mapped[SwimmingWorkoutDetails] = relationship(
        back_populates="pool_details",
        lazy="raise",
    )


class StrengthWorkoutDetails(Base):
    """Validated JSON exercise structure for one strength workout."""

    __tablename__ = "strength_workout_details"

    workout_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workouts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    strength_type: Mapped[StrengthType] = mapped_column(
        persisted_enum(StrengthType, name="strength_type", length=16),
        nullable=False,
    )
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
    workout: Mapped[Workout | None] = relationship(
        back_populates="import_jobs",
        lazy="raise",
    )
    source_links: Mapped[list[ActivitySourceLink]] = relationship(
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
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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


class ActivityFeedback(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Optional, non-diagnostic user feedback for one owned workout."""

    __tablename__ = "activity_feedback"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "workout_id",
            name="uq_activity_feedback_user_workout",
        ),
        CheckConstraint(
            "manual_average_heart_rate IS NULL "
            "OR (manual_average_heart_rate >= 30 "
            "AND manual_average_heart_rate <= 250)",
            name="manual_average_heart_rate_range",
        ),
        CheckConstraint(
            "reported_rpe IS NULL OR (reported_rpe >= 1 AND reported_rpe <= 10)",
            name="reported_rpe_range",
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
    manual_average_heart_rate: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    reported_rpe: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reported_rpe_label: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    reported_discomfort: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )
    mobility_done: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    discomfort_body_area: Mapped[BodyArea | None] = mapped_column(
        persisted_enum(
            BodyArea,
            name="activity_feedback_body_area",
            length=16,
        ),
        nullable=True,
    )
    discomfort_severity: Mapped[DiscomfortSeverity | None] = mapped_column(
        persisted_enum(
            DiscomfortSeverity,
            name="discomfort_severity",
            length=16,
        ),
        nullable=True,
    )
    discomfort_description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    feedback_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user: Mapped[User] = relationship(
        back_populates="activity_feedback",
        lazy="raise",
    )
    workout: Mapped[Workout] = relationship(
        back_populates="feedback",
        lazy="raise",
    )

    @property
    def activity_id(self) -> uuid.UUID:
        return self.workout_id

    @activity_id.setter
    def activity_id(self, value: uuid.UUID) -> None:
        self.workout_id = value


class WorkoutFlowSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable state for one user's resumable daily workout feedback flow."""

    __tablename__ = "workout_flow_sessions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            name="uq_workout_flow_sessions_user_id",
        ),
        CheckConstraint(
            "pending_manual_average_heart_rate IS NULL "
            "OR (pending_manual_average_heart_rate >= 30 "
            "AND pending_manual_average_heart_rate <= 250)",
            name="pending_manual_average_heart_rate_range",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    workout_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workouts.id", ondelete="SET NULL"),
        nullable=True,
    )
    state: Mapped[WorkoutFlowStep] = mapped_column(
        persisted_enum(
            WorkoutFlowStep,
            name="workout_flow_step",
            length=32,
        ),
        default=WorkoutFlowStep.WAITING_FOR_FILE,
        server_default=WorkoutFlowStep.WAITING_FOR_FILE.value,
        nullable=False,
    )
    pending_manual_average_heart_rate: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    pending_discomfort_description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user: Mapped[User] = relationship(
        back_populates="workout_flow_session",
        lazy="raise",
    )
    workout: Mapped[Workout | None] = relationship(
        back_populates="workout_flow_sessions",
        lazy="raise",
    )

    @property
    def activity_id(self) -> uuid.UUID | None:
        return self.workout_id

    @activity_id.setter
    def activity_id(self, value: uuid.UUID | None) -> None:
        self.workout_id = value


class StravaSyncJob(UUIDPrimaryKeyMixin, Base):
    """One serialized import attempt for a user."""

    __tablename__ = "strava_sync_jobs"
    __table_args__ = (
        Index("ix_strava_sync_jobs_user_requested", "user_id", "requested_at"),
        Index(
            "uq_strava_sync_jobs_active_user",
            "user_id",
            unique=True,
            postgresql_where=text(
                "status IN ('REQUESTED', 'RUNNING')",
            ),
            sqlite_where=text("status IN ('REQUESTED', 'RUNNING')"),
        ),
        CheckConstraint("imported_count >= 0", name="imported_nonnegative"),
        CheckConstraint("updated_count >= 0", name="updated_nonnegative"),
        CheckConstraint("skipped_count >= 0", name="skipped_nonnegative"),
        CheckConstraint("failed_count >= 0", name="failed_nonnegative"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[SyncStatus] = mapped_column(
        persisted_enum(SyncStatus, name="sync_status", length=16),
        default=SyncStatus.REQUESTED,
        server_default=SyncStatus.REQUESTED.value,
        nullable=False,
    )
    sync_type: Mapped[SyncType] = mapped_column(
        persisted_enum(SyncType, name="sync_type", length=16),
        nullable=False,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
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
    imported_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    updated_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    skipped_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    failed_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message_safe: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    user: Mapped[User] = relationship(
        back_populates="strava_sync_jobs",
        lazy="raise",
    )


class StravaWebhookEvent(UUIDPrimaryKeyMixin, Base):
    """Durable webhook inbox record with provider-key idempotency."""

    __tablename__ = "strava_webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "external_event_key",
            name="uq_strava_webhook_events_external_key",
        ),
        Index(
            "ix_strava_webhook_events_owner_created",
            "owner_id",
            "created_at",
        ),
        Index(
            "ix_strava_webhook_events_user_created",
            "user_id",
            "created_at",
        ),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    external_event_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_type: Mapped[WebhookObjectType] = mapped_column(
        persisted_enum(
            WebhookObjectType,
            name="webhook_object_type",
            length=16,
        ),
        nullable=False,
    )
    object_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    aspect_type: Mapped[WebhookAspectType] = mapped_column(
        persisted_enum(
            WebhookAspectType,
            name="webhook_aspect_type",
            length=16,
        ),
        nullable=False,
    )
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    payload: Mapped[dict[str, object]] = mapped_column(
        json_document(),
        nullable=False,
    )
    processing_status: Mapped[WebhookProcessingStatus] = mapped_column(
        persisted_enum(
            WebhookProcessingStatus,
            name="webhook_processing_status",
            length=16,
        ),
        default=WebhookProcessingStatus.PENDING,
        server_default=WebhookProcessingStatus.PENDING.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user: Mapped[User | None] = relationship(
        back_populates="strava_webhook_events",
        lazy="raise",
    )


class AthleteBaseline(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One immutable-in-history baseline calculation version."""

    __tablename__ = "athlete_baselines"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "version",
            name="uq_athlete_baselines_user_version",
        ),
        Index("ix_athlete_baselines_user_generated", "user_id", "generated_at"),
        CheckConstraint("version > 0", name="positive_version"),
        CheckConstraint(
            "overall_confidence >= 0 AND overall_confidence <= 1",
            name="overall_confidence_range",
        ),
        CheckConstraint(
            "analysis_end >= analysis_start",
            name="analysis_period_order",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    analysis_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    analysis_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    source: Mapped[BaselineSource] = mapped_column(
        persisted_enum(
            BaselineSource,
            name="athlete_baseline_source",
            length=32,
        ),
        nullable=False,
    )
    status: Mapped[BaselineStatus] = mapped_column(
        persisted_enum(BaselineStatus, name="baseline_status", length=24),
        nullable=False,
    )
    overall_confidence: Mapped[float] = mapped_column(Float, nullable=False)

    user: Mapped[User] = relationship(
        back_populates="athlete_baselines",
        lazy="raise",
    )
    discipline_baselines: Mapped[list[DisciplineBaseline]] = relationship(
        back_populates="athlete_baseline",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class DisciplineBaseline(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Deterministic metrics for one discipline in one baseline version."""

    __tablename__ = "discipline_baselines"
    __table_args__ = (
        UniqueConstraint(
            "athlete_baseline_id",
            "discipline",
            name="uq_discipline_baselines_baseline_discipline",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="confidence_range",
        ),
        CheckConstraint("sessions_count >= 0", name="sessions_nonnegative"),
        CheckConstraint("active_weeks >= 0", name="active_weeks_nonnegative"),
        CheckConstraint(
            "total_duration_seconds >= 0",
            name="total_duration_nonnegative",
        ),
        CheckConstraint(
            "average_weekly_duration_seconds >= 0",
            name="weekly_duration_nonnegative",
        ),
        CheckConstraint(
            "recent_session_count >= 0",
            name="recent_sessions_nonnegative",
        ),
    )

    athlete_baseline_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("athlete_baselines.id", ondelete="CASCADE"),
        nullable=False,
    )
    discipline: Mapped[Discipline] = mapped_column(
        persisted_enum(
            Discipline,
            name="baseline_discipline",
            length=16,
        ),
        nullable=False,
    )
    level_label: Mapped[LevelLabel] = mapped_column(
        persisted_enum(LevelLabel, name="level_label", length=16),
        nullable=False,
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    sessions_count: Mapped[int] = mapped_column(Integer, nullable=False)
    active_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    total_duration_seconds: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    average_weekly_duration_seconds: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )
    total_distance_meters: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    average_weekly_distance_meters: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    longest_session_seconds: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    longest_distance_meters: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    recent_session_count: Mapped[int] = mapped_column(Integer, nullable=False)
    metrics: Mapped[dict[str, object]] = mapped_column(
        json_document(),
        default=dict,
        nullable=False,
    )

    athlete_baseline: Mapped[AthleteBaseline] = relationship(
        back_populates="discipline_baselines",
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
WorkoutFeedback = ActivityFeedback


__all__ = [
    "Activity",
    "ActivityFeedback",
    "ActivitySourceLink",
    "AppleHealthImportJob",
    "AthleteBaseline",
    "AthleteProfile",
    "AvailabilityRule",
    "BaselinePreference",
    "BodyArea",
    "CoachPreference",
    "CyclingWorkoutDetails",
    "DisciplineBaseline",
    "EquipmentAccess",
    "EquipmentAccessType",
    "EquipmentType",
    "HealthConstraint",
    "HealthConstraintType",
    "HikingWorkoutDetails",
    "LLMProviderMode",
    "LLMUsage",
    "OAuthState",
    "OnboardingSession",
    "OtherWorkoutDetails",
    "PoolSwimmingDetails",
    "RunningWorkoutDetails",
    "StravaConnection",
    "StravaSyncJob",
    "StravaWebhookEvent",
    "StrengthWorkoutDetails",
    "SwimmingWorkoutDetails",
    "TrainingGoal",
    "User",
    "Workout",
    "WorkoutFeedback",
    "WorkoutFlowSession",
    "WorkoutSourceLink",
]
