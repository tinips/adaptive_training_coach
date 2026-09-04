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
