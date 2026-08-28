"""Explicit persisted and application enums."""

from __future__ import annotations

from enum import StrEnum


class UserStatus(StrEnum):
    NEW = "NEW"
    ONBOARDING_IN_PROGRESS = "ONBOARDING_IN_PROGRESS"
    ONBOARDING_COMPLETED = "ONBOARDING_COMPLETED"
    PROFILE_COMPLETED = "PROFILE_COMPLETED"


class OnboardingStatus(StrEnum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class OnboardingStep(StrEnum):
    CONSENT = "CONSENT"
    SETUP_INTRODUCTION = "SETUP_INTRODUCTION"
    GOAL_INTAKE = "GOAL_INTAKE"
    GOAL_CONFIRMED = "GOAL_CONFIRMED"
    PROFILE_BIRTH_YEAR_INTAKE = "PROFILE_BIRTH_YEAR_INTAKE"
    PROFILE_GENDER_INTAKE = "PROFILE_GENDER_INTAKE"
    PROFILE_WEIGHT_INTAKE = "PROFILE_WEIGHT_INTAKE"
    PROFILE_HEIGHT_INTAKE = "PROFILE_HEIGHT_INTAKE"
    AVAILABILITY_INTAKE = "AVAILABILITY_INTAKE"
    EQUIPMENT_RECOMMENDATION = "EQUIPMENT_RECOMMENDATION"
    EQUIPMENT_INTAKE = "EQUIPMENT_INTAKE"
    HEALTH_LIMITATIONS_INTAKE = "HEALTH_LIMITATIONS_INTAKE"
    TRAINING_HISTORY_IMPORT = "TRAINING_HISTORY_IMPORT"


class AthleteGender(StrEnum):
    """Biological-sex value supplied by the athlete."""

    MALE = "MALE"
    FEMALE = "FEMALE"


class ActivitySource(StrEnum):
    MANUAL = "MANUAL"
    APPLE_HEALTH = "APPLE_HEALTH"
    TCX = "TCX"
    FIT = "FIT"
    OTHER_IMPORT = "OTHER_IMPORT"


class HeartRateTemporalQuality(StrEnum):
    EXACT_SAMPLE = "EXACT_SAMPLE"
    SHORT_INTERVAL = "SHORT_INTERVAL"
    COARSE_INTERVAL = "COARSE_INTERVAL"
    UNKNOWN = "UNKNOWN"


class DisciplineEvidenceState(StrEnum):
    """How much recent evidence the planner holds for one target discipline."""

    WELL_EVIDENCED = "WELL_EVIDENCED"
    THIN = "THIN"
    NONE = "NONE"


class TrainingFileFormat(StrEnum):
    APPLE_HEALTH_ZIP = "APPLE_HEALTH_ZIP"
    TCX = "TCX"
    UNKNOWN = "UNKNOWN"


class TrainingImportContext(StrEnum):
    ONBOARDING_HISTORY = "ONBOARDING_HISTORY"
    POST_ONBOARDING = "POST_ONBOARDING"


class WorkoutFlowStep(StrEnum):
    WAITING_FOR_FILE = "WAITING_FOR_FILE"
    HR_OFFER = "HR_OFFER"
    HR_ENTRY = "HR_ENTRY"
    HR_CONFIRM = "HR_CONFIRM"
    RPE = "RPE"
    MOBILITY = "MOBILITY"
    DISCOMFORT = "DISCOMFORT"
    BODY_AREA = "BODY_AREA"
    DESCRIPTION_ENTRY = "DESCRIPTION_ENTRY"
    DESCRIPTION_CONFIRM = "DESCRIPTION_CONFIRM"
    SEVERITY = "SEVERITY"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"


class DiscomfortSeverity(StrEnum):
    MILD = "MILD"
    MODERATE = "MODERATE"
    SEVERE = "SEVERE"


class AppleHealthImportStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Discipline(StrEnum):
    RUNNING = "RUNNING"
    CYCLING = "CYCLING"
    HIKING = "HIKING"
    SWIMMING = "SWIMMING"
    STRENGTH = "STRENGTH"
    OTHER = "OTHER"

    # Source-compatible aliases. Only the canonical values above are persisted
    # or yielded when iterating over the enum.
    RUN = "RUNNING"
    RIDE = "CYCLING"
    WALK_HIKE = "HIKING"
    SWIM = "SWIMMING"


class FitnessBaselineSource(StrEnum):
    """Provenance for an immutable athlete fitness baseline."""

    IMPORTED_WORKOUT_WINDOW = "IMPORTED_WORKOUT_WINDOW"


class RunningType(StrEnum):
    OUTDOOR = "OUTDOOR"
    TRAIL = "TRAIL"
    TRACK = "TRACK"
    TREADMILL = "TREADMILL"


class CyclingType(StrEnum):
    ROAD = "ROAD"
    MTB = "MTB"
    GRAVEL = "GRAVEL"
    STATIONARY = "STATIONARY"
    OTHER = "OTHER"


class HikingType(StrEnum):
    HIKING = "HIKING"
    TREKKING = "TREKKING"
    MOUNTAINEERING = "MOUNTAINEERING"
    SNOWSHOEING = "SNOWSHOEING"
    OTHER = "OTHER"


class SwimmingEnvironment(StrEnum):
    POOL = "POOL"
    OPEN_WATER = "OPEN_WATER"
    UNKNOWN = "UNKNOWN"


class SwimmingStroke(StrEnum):
    FREESTYLE = "FREESTYLE"
    BREASTSTROKE = "BREASTSTROKE"
    BACKSTROKE = "BACKSTROKE"
    BUTTERFLY = "BUTTERFLY"
    MIXED = "MIXED"
    OTHER = "OTHER"


class StrengthType(StrEnum):
    GYM = "GYM"
    CALISTHENICS = "CALISTHENICS"
    OTHER = "OTHER"


class LLMUsageStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    CLARIFICATION = "CLARIFICATION"
    FALLBACK = "FALLBACK"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    RATE_LIMITED = "RATE_LIMITED"


class TrainingGoalStatus(StrEnum):
    """Lifecycle state for an explicitly confirmed canonical goal."""

    CONFIRMED = "CONFIRMED"


class EquipmentImportance(StrEnum):
    """Static catalog importance, independent of athlete or training state."""

    ESSENTIAL = "essential"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"


class CatalogItemSource(StrEnum):
    """Origin of reusable training-catalog knowledge."""

    SEEDED = "SEEDED"
    LLM_GENERATED = "LLM_GENERATED"


class CatalogItemStatus(StrEnum):
    """Whether one catalog definition can drive new planning decisions."""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class GoalTemplateKind(StrEnum):
    PRIMARY = "PRIMARY"
    SUPPORTING = "SUPPORTING"


class GoalContextRole(StrEnum):
    TARGET = "TARGET"
    SUPPORTING = "SUPPORTING"


class CapabilityKind(StrEnum):
    EQUIPMENT = "EQUIPMENT"
    ACCESS = "ACCESS"
    FACILITY = "FACILITY"


class ExecutionOptionRole(StrEnum):
    PREFERRED = "PREFERRED"
    SUBSTITUTE = "SUBSTITUTE"


class CapabilityImportance(StrEnum):
    REQUIRED = "REQUIRED"
    RECOMMENDED = "RECOMMENDED"
    OPTIONAL = "OPTIONAL"


class AthleteCapabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class ContextAssessmentStatus(StrEnum):
    FEASIBLE = "FEASIBLE"
    FEASIBLE_WITH_SUBSTITUTION = "FEASIBLE_WITH_SUBSTITUTION"
    UNKNOWN = "UNKNOWN"
    LIMITED = "LIMITED"


class ProfileSettingsStep(StrEnum):
    """Persisted, deterministic post-onboarding profile edit states."""

    MENU = "MENU"
    GOAL_MENU = "GOAL_MENU"
    GOAL_MAIN = "GOAL_MAIN"
    GOAL_OUTCOME = "GOAL_OUTCOME"
    GOAL_DATE = "GOAL_DATE"
    GOAL_SECONDARY = "GOAL_SECONDARY"
    GOAL_CLASSIFICATION_CONFIRM = "GOAL_CLASSIFICATION_CONFIRM"
    AVAILABILITY = "AVAILABILITY"
    EQUIPMENT = "EQUIPMENT"
    HEALTH = "HEALTH"
    PERSONAL_MENU = "PERSONAL_MENU"
    PERSONAL_BIRTH_YEAR = "PERSONAL_BIRTH_YEAR"
    PERSONAL_GENDER = "PERSONAL_GENDER"
    PERSONAL_WEIGHT = "PERSONAL_WEIGHT"
    PERSONAL_HEIGHT = "PERSONAL_HEIGHT"
