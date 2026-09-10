"""Named, isolated calibration constants for the first-week evaluator.

Every number here is sourced from docs/decisions/locked.md. Numbers marked
PROVISIONAL there (density cut lines, the variance-flag trigger count, the
THRIVING minimum positive-efficiency-session count, and the minimum-evidence
bar below) are illustrative and pending calibration against real athlete
data; they must stay isolated constants, never inline literals, so they can
be recalibrated without touching the surrounding logic.
"""

from __future__ import annotations

from app.domain.enums import SessionIntentVerdict, SessionOutputVerdict

# --- Per-session score. locked.md, "Per-session score". ---------------------
# session point value = intent value + output value, range -2 (best) to +2
# (worst).
INTENT_POINT_VALUE: dict[SessionIntentVerdict, int] = {
    SessionIntentVerdict.EASIER_THAN_EXPECTED: -1,
    SessionIntentVerdict.AS_PRESCRIBED: 0,
    SessionIntentVerdict.OVERCOOKED: 1,
}
OUTPUT_POINT_VALUE: dict[SessionOutputVerdict, int] = {
    SessionOutputVerdict.BELOW_EXPECTED_OUTPUT: 1,
    SessionOutputVerdict.WITHIN_EXPECTED_OUTPUT: 0,
    SessionOutputVerdict.ABOVE_EXPECTED_OUTPUT: -1,
}

# --- Weekly bands. locked.md, "Weekly bands". PROVISIONAL cut lines. --------
DENSITY_WATCH_EFFORT_MIN = 0.3
DENSITY_BACK_OFF_MIN = 0.6

# --- Variance flag. locked.md, "Variance flag". PROVISIONAL trigger count. --
VARIANCE_FLAG_NOTE_MIN_COUNT = 2

# --- THRIVING gate. locked.md, "THRIVING gate". PROVISIONAL minimum count. --
THRIVING_MIN_POSITIVE_EFFICIENCY_SESSIONS = 1

# --- Minimum evidence bar for INSUFFICIENT_EVIDENCE. ------------------------
# docs/decisions/open.md references "the 75%-matched minimum-evidence bar
# (see locked.md, 'Minimum evidence')", but locked.md carries no dedicated
# "Minimum evidence" section or number as of 2026-09-10; this constant is the
# PROVISIONAL number that reference implies, applied to completion percent
# (matched / (matched + missed)). At least one matched, comparable session is
# also required so density has a defined denominator.
MIN_EVIDENCE_COMPLETION_PERCENT = 75.0
MIN_EVIDENCE_COMPARABLE_SESSIONS = 1

# --- RPE-range-to-reference-HR-band mapping. locked.md, "Signal scoring...".-
# "an entire range in 1-4 selects easy, 5-6 selects moderate, and 7-10
# selects hard. A range crossing those boundaries is NOT_COMPARABLE for HR."
RPE_BAND_EASY_RANGE = (1, 4)
RPE_BAND_MODERATE_RANGE = (5, 6)
RPE_BAND_HARD_RANGE = (7, 10)

# --- HR comparison tolerances. locked.md, "Signal scoring...". --------------
HR_REFERENCE_BAND_ALLOWANCE_BPM = 5
HR_SOURCE_CONFLICT_THRESHOLD_BPM = 10
SAMPLED_HR_MIN_DURATION_COVERAGE = 0.8

# --- E6 duration tolerance (strength and RPE-fallback sessions only; running/
# swimming/cycling duration is derived, never independently flagged, per
# locked.md "Duration derivation"). -----------------------------------------
DURATION_TOLERANCE_PERCENT = 0.10

# --- Minimum allowances. locked.md, "Prescribed-range tolerance model" (the
# range itself is the margin for these metrics; these floors only bound how
# narrow the LLM-authored range may be, reused here for symmetry/clarity). --
RUNNING_PACE_MIN_ALLOWANCE_SECONDS_PER_KM = 10.0
SWIM_PACE_MIN_ALLOWANCE_SECONDS_PER_100M = 3.0
CYCLING_POWER_MIN_ALLOWANCE_WATTS = 10.0
RUNNING_DISTANCE_MIN_ALLOWANCE_METERS = 250.0
SWIM_DISTANCE_MIN_ALLOWANCE_METERS = 50.0
CYCLING_DISTANCE_MIN_ALLOWANCE_METERS = 1000.0

EVALUATOR_RULE_VERSION = 1
