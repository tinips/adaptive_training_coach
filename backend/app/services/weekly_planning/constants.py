"""Named planning decisions from the first-week planner specification."""

# Section 2.3: the fixed introductory swimming prescription.
UNTRAINED_SWIM_SESSION_MAX_MINUTES = 30
UNTRAINED_SWIM_MAX_SESSIONS = 2

# Section 2.2: deterministic validation thresholds.
MAX_CONSECUTIVE_LOAD_DAYS = 6
MAX_IDENTICAL_SESSIONS = 2
MONOTONY_DURATION_TOLERANCE = 0.10
SESSION_COUNT_TOLERANCE = 1

# Section 2.4: RPE supplied when repairing unsupported targets.
RPE_BY_INTENSITY = {"EASY": 3, "MODERATE": 5, "HARD": 8}

# docs/decisions/locked.md, "First-week evaluator": every planned session now
# gets a code-generated UUID. v4 already meant FirstWeekPlan; v5 is the same
# shape with session ids, so a plan_schema_version < 5 FirstWeekPlan row has
# no durable session identity and must not be treated as linkable.
FIRST_WEEK_PLAN_SCHEMA_VERSION = 5
LEGACY_WEEKLY_PLAN_SCHEMA_VERSION = 3
