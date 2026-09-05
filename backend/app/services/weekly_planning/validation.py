"""Pure first-week plan validation, repair, and deterministic fallback logic."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from pydantic import TypeAdapter

from app.domain.enums import Discipline
from app.schemas.availability import ConfirmedWeeklyAvailability
from app.schemas.baseline import AthleteBaselineData, TrainingPreferences
from app.schemas.weekly_plans import (
    FirstWeekPlan,
    FirstWeekPlanPrescription,
    FirstWeekSession,
    PlanReadiness,
    PlanSession,
    WeeklyPlan,
)
from app.services.weekly_planning.constants import (
    MAX_CONSECUTIVE_LOAD_DAYS,
    MAX_IDENTICAL_SESSIONS,
    MONOTONY_DURATION_TOLERANCE,
    SESSION_COUNT_TOLERANCE,
    UNTRAINED_SWIM_MAX_SESSIONS,
    UNTRAINED_SWIM_SESSION_MAX_MINUTES,
)
from app.services.weekly_planning.tiers import BaselineTier
from app.services.weekly_planning.zones import ResolvedIntensityZones


@dataclass(frozen=True, slots=True)
class PlanViolation:
    """One stable validation failure; detail is for logs and never persisted."""

    code: str
    discipline: Discipline | None
    day: date | None
    detail: str


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    violations: tuple[PlanViolation, ...]

    @property
    def ok(self) -> bool:
        return not self.violations


_ENDURANCE_DISCIPLINES = frozenset(
    {Discipline.RUNNING, Discipline.CYCLING, Discipline.SWIMMING}
)
_AVAILABILITY_DISCIPLINE = {
    Discipline.RUNNING: "running",
    Discipline.CYCLING: "cycling",
    Discipline.SWIMMING: "swimming",
    Discipline.STRENGTH: "strength_training",
}
_STRENGTH_PRESCRIPTION = re.compile(
    r"\b(?:\d+\s*x\s*\d+|sets?|reps?|loads?|kg|kgs|lb|lbs|%|one[- ]rep)\b",
    re.IGNORECASE,
)
_STRENGTH_FALLBACK_EXECUTION = (
    "Use controlled form throughout and finish with plenty in reserve."
)
_MIN_USABLE_STRENGTH_EXECUTION_LENGTH = 15
_FIRST_WEEK_SESSION_ADAPTER: TypeAdapter[FirstWeekSession] = TypeAdapter(
    FirstWeekSession
)

_FIRST_WEEK_GUARDRAILS = (
    "Place no hard sessions on back-to-back days.",
    "Do not place a hard session on the day before or after your longest session.",
    "Do not stack hard sessions on the same day; leave room to recover.",
    "Keep every session within a confirmed available window and allowed discipline.",
)
_FIRST_WEEK_LOGGING = (
    "After every session, record duration and RPE, plus how the effort felt.",
    (
        "Record pace, power, or heart rate only when you used that metric and "
        "have it available."
    ),
    (
        "Log the actual day and time you train so future plans can learn your "
        "revealed availability."
    ),
)


def validate_plan(
    plan: WeeklyPlan,
    *,
    readiness: PlanReadiness,
    baseline: AthleteBaselineData | None,
    availability: ConfirmedWeeklyAvailability | None,
    preferences: TrainingPreferences | None,
) -> ValidationOutcome:
    """Check the model's plan without side effects or model calls."""

    violations: list[PlanViolation] = []
    readiness_counts = {
        row.discipline: row.session_count for row in readiness.disciplines
    }
    sessions = tuple(_sessions(plan))
    zero_baseline = {
        discipline
        for discipline in (Discipline.RUNNING, Discipline.CYCLING, Discipline.SWIMMING)
        if _is_zero_baseline(
            discipline=discipline,
            baseline=baseline,
            evidenced_sessions=readiness_counts.get(discipline, 0),
        )
    }

    for day, session in sessions:
        if session.discipline in zero_baseline and session.intensity.is_hard:
            violations.append(
                PlanViolation(
                    "HARD_ON_ZERO_BASELINE",
                    session.discipline,
                    day,
                    "hard session in a discipline with no stated or evidenced volume",
                )
            )
        violations.extend(
            _unsupported_target_violations(day, session, baseline, readiness_counts)
        )
        if session.discipline is Discipline.STRENGTH:
            violations.extend(_strength_violations(day, session))

    swim_sessions = [
        (day, session)
        for day, session in sessions
        if session.discipline is Discipline.SWIMMING
        and Discipline.SWIMMING in zero_baseline
    ]
    if len(swim_sessions) > UNTRAINED_SWIM_MAX_SESSIONS:
        violations.append(
            PlanViolation(
                "UNTRAINED_SWIM_OVERREACH",
                Discipline.SWIMMING,
                None,
                "untrained swimmer has too many sessions",
            )
        )
    for day, session in swim_sessions:
        if (session.targets.duration_minutes or 0) > UNTRAINED_SWIM_SESSION_MAX_MINUTES:
            violations.append(
                PlanViolation(
                    "UNTRAINED_SWIM_OVERREACH",
                    Discipline.SWIMMING,
                    day,
                    "untrained swim exceeds the introductory duration cap",
                )
            )

    violations.extend(_hard_spacing_violations(plan))
    violations.extend(_load_violations(plan))
    violations.extend(_monotony_violations(plan))
    if availability is not None:
        violations.extend(_availability_violations(plan, availability))
    violations.extend(
        _session_count_violations(
            plan,
            readiness=readiness,
            baseline=baseline,
            availability=availability,
            preferences=preferences,
            zero_baseline=zero_baseline,
        )
    )
    return ValidationOutcome(violations=tuple(violations))


def make_first_week_plan(
    prescription: FirstWeekPlanPrescription,
) -> FirstWeekPlan:
    """Add code-owned summaries and non-negotiable athlete placement guardrails."""

    sessions = prescription.sessions
    counts = Counter(session.discipline for session in sessions)
    minutes: dict[Discipline, int] = defaultdict(int)
    for session in sessions:
        minutes[session.discipline] += session.targets.duration_minutes or 0
    return FirstWeekPlan(
        week_start=prescription.week_start,
        sessions=sessions,
        guardrails=tuple(
            dict.fromkeys((*_FIRST_WEEK_GUARDRAILS, *prescription.guardrails))
        ),
        logging_instructions=tuple(
            dict.fromkeys((*_FIRST_WEEK_LOGGING, *prescription.logging_instructions))
        ),
        tests=(),
        sessions_per_discipline=dict(counts),
        total_minutes_per_discipline=dict(minutes),
    )


def validate_first_week_plan(
    plan: FirstWeekPlan,
    *,
    readiness: PlanReadiness,
    baseline: AthleteBaselineData | None,
    availability: ConfirmedWeeklyAvailability | None,
    preferences: TrainingPreferences | None,
    zones: dict[Discipline, ResolvedIntensityZones],
    tiers: dict[Discipline, BaselineTier] | None = None,
) -> ValidationOutcome:
    """Validate an athlete-placed menu without assigning it calendar dates."""

    violations: list[PlanViolation] = []
    readiness_counts = {
        row.discipline: row.session_count for row in readiness.disciplines
    }
    zero_baseline = {
        discipline
        for discipline in _ENDURANCE_DISCIPLINES
        if _is_zero_baseline(
            discipline=discipline,
            baseline=baseline,
            evidenced_sessions=readiness_counts.get(discipline, 0),
        )
    }
    counts: Counter[Discipline] = Counter(
        session.discipline for session in plan.sessions
    )
    for session in plan.sessions:
        violations.extend(_first_week_purpose_violations(session))
        violations.extend(
            _first_week_zone_violations(session, zones.get(session.discipline))
        )
        if session.discipline in zero_baseline and session.intensity.is_hard:
            violations.append(
                PlanViolation(
                    "HARD_ON_ZERO_BASELINE",
                    session.discipline,
                    None,
                    "hard session in a discipline with no stated or evidenced volume",
                )
            )
        if session.discipline is Discipline.STRENGTH:
            violations.extend(_strength_violations(None, session))
        if availability is not None and not _menu_session_fits_availability(
            session, availability
        ):
            violations.append(
                PlanViolation(
                    "AVAILABILITY_CONFLICT",
                    session.discipline,
                    None,
                    "session does not fit any confirmed window for its discipline",
                )
            )
    violations.extend(_first_week_distinctness_violations(plan))
    if tiers is not None:
        violations.extend(_first_week_tier_demand_violations(plan, zones, tiers))
    if preferences is not None:
        for discipline, requested in preferences.desired_weekly_sessions.items():
            if requested <= 0 or discipline in zero_baseline:
                continue
            if counts.get(discipline, 0) != requested:
                violations.append(
                    PlanViolation(
                        "SESSION_COUNT_UNDERSHOOT",
                        discipline,
                        None,
                        "first-week menu does not meet the stated requested frequency",
                    )
                )
    return ValidationOutcome(violations=tuple(violations))


def _first_week_purpose_violations(session: PlanSession) -> list[PlanViolation]:
    """Require the overview purpose to be one concise, complete sentence."""

    purpose = session.purpose.strip()
    ending_count = len(re.findall(r"[.!?]", purpose))
    has_single_natural_ending = ending_count == 1 and purpose.endswith((".", "!", "?"))
    if len(purpose) <= 120 and has_single_natural_ending:
        return []
    return [
        PlanViolation(
            "FIRST_WEEK_PURPOSE_NOT_CONCISE",
            session.discipline,
            None,
            "purpose must be one complete sentence of at most 120 characters",
        )
    ]


def _first_week_distinctness_violations(
    plan: FirstWeekPlan,
) -> list[PlanViolation]:
    """Reject repeated menu cards that cannot reveal different training signals."""

    seen: set[tuple[object, ...]] = set()
    violations: list[PlanViolation] = []
    for session in plan.sessions:
        signature = (
            session.discipline,
            session.purpose.casefold().strip(),
            session.intensity.metric,
            session.intensity.target_range,
            session.intensity.rpe_range,
            session.objective.casefold().strip(),
            session.execution.casefold().strip(),
        )
        if signature in seen:
            violations.append(
                PlanViolation(
                    "FIRST_WEEK_DUPLICATE_SESSION",
                    session.discipline,
                    None,
                    (
                        "sessions in a first-week menu must differ in purpose, "
                        "intensity, or execution"
                    ),
                )
            )
        seen.add(signature)
    return violations


def _first_week_tier_demand_violations(
    plan: FirstWeekPlan,
    zones: dict[Discipline, ResolvedIntensityZones],
    tiers: dict[Discipline, BaselineTier],
) -> list[PlanViolation]:
    """Keep tiered calibration signal deterministic instead of prompt-only."""

    by_discipline: dict[Discipline, list[PlanSession]] = defaultdict(list)
    for session in plan.sessions:
        by_discipline[session.discipline].append(session)
    violations: list[PlanViolation] = []
    for discipline, sessions in by_discipline.items():
        tier = tiers.get(discipline)
        if tier is None:
            continue
        if tier == "UNPREPARED":
            if any(session.intensity.rpe_range[1] > 4 for session in sessions):
                violations.append(
                    PlanViolation(
                        "FIRST_WEEK_UNPREPARED_TOO_HARD",
                        discipline,
                        None,
                        (
                            "unprepared disciplines must remain at an easy "
                            "perceived effort"
                        ),
                    )
                )
            continue
        zone = zones.get(discipline)
        if (
            zone is None
            or zone.mode != "NUMERIC"
            or len(sessions) < 2
            or tier not in {"DEVELOPING", "TRAINED", "WELL_TRAINED"}
        ):
            continue
        if not any(session.intensity.rpe_range[0] >= 5 for session in sessions):
            violations.append(
                PlanViolation(
                    "FIRST_WEEK_CALIBRATION_SIGNAL_MISSING",
                    discipline,
                    None,
                    (
                        "prepared discipline with numeric zones needs controlled "
                        "moderate work"
                    ),
                )
            )
    return violations


def _first_week_zone_violations(
    session: PlanSession, zone: ResolvedIntensityZones | None
) -> list[PlanViolation]:
    if zone is None:
        return []
    if zone.mode == "RPE_FALLBACK":
        if session.intensity.metric == "RPE":
            return []
        return [
            PlanViolation(
                "FIRST_WEEK_RPE_REQUIRED",
                session.discipline,
                None,
                "no usable threshold exists; this discipline must use RPE guidance",
            )
        ]
    if session.intensity.metric == "RPE":
        return []
    allowed_ranges = tuple(
        value for value in (zone.easy, zone.moderate, zone.hard) if value
    )
    lower, upper = session.intensity.target_range
    if session.intensity.metric != zone.metric or not any(
        lower >= allowed[0] and upper <= allowed[1] for allowed in allowed_ranges
    ):
        return [
            PlanViolation(
                "FIRST_WEEK_ZONE_CONFLICT",
                session.discipline,
                None,
                "intensity target is outside the resolved first-week zones",
            )
        ]
    return []


def _menu_session_fits_availability(
    session: PlanSession, availability: ConfirmedWeeklyAvailability
) -> bool:
    allowed = _AVAILABILITY_DISCIPLINE.get(session.discipline)
    duration = session.targets.duration_minutes or 0
    return any(
        details.available
        and allowed in details.disciplines
        and any(window.duration_minutes >= duration for window in details.time_windows)
        for details in availability.days.values()
    )


def repair_plan(
    plan: WeeklyPlan | FirstWeekPlan,
    violations: Iterable[PlanViolation],
    *,
    baseline: AthleteBaselineData | None,
    availability: ConfirmedWeeklyAvailability | None = None,
) -> WeeklyPlan | FirstWeekPlan:
    """Apply one deterministic, idempotent repair pass without adding sessions."""

    if isinstance(plan, FirstWeekPlan):
        return _repair_first_week_menu(plan, violations)

    payload = plan.model_dump(mode="json")
    violation_list = tuple(violations)
    codes = {item.code for item in violation_list}
    entries = list(_payload_sessions(payload))

    for violation in violation_list:
        if violation.code not in {
            "HARD_ON_ZERO_BASELINE",
            "CONSECUTIVE_HARD",
            "HARD_ADJACENT_TO_LONGEST",
        }:
            continue
        for _, _, raw in _matching_payload_entries(
            entries, violation, week_start=plan.week_start
        ):
            raw["intensity"] = _easy_intensity()
            targets = _targets(raw)
            for field in (
                "pace_seconds_per_km",
                "swim_pace_seconds_per_100m",
                "average_power_watts",
            ):
                targets[field] = None

    for violation in violation_list:
        if violation.code != "UNSUPPORTED_TARGET":
            continue
        for _, _, raw in _matching_payload_entries(
            entries, violation, week_start=plan.week_start
        ):
            targets = _targets(raw)
            field = violation.detail
            if field in targets:
                targets[field] = None
                _add_rpe_if_duration_only(raw)

    if "UNTRAINED_SWIM_OVERREACH" in codes:
        untrained_swims = [
            entry
            for entry in entries
            if entry[2].get("discipline") == Discipline.SWIMMING.value
        ]
        for _, _, raw in untrained_swims:
            targets = _targets(raw)
            duration = targets.get("duration_minutes")
            if (
                isinstance(duration, int)
                and duration > UNTRAINED_SWIM_SESSION_MAX_MINUTES
            ):
                targets["duration_minutes"] = UNTRAINED_SWIM_SESSION_MAX_MINUTES
        for day_index, session_index, _ in sorted(
            untrained_swims,
            key=lambda item: _payload_duration(item[2]),
        )[UNTRAINED_SWIM_MAX_SESSIONS:]:
            _drop_payload_session(payload, day_index, session_index)

    for violation in violation_list:
        if violation.code != "STRENGTH_OVER_SPECIFIED":
            continue
        for _, _, raw in _matching_payload_entries(
            entries, violation, week_start=plan.week_start
        ):
            duration = _targets(raw).get("duration_minutes")
            raw["targets"] = {"duration_minutes": duration}

    if "AVAILABILITY_CONFLICT" in codes and availability is not None:
        _repair_availability(payload, availability)
    if "SESSION_COUNT_OVERSHOOT" in codes:
        _drop_lowest_value_sessions(
            payload,
            baseline=baseline,
            violations=tuple(
                item
                for item in violation_list
                if item.code == "SESSION_COUNT_OVERSHOOT"
            ),
        )
    if "EXCESSIVE_CONSECUTIVE_LOAD" in codes:
        _drop_excessive_load_session(payload)
    return WeeklyPlan.model_validate(payload)


def build_fallback_week(
    week_start: date,
    *,
    baseline: AthleteBaselineData | None,
    availability: ConfirmedWeeklyAvailability | None,
    preferences: TrainingPreferences | None,
    disciplines: Iterable[Discipline] = (),
) -> WeeklyPlan:
    """Build a safe, simple week when a generated plan cannot be repaired."""

    target_disciplines = tuple(dict.fromkeys(disciplines))
    if not target_disciplines:
        target_disciplines = tuple(
            discipline
            for discipline in (
                Discipline.RUNNING,
                Discipline.CYCLING,
                Discipline.SWIMMING,
            )
            if _baseline_for(baseline, discipline) is not None
        )
    days: list[dict[str, object]] = [
        {
            "date": date.fromordinal(week_start.toordinal() + offset),
            "sessions": [],
            "rest_note": "Rest and recover.",
        }
        for offset in range(7)
    ]
    remaining = [
        (
            sum(
                window.duration_minutes
                for window in availability.days[
                    date.fromordinal(week_start.toordinal() + offset)
                    .strftime("%A")
                    .casefold()
                ].time_windows
            )
            if availability is not None
            else 360
        )
        for offset in range(7)
    ]
    scheduled_counts = [0] * 7
    for discipline in target_disciplines:
        expected = _expected_sessions(discipline, baseline, preferences)
        base = _baseline_for(baseline, discipline)
        duration_total = _duration_total(base)
        adaptive_strength_duration = False
        if expected <= 0:
            expected = 1
        if duration_total <= 0:
            adaptive_strength_duration = discipline is Discipline.STRENGTH
            if not adaptive_strength_duration:
                duration_total = UNTRAINED_SWIM_SESSION_MAX_MINUTES
                expected = 1
        if discipline is Discipline.SWIMMING and _is_zero_baseline(
            discipline=discipline, baseline=baseline, evidenced_sessions=0
        ):
            expected = min(expected, UNTRAINED_SWIM_MAX_SESSIONS)
            duration_total = min(duration_total, UNTRAINED_SWIM_SESSION_MAX_MINUTES)
        eligible = _fallback_day_indexes(week_start, discipline, availability)
        for _ in range(expected):
            fallback_duration = max(5, duration_total // max(1, expected))
            candidates = [
                index
                for index in eligible
                if remaining[index]
                >= (5 if adaptive_strength_duration else fallback_duration)
            ]
            if not candidates:
                break
            index = _fallback_day_index(
                candidates=candidates,
                remaining=remaining,
                scheduled_counts=scheduled_counts,
                prefer_longer_window=adaptive_strength_duration,
            )
            duration = (
                min(60, remaining[index])
                if adaptive_strength_duration
                else fallback_duration
            )
            session: dict[str, object] = {
                "discipline": discipline.value,
                "purpose": _fallback_purpose(discipline),
                "intensity": _easy_intensity(),
                "objective": _fallback_objective(discipline),
                "targets": {"duration_minutes": duration, "rpe": 3},
                "execution": _fallback_execution(discipline),
            }
            if discipline is Discipline.STRENGTH:
                session["targets"] = {"duration_minutes": duration}
            raw_sessions = days[index]["sessions"]
            if isinstance(raw_sessions, list):
                raw_sessions.append(session)
                remaining[index] -= duration
                scheduled_counts[index] += 1
            days[index]["rest_note"] = None
    return WeeklyPlan.model_validate({"week_start": week_start, "days": days})


def _repair_first_week_menu(
    plan: FirstWeekPlan, violations: Iterable[PlanViolation]
) -> FirstWeekPlan:
    """Repair a menu without inventing dates or re-running placement."""

    codes = {violation.code for violation in violations}
    payload = plan.model_dump(mode="json")
    raw_sessions = payload.get("sessions")
    if not isinstance(raw_sessions, list):
        return plan
    if codes & {
        "FIRST_WEEK_RPE_REQUIRED",
        "FIRST_WEEK_ZONE_CONFLICT",
        "HARD_ON_ZERO_BASELINE",
        "UNSUPPORTED_TARGET",
    }:
        for raw in raw_sessions:
            if not isinstance(raw, dict):
                continue
            raw["intensity"] = _easy_intensity()
            targets = _targets(raw)
            targets["rpe"] = 3
            for field in (
                "average_hr_bpm",
                "hr_range_bpm",
                "average_power_watts",
                "pace_seconds_per_km",
                "swim_pace_seconds_per_100m",
            ):
                targets[field] = None
    if "STRENGTH_OVER_SPECIFIED" in codes:
        for raw in raw_sessions:
            if raw.get("discipline") != Discipline.STRENGTH.value:
                continue
            targets = _targets(raw)
            execution = raw.get("execution")
            execution_text = execution if isinstance(execution, str) else ""
            extra_targets = [
                field
                for field, value in targets.items()
                if field != "duration_minutes" and value is not None
            ]
            if not extra_targets and not _STRENGTH_PRESCRIPTION.search(execution_text):
                continue  # this sibling session never violated; leave it untouched
            duration = targets.get("duration_minutes")
            raw["targets"] = {"duration_minutes": duration}
            raw["execution"] = _strip_strength_prescription(execution_text)
    sessions = tuple(
        _FIRST_WEEK_SESSION_ADAPTER.validate_python(raw) for raw in raw_sessions
    )
    return make_first_week_plan(
        FirstWeekPlanPrescription(
            week_start=plan.week_start,
            sessions=sessions,
            guardrails=plan.guardrails,
            logging_instructions=plan.logging_instructions,
        )
    )


def _sessions(plan: WeeklyPlan) -> Iterable[tuple[date, PlanSession]]:
    for day in plan.days:
        for session in day.sessions:
            yield day.date, session


def _baseline_for(
    baseline: AthleteBaselineData | None, discipline: Discipline
) -> object | None:
    if baseline is None:
        return None
    return {
        Discipline.RUNNING: baseline.running,
        Discipline.CYCLING: baseline.cycling,
        Discipline.SWIMMING: baseline.swimming,
    }.get(discipline)


def _duration_total(baseline: object | None) -> int:
    value = getattr(baseline, "typical_weekly_duration_minutes", 0)
    return value if isinstance(value, int) else 0


def _is_zero_baseline(
    *,
    discipline: Discipline,
    baseline: AthleteBaselineData | None,
    evidenced_sessions: int,
) -> bool:
    stated = _baseline_for(baseline, discipline)
    return (
        stated is not None
        and getattr(stated, "typical_weekly_sessions", None) == 0
        and _duration_total(stated) == 0
        and evidenced_sessions == 0
    )


def _unsupported_target_violations(
    day: date,
    session: PlanSession,
    baseline: AthleteBaselineData | None,
    readiness_counts: dict[Discipline, int],
) -> list[PlanViolation]:
    targets = session.targets
    violations: list[PlanViolation] = []
    cycling = _baseline_for(baseline, Discipline.CYCLING)
    running = _baseline_for(baseline, Discipline.RUNNING)
    swimming = _baseline_for(baseline, Discipline.SWIMMING)
    if (
        targets.average_power_watts is not None
        and getattr(cycling, "recent_ftp_watts", None) is None
    ):
        violations.append(
            PlanViolation(
                "UNSUPPORTED_TARGET", session.discipline, day, "average_power_watts"
            )
        )
    if targets.pace_seconds_per_km is not None and (
        getattr(running, "recent_race_result", None) is None
        and readiness_counts.get(Discipline.RUNNING, 0) == 0
    ):
        violations.append(
            PlanViolation(
                "UNSUPPORTED_TARGET", session.discipline, day, "pace_seconds_per_km"
            )
        )
    if (
        targets.swim_pace_seconds_per_100m is not None
        and getattr(swimming, "recent_400m_seconds", None) is None
    ):
        violations.append(
            PlanViolation(
                "UNSUPPORTED_TARGET",
                session.discipline,
                day,
                "swim_pace_seconds_per_100m",
            )
        )
    return violations


def _strip_strength_prescription(execution: str) -> str:
    """Drop only the sentence(s) naming explicit sets, reps, or loads.

    Splits on sentence boundaries and removes just the offending sentence,
    keeping any surrounding equipment-appropriate detail intact. Falls back to
    the generic safety literal only when nothing usable survives the strip.
    """

    sentences = re.split(r"(?<=[.!?])\s+", execution.strip())
    kept = [
        sentence
        for sentence in sentences
        if sentence and not _STRENGTH_PRESCRIPTION.search(sentence)
    ]
    cleaned = " ".join(kept).strip()
    if len(cleaned) < _MIN_USABLE_STRENGTH_EXECUTION_LENGTH:
        return _STRENGTH_FALLBACK_EXECUTION
    return cleaned


def _strength_violations(day: date | None, session: PlanSession) -> list[PlanViolation]:
    targets = session.targets.model_dump()
    extra_targets = [
        field
        for field, value in targets.items()
        if field != "duration_minutes" and value is not None
    ]
    if extra_targets or _STRENGTH_PRESCRIPTION.search(session.execution):
        return [
            PlanViolation(
                "STRENGTH_OVER_SPECIFIED",
                Discipline.STRENGTH,
                day,
                "strength targets or execution specify sets, reps, or loads",
            )
        ]
    return []


def _hard_spacing_violations(plan: WeeklyPlan) -> list[PlanViolation]:
    hard_dates = {
        day.date
        for day in plan.days
        if any(session.intensity.is_hard for session in day.sessions)
    }
    violations: list[PlanViolation] = []
    for day in sorted(hard_dates):
        if date.fromordinal(day.toordinal() - 1) in hard_dates:
            violations.append(
                PlanViolation(
                    "CONSECUTIVE_HARD", None, day, "hard sessions on adjacent days"
                )
            )
    all_sessions = tuple(_sessions(plan))
    if not all_sessions:
        return violations
    longest = max(session.targets.duration_minutes or 0 for _, session in all_sessions)
    longest_dates = {
        day
        for day, session in all_sessions
        if (session.targets.duration_minutes or 0) == longest
    }
    for day, session in all_sessions:
        if session.intensity.is_hard and any(
            abs(day.toordinal() - longest_day.toordinal()) == 1
            for longest_day in longest_dates
        ):
            violations.append(
                PlanViolation(
                    "HARD_ADJACENT_TO_LONGEST",
                    session.discipline,
                    day,
                    "hard session adjacent to the longest session",
                )
            )
    return violations


def _load_violations(plan: WeeklyPlan) -> list[PlanViolation]:
    endurance_days = [
        day.date
        for day in plan.days
        if any(session.discipline in _ENDURANCE_DISCIPLINES for session in day.sessions)
    ]
    if len(endurance_days) > MAX_CONSECUTIVE_LOAD_DAYS:
        return [
            PlanViolation(
                "EXCESSIVE_CONSECUTIVE_LOAD",
                None,
                endurance_days[-1],
                "endurance load spans every day of the week",
            )
        ]
    return []


def _monotony_violations(plan: WeeklyPlan) -> list[PlanViolation]:
    grouped: dict[tuple[Discipline, str, tuple[int, int]], list[tuple[date, int]]] = (
        defaultdict(list)
    )
    for day, session in _sessions(plan):
        grouped[
            (
                session.discipline,
                session.intensity.metric,
                session.intensity.rpe_range,
            )
        ].append((day, session.targets.duration_minutes or 0))
    violations: list[PlanViolation] = []
    for (discipline, metric, rpe_range), entries in grouped.items():
        clusters: list[list[tuple[date, int]]] = []
        for item in sorted(entries, key=lambda value: value[1]):
            for cluster in clusters:
                reference = cluster[0][1]
                if (
                    reference
                    and abs(item[1] - reference) / reference
                    <= MONOTONY_DURATION_TOLERANCE
                ):
                    cluster.append(item)
                    break
            else:
                clusters.append([item])
        for cluster in clusters:
            if len(cluster) > MAX_IDENTICAL_SESSIONS:
                violations.append(
                    PlanViolation(
                        "MONOTONY",
                        discipline,
                        cluster[-1][0],
                        (
                            f"{metric} {rpe_range} sessions repeat with nearly "
                            "identical duration"
                        ),
                    )
                )
    return violations


def _availability_violations(
    plan: WeeklyPlan, availability: ConfirmedWeeklyAvailability
) -> list[PlanViolation]:
    violations: list[PlanViolation] = []
    sessions_by_day: dict[date, list[PlanSession]] = defaultdict(list)
    for day, session in _sessions(plan):
        sessions_by_day[day].append(session)
        details = availability.days[day.strftime("%A").casefold()]
        limit = sum(window.duration_minutes for window in details.time_windows)
        allowed = _AVAILABILITY_DISCIPLINE.get(session.discipline)
        if (
            not details.available
            or allowed not in details.disciplines
            or (session.targets.duration_minutes or 0) > limit
        ):
            violations.append(
                PlanViolation(
                    "AVAILABILITY_CONFLICT",
                    session.discipline,
                    day,
                    "session does not fit confirmed availability",
                )
            )
    for day, sessions in sessions_by_day.items():
        details = availability.days[day.strftime("%A").casefold()]
        limit = sum(window.duration_minutes for window in details.time_windows)
        total = sum(session.targets.duration_minutes or 0 for session in sessions)
        if details.available and total > limit:
            violations.append(
                PlanViolation(
                    "AVAILABILITY_CONFLICT",
                    None,
                    day,
                    "daily session total exceeds confirmed availability",
                )
            )
    return violations


def _repair_availability(
    payload: dict[str, object], availability: ConfirmedWeeklyAvailability
) -> None:
    """Make each day fit its permitted disciplines and total available minutes."""

    raw_days = payload.get("days")
    if not isinstance(raw_days, list):
        return
    for raw_day in raw_days:
        if not isinstance(raw_day, dict):
            continue
        raw_date = raw_day.get("date")
        sessions = raw_day.get("sessions")
        if not isinstance(raw_date, str) or not isinstance(sessions, list):
            continue
        try:
            day = date.fromisoformat(raw_date)
        except ValueError:
            continue
        details = availability.days[day.strftime("%A").casefold()]
        limit = sum(window.duration_minutes for window in details.time_windows)
        allowed = set(details.disciplines) if details.available else set()
        valid_sessions = [
            session
            for session in sessions
            if isinstance(session, dict)
            and session.get("discipline")
            in {
                discipline.value
                for discipline, name in _AVAILABILITY_DISCIPLINE.items()
                if name in allowed
            }
        ]
        sessions[:] = valid_sessions
        for session in sessions:
            targets = _targets(session)
            duration = _payload_duration(session)
            if duration > limit:
                targets["duration_minutes"] = limit

        total = sum(_payload_duration(session) for session in sessions)
        for session in sorted(sessions, key=_payload_duration):
            if total <= limit:
                break
            duration = _payload_duration(session)
            reduced_duration = duration - (total - limit)
            if reduced_duration >= 5:
                _targets(session)["duration_minutes"] = reduced_duration
                total = limit
            else:
                sessions.remove(session)
                total -= duration
        if not sessions:
            raw_day["rest_note"] = "Rest and recover."


def _expected_sessions(
    discipline: Discipline,
    baseline: AthleteBaselineData | None,
    preferences: TrainingPreferences | None,
) -> int:
    if preferences is not None and discipline in preferences.desired_weekly_sessions:
        return preferences.desired_weekly_sessions[discipline]
    stated = _baseline_for(baseline, discipline)
    value = getattr(stated, "typical_weekly_sessions", 0)
    return value if isinstance(value, int) else 0


def _session_count_violations(
    plan: WeeklyPlan,
    *,
    readiness: PlanReadiness,
    baseline: AthleteBaselineData | None,
    availability: ConfirmedWeeklyAvailability | None,
    preferences: TrainingPreferences | None,
    zero_baseline: set[Discipline],
) -> list[PlanViolation]:
    counts = Counter(session.discipline for _, session in _sessions(plan))
    violations: list[PlanViolation] = []
    for row in readiness.disciplines:
        discipline = row.discipline
        expected = _expected_sessions(discipline, baseline, preferences)
        actual = counts[discipline]
        if actual > expected + SESSION_COUNT_TOLERANCE:
            violations.append(
                PlanViolation(
                    "SESSION_COUNT_OVERSHOOT",
                    discipline,
                    None,
                    "plan contains more sessions than requested",
                )
            )
        lower = expected - SESSION_COUNT_TOLERANCE
        capacity = _discipline_availability_capacity(discipline, availability)
        if (
            actual < lower
            and discipline not in zero_baseline
            and (capacity is None or actual < capacity)
        ):
            violations.append(
                PlanViolation(
                    "SESSION_COUNT_UNDERSHOOT",
                    discipline,
                    None,
                    "plan contains fewer sessions than requested",
                )
            )
    return violations


def _discipline_availability_capacity(
    discipline: Discipline, availability: ConfirmedWeeklyAvailability | None
) -> int | None:
    if availability is None:
        return None
    name = _AVAILABILITY_DISCIPLINE.get(discipline)
    if name is None:
        return 0
    return sum(
        day.available and bool(day.time_windows) and name in day.disciplines
        for day in availability.days.values()
    )


def _payload_sessions(
    payload: dict[str, object],
) -> Iterable[tuple[int, int, dict[str, object]]]:
    raw_days = payload.get("days")
    if not isinstance(raw_days, list):
        return ()
    result: list[tuple[int, int, dict[str, object]]] = []
    for day_index, day in enumerate(raw_days):
        if not isinstance(day, dict) or not isinstance(day.get("sessions"), list):
            continue
        for session_index, session in enumerate(day["sessions"]):
            if isinstance(session, dict):
                result.append((day_index, session_index, session))
    return tuple(result)


def _matching_payload_entries(
    entries: Iterable[tuple[int, int, dict[str, object]]],
    violation: PlanViolation,
    *,
    week_start: date,
) -> Iterable[tuple[int, int, dict[str, object]]]:
    for day_index, session_index, raw in entries:
        raw_discipline = raw.get("discipline")
        if (
            violation.discipline is not None
            and raw_discipline != violation.discipline.value
        ):
            continue
        if (
            violation.day is not None
            and date.fromordinal(week_start.toordinal() + day_index) != violation.day
        ):
            continue
        yield day_index, session_index, raw


def _targets(raw: dict[str, object]) -> dict[str, object]:
    targets = raw.get("targets")
    if isinstance(targets, dict):
        return targets
    raw["targets"] = {}
    return {}


def _add_rpe_if_duration_only(raw: dict[str, object]) -> None:
    targets = _targets(raw)
    if set(targets) <= {"duration_minutes"}:
        intensity = raw.get("intensity")
        if isinstance(intensity, dict):
            rpe_range = intensity.get("rpe_range")
            if (
                isinstance(rpe_range, (list, tuple))
                and len(rpe_range) == 2
                and all(isinstance(value, int) for value in rpe_range)
            ):
                targets["rpe"] = sum(rpe_range) // 2


def _easy_intensity() -> dict[str, object]:
    return {
        "metric": "RPE",
        "target_range": [2, 3],
        "rpe_range": [2, 3],
        "guidance": "Easy, conversational effort with relaxed breathing.",
    }


def _fallback_day_index(
    *,
    candidates: list[int],
    remaining: list[int],
    scheduled_counts: list[int],
    prefer_longer_window: bool,
) -> int:
    if prefer_longer_window:
        return min(
            candidates,
            # Spread the week first.  On an empty day, use the smallest adequate
            # window and reserve weekend capacity for longer endurance work.  If
            # every day already has a session, longer windows break the tie.
            key=lambda candidate: (
                scheduled_counts[candidate],
                (
                    remaining[candidate]
                    if scheduled_counts[candidate] == 0
                    else -remaining[candidate]
                ),
                candidate,
            ),
        )
    return min(candidates, key=lambda candidate: scheduled_counts[candidate])


def _fallback_purpose(discipline: Discipline) -> str:
    return {
        Discipline.RUNNING: "Build easy aerobic consistency.",
        Discipline.CYCLING: "Build easy aerobic consistency.",
        Discipline.SWIMMING: "Build water comfort and relaxed technique.",
        Discipline.STRENGTH: "Maintain general strength and movement quality.",
    }.get(discipline, "Build consistent, manageable training.")


def _payload_duration(raw: dict[str, object]) -> int:
    duration = _targets(raw).get("duration_minutes")
    return duration if isinstance(duration, int) else 0


def _drop_payload_session(
    payload: dict[str, object], day_index: int, session_index: int
) -> None:
    raw_days = payload.get("days")
    if not isinstance(raw_days, list) or not isinstance(raw_days[day_index], dict):
        return
    day = raw_days[day_index]
    sessions = day.get("sessions")
    if not isinstance(sessions, list) or not 0 <= session_index < len(sessions):
        return
    sessions.pop(session_index)
    if not sessions:
        day["rest_note"] = "Rest and recover."


def _drop_lowest_value_sessions(
    payload: dict[str, object],
    *,
    baseline: AthleteBaselineData | None,
    violations: tuple[PlanViolation, ...],
) -> None:
    constrained_disciplines = {
        violation.discipline
        for violation in violations
        if violation.code in {"AVAILABILITY_CONFLICT", "SESSION_COUNT_OVERSHOOT"}
        and violation.discipline is not None
    }
    priority = _priority_discipline(baseline)
    availability_drops = sum(
        violation.code == "AVAILABILITY_CONFLICT" for violation in violations
    )
    overshoot_drops = 0
    preferences = baseline.preferences if baseline is not None else None
    for discipline in constrained_disciplines:
        actual = sum(
            _payload_discipline(raw) is discipline
            for _, _, raw in _payload_sessions(payload)
        )
        permitted = (
            _expected_sessions(discipline, baseline, preferences)
            + SESSION_COUNT_TOLERANCE
        )
        overshoot_drops = max(overshoot_drops, actual - permitted)
    drop_count = max(availability_drops, overshoot_drops)
    for _ in range(max(0, drop_count)):
        candidates = [
            item
            for item in _payload_sessions(payload)
            if not constrained_disciplines
            or _payload_discipline(item[2]) in constrained_disciplines
        ]
        if not candidates:
            return
        day_index, session_index, _ = min(
            candidates,
            key=lambda item: (
                _payload_discipline(item[2]) == priority,
                _payload_duration(item[2]),
            ),
        )
        _drop_payload_session(payload, day_index, session_index)


def _priority_discipline(baseline: AthleteBaselineData | None) -> Discipline | None:
    if baseline is None or baseline.preferences is None:
        return None
    preferences = baseline.preferences
    candidates = (
        Discipline.RUNNING,
        Discipline.CYCLING,
        Discipline.SWIMMING,
        Discipline.STRENGTH,
    )
    return max(
        candidates,
        key=lambda discipline: (
            preferences.desired_weekly_sessions.get(discipline, 0)
            - getattr(_baseline_for(baseline, discipline), "typical_weekly_sessions", 0)
        ),
    )


def _drop_excessive_load_session(payload: dict[str, object]) -> None:
    raw_days = payload.get("days")
    if not isinstance(raw_days, list):
        return
    run_length = 0
    breaking_day: int | None = None
    for day_index, day in enumerate(raw_days):
        sessions = day.get("sessions") if isinstance(day, dict) else None
        carries_endurance = isinstance(sessions, list) and any(
            isinstance(session, dict)
            and _payload_discipline(session) in _ENDURANCE_DISCIPLINES
            for session in sessions
        )
        run_length = run_length + 1 if carries_endurance else 0
        if run_length > MAX_CONSECUTIVE_LOAD_DAYS:
            breaking_day = day_index
            break
    if breaking_day is None:
        return
    candidates = [
        item
        for item in _payload_sessions(payload)
        if item[0] == breaking_day
        and _payload_discipline(item[2]) in _ENDURANCE_DISCIPLINES
    ]
    if candidates:
        day_index, session_index, _ = min(
            candidates, key=lambda item: _payload_duration(item[2])
        )
        _drop_payload_session(payload, day_index, session_index)


def _payload_discipline(raw: dict[str, object]) -> Discipline | None:
    value = raw.get("discipline")
    if not isinstance(value, str):
        return None
    try:
        return Discipline(value)
    except ValueError:
        return None


def _fallback_day_indexes(
    week_start: date,
    discipline: Discipline,
    availability: ConfirmedWeeklyAvailability | None,
) -> list[int]:
    if availability is None:
        return list(range(7))
    name = _AVAILABILITY_DISCIPLINE.get(discipline)
    return [
        offset
        for offset in range(7)
        if (
            details := availability.days[
                date.fromordinal(week_start.toordinal() + offset)
                .strftime("%A")
                .casefold()
            ]
        ).available
        and bool(details.time_windows)
        and name in details.disciplines
    ]


def _fallback_objective(discipline: Discipline) -> str:
    if discipline is Discipline.SWIMMING:
        return "Build water comfort, breathing, and body position."
    if discipline is Discipline.STRENGTH:
        return "Maintain legs and core movement patterns."
    return f"Build consistent easy {discipline.value.lower()} training."


def _fallback_execution(discipline: Discipline) -> str:
    if discipline is Discipline.SWIMMING:
        return (
            "Pool technique: breathing, body position, and short 25 m repeats "
            "with generous rest."
        )
    if discipline is Discipline.STRENGTH:
        return "General legs and core maintenance at a comfortable level."
    return "Keep the effort easy and conversational throughout."
