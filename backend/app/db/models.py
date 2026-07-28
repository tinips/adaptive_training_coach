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
    BaselinePreferenceStatus,
    BaselineSource,
    BaselineStatus,
    CoachTone,
    ConnectionStatus,
    DayOfWeek,
    DetailLevel,
    Discipline,
    GoalPriority,
    LevelLabel,
    LLMUsageStatus,
    OAuthProvider,
    OnboardingStatus,
    OnboardingStep,
    PrimarySport,
    SyncStatus,
    SyncType,
    UserStatus,
    WebhookAspectType,
    WebhookObjectType,
    WebhookProcessingStatus,
)


class GoalType(StrEnum):
    """Normalized goal categories supported by the onboarding milestone."""

    FIVE_K = "FIVE_K"
    TEN_K = "TEN_K"
    HALF_MARATHON = "HALF_MARATHON"
    MARATHON = "MARATHON"
    TRAIL = "TRAIL"
    CYCLING_EVENT = "CYCLING_EVENT"
    GRAN_FONDO = "GRAN_FONDO"
    SPRINT_TRIATHLON = "SPRINT_TRIATHLON"
    OLYMPIC_TRIATHLON = "OLYMPIC_TRIATHLON"
    HALF_IRONMAN_70_3 = "HALF_IRONMAN_70_3"
    IRONMAN = "IRONMAN"
    FIRST_TRIATHLON = "FIRST_TRIATHLON"
    IMPROVE_TECHNIQUE = "IMPROVE_TECHNIQUE"
    OPEN_WATER_SWIMMING = "OPEN_WATER_SWIMMING"
    SPECIFIC_EVENT = "SPECIFIC_EVENT"
    GENERAL_HEALTH = "GENERAL_HEALTH"
    IMPROVE_ENDURANCE = "IMPROVE_ENDURANCE"
    IMPROVE_PERFORMANCE = "IMPROVE_PERFORMANCE"
    LOSE_BODY_FAT = "LOSE_BODY_FAT"
    BUILD_STRENGTH = "BUILD_STRENGTH"
    OTHER = "OTHER"


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
    activities: Mapped[list[Activity]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
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
    pending_free_text_step: Mapped[OnboardingStep | None] = mapped_column(
        persisted_enum(
            OnboardingStep,
            name="pending_onboarding_step",
            length=32,
        ),
        nullable=True,
    )
    pending_parsed_value: Mapped[dict[str, object] | None] = mapped_column(
        json_document(),
        nullable=True,
    )
    return_to_summary: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user: Mapped[User] = relationship(
        back_populates="onboarding_session",
        lazy="raise",
    )


class AthleteProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Normalized athlete demographics and primary discipline."""

    __tablename__ = "athlete_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_athlete_profiles_user_id"),
        CheckConstraint("age >= 16 AND age <= 100", name="age_range"),
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
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    primary_sport: Mapped[PrimarySport] = mapped_column(
        persisted_enum(PrimarySport, name="primary_sport", length=24),
        nullable=False,
    )

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
    goal_type: Mapped[GoalType] = mapped_column(
        persisted_enum(GoalType, name="goal_type", length=32),
        nullable=False,
    )
    event_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    goal_priority: Mapped[GoalPriority] = mapped_column(
        persisted_enum(GoalPriority, name="goal_priority", length=24),
        nullable=False,
    )

    user: Mapped[User] = relationship(
        back_populates="training_goal",
        lazy="raise",
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
        persisted_enum(BaselineSource, name="baseline_source", length=16),
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


class Activity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Normalized provider activity summary used by the baseline engine."""

    __tablename__ = "activities"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "external_id",
            name="uq_activities_source_external_id",
        ),
        Index("ix_activities_user_started_at", "user_id", "started_at"),
        CheckConstraint("duration_seconds >= 0", name="duration_nonnegative"),
        CheckConstraint(
            "moving_time_seconds IS NULL OR moving_time_seconds >= 0",
            name="moving_time_nonnegative",
        ),
        CheckConstraint(
            "distance_meters IS NULL OR distance_meters >= 0",
            name="distance_nonnegative",
        ),
        CheckConstraint(
            "elevation_gain_meters IS NULL OR elevation_gain_meters >= 0",
            name="elevation_nonnegative",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[ActivitySource] = mapped_column(
        persisted_enum(ActivitySource, name="activity_source", length=16),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sport: Mapped[Discipline] = mapped_column(
        persisted_enum(Discipline, name="discipline", length=16),
        nullable=False,
    )
    source_sport_type: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    timezone: Mapped[str | None] = mapped_column(String(128), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    moving_time_seconds: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    distance_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    elevation_gain_meters: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    average_heart_rate: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    max_heart_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_speed: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_watts: Mapped[float | None] = mapped_column(Float, nullable=True)
    trainer: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    commute: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    manual: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    raw_summary: Mapped[dict[str, object] | None] = mapped_column(
        json_document(),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user: Mapped[User] = relationship(back_populates="activities", lazy="raise")


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
            length=16,
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


__all__ = [
    "Activity",
    "AthleteBaseline",
    "AthleteProfile",
    "AvailabilityRule",
    "BaselinePreference",
    "BodyArea",
    "CoachPreference",
    "DisciplineBaseline",
    "EquipmentAccess",
    "EquipmentAccessType",
    "EquipmentType",
    "GoalType",
    "HealthConstraint",
    "HealthConstraintType",
    "LLMProviderMode",
    "LLMUsage",
    "OAuthState",
    "OnboardingSession",
    "StravaConnection",
    "StravaSyncJob",
    "StravaWebhookEvent",
    "TrainingGoal",
    "User",
]
