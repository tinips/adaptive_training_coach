"""Deterministic question registry and parsing for self-reported baselines."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.enums import Discipline
from app.schemas.baseline import (
    AthleteBaselineData,
    CyclingBaseline,
    RecentRaceResult,
    RunningBaseline,
    SwimmingBaseline,
    TriathlonBaseline,
)


@dataclass(frozen=True, slots=True)
class BaselineQuestion:
    key: str
    prompt: str


_DURATION = re.compile(r"(?:(?P<hours>\d+):)?(?P<minutes>\d{1,2}):(?P<seconds>\d{2})")
_RACE = re.compile(
    r"(?P<distance>\d+(?:\.\d+)?)\s*(?:km|k)\s*[,;]\s*"
    r"(?P<time>(?:\d+:)?\d{1,2}:\d{2})",
    re.IGNORECASE,
)

_OPTIONAL_FIELDS = frozenset(
    {
        "running.recent_race_result",
        "cycling.recent_ftp_watts",
        "swimming.pool_length_meters",
        "swimming.recent_400m_seconds",
    }
)

_QUESTIONS: dict[str, BaselineQuestion] = {
    "running.typical_weekly_sessions": BaselineQuestion(
        "running.typical_weekly_sessions",
        "Running: typical sessions per week over the last 4 weeks (0 to 14).",
    ),
    "running.typical_weekly_duration_minutes": BaselineQuestion(
        "running.typical_weekly_duration_minutes",
        "Running: typical total minutes per week over the last 4 weeks.",
    ),
    "running.longest_recent_run_minutes": BaselineQuestion(
        "running.longest_recent_run_minutes",
        "Running: longest run in the last 14 days (minutes). Send 0 if none.",
    ),
    "running.recent_race_result": BaselineQuestion(
        "running.recent_race_result",
        "Recent run race or time trial (optional). Send e.g. `5 km, 25:30`, or `skip`.",
    ),
    "cycling.typical_weekly_sessions": BaselineQuestion(
        "cycling.typical_weekly_sessions",
        "Cycling: typical rides per week over the last 4 weeks (0 to 14).",
    ),
    "cycling.typical_weekly_duration_minutes": BaselineQuestion(
        "cycling.typical_weekly_duration_minutes",
        "Cycling: typical total minutes per week over the last 4 weeks.",
    ),
    "cycling.longest_recent_ride_minutes": BaselineQuestion(
        "cycling.longest_recent_ride_minutes",
        "Cycling: longest ride in the last 14 days (minutes). Send 0 if none.",
    ),
    "cycling.riding_environment": BaselineQuestion(
        "cycling.riding_environment",
        "Cycling: where can you currently ride? indoor, outdoor, both, or none.",
    ),
    "cycling.riding_confidence": BaselineQuestion(
        "cycling.riding_confidence",
        "Cycling confidence: new rider, simple routes, confident, or not "
        "currently riding.",
    ),
    "cycling.recent_ftp_watts": BaselineQuestion(
        "cycling.recent_ftp_watts",
        "Recent FTP in watts (optional). Send a value or `skip`.",
    ),
    "swimming.typical_weekly_sessions": BaselineQuestion(
        "swimming.typical_weekly_sessions",
        "Swimming: typical swims per week over the last 4 weeks (0 to 14).",
    ),
    "swimming.typical_weekly_duration_minutes": BaselineQuestion(
        "swimming.typical_weekly_duration_minutes",
        "Swimming: typical total minutes per week over the last 4 weeks.",
    ),
    "swimming.longest_continuous_swim_meters": BaselineQuestion(
        "swimming.longest_continuous_swim_meters",
        "Swimming: longest continuous swim in the last 4 weeks (meters). "
        "Send 0 if none.",
    ),
    "swimming.swimming_environment": BaselineQuestion(
        "swimming.swimming_environment",
        "Swimming: where can you currently swim? pool, open water, both, or none.",
    ),
    "swimming.pool_length_meters": BaselineQuestion(
        "swimming.pool_length_meters",
        "Pool length in meters (optional): 25, 50, or `skip`.",
    ),
    "swimming.recent_400m_seconds": BaselineQuestion(
        "swimming.recent_400m_seconds",
        "Recent 400m swim time (optional). Send `MM:SS` or `skip`.",
    ),
    "triathlon.prior_experience": BaselineQuestion(
        "triathlon.prior_experience",
        "Triathlon: prior experience - none, sprint, olympic, or long course.",
    ),
    "triathlon.weakest_discipline": BaselineQuestion(
        "triathlon.weakest_discipline",
        "Triathlon: self-assessed weakest discipline - running, cycling, "
        "swimming, or no clear weakness.",
    ),
    "triathlon.open_water_confidence": BaselineQuestion(
        "triathlon.open_water_confidence",
        "Triathlon: open-water confidence - not confident, some experience, "
        "or confident.",
    ),
}

_FIELDS_BY_DISCIPLINE: dict[Discipline, tuple[str, ...]] = {
    Discipline.RUNNING: (
        "running.typical_weekly_sessions",
        "running.typical_weekly_duration_minutes",
        "running.longest_recent_run_minutes",
        "running.recent_race_result",
    ),
    Discipline.CYCLING: (
        "cycling.typical_weekly_sessions",
        "cycling.typical_weekly_duration_minutes",
        "cycling.longest_recent_ride_minutes",
        "cycling.riding_environment",
        "cycling.riding_confidence",
        "cycling.recent_ftp_watts",
    ),
    Discipline.SWIMMING: (
        "swimming.typical_weekly_sessions",
        "swimming.typical_weekly_duration_minutes",
        "swimming.longest_continuous_swim_meters",
        "swimming.swimming_environment",
        "swimming.pool_length_meters",
        "swimming.recent_400m_seconds",
    ),
}
_TRIATHLON_FIELDS = (
    "triathlon.prior_experience",
    "triathlon.weakest_discipline",
    "triathlon.open_water_confidence",
)
_DISPLAY_DISCIPLINE_ORDER = (
    Discipline.RUNNING,
    Discipline.CYCLING,
    Discipline.SWIMMING,
)


def fields_for_disciplines(
    disciplines: tuple[Discipline, ...], *, include_triathlon: bool = False
) -> tuple[str, ...]:
    """Return the small form required for the athlete's active goal."""

    selected = set(disciplines)
    fields = tuple(
        key
        for discipline in _DISPLAY_DISCIPLINE_ORDER
        if discipline in selected
        for key in _FIELDS_BY_DISCIPLINE.get(discipline, ())
    )
    return fields + (_TRIATHLON_FIELDS if include_triathlon else ())


def is_optional_field(key: str) -> bool:
    return key in _OPTIONAL_FIELDS


def question_for(key: str) -> BaselineQuestion:
    return _QUESTIONS[key]


def parse_answer(*, key: str, text: str) -> object:
    """Convert one form value to its schema-compatible, validated form."""

    value = text.strip()
    if key in _OPTIONAL_FIELDS and value.lower() in {"", "skip"}:
        return None
    if key.endswith("typical_weekly_sessions"):
        return _integer(value, minimum=0, maximum=14)
    if key in {
        "running.typical_weekly_duration_minutes",
        "running.longest_recent_run_minutes",
        "cycling.typical_weekly_duration_minutes",
        "cycling.longest_recent_ride_minutes",
        "swimming.typical_weekly_duration_minutes",
    }:
        return _integer(value, minimum=0, maximum=24 * 60)
    if key == "swimming.longest_continuous_swim_meters":
        return _integer(value, minimum=0, maximum=100_000)
    if key == "cycling.recent_ftp_watts":
        return _integer(value, minimum=1, maximum=1000)
    if key == "swimming.pool_length_meters":
        parsed = _integer(value, minimum=1, maximum=100)
        if parsed not in {25, 50}:
            raise ValueError("invalid pool length")
        return parsed
    if key == "swimming.recent_400m_seconds":
        return _duration_seconds(value, allow_hours=False, maximum=60 * 60)
    if key == "running.recent_race_result":
        match = _RACE.fullmatch(value)
        if match is None:
            raise ValueError("invalid race result")
        return {
            "distance_km": _decimal(match.group("distance"), minimum=0.1, maximum=250),
            "duration_seconds": _duration_seconds(
                match.group("time"), allow_hours=True, maximum=24 * 60 * 60
            ),
        }
    if key == "cycling.riding_environment":
        return _choice(value, {"INDOOR", "OUTDOOR", "BOTH", "NONE"})
    if key == "cycling.riding_confidence":
        return _choice(
            value,
            {"NEW_RIDER", "SIMPLE_ROUTES", "CONFIDENT", "NOT_CURRENTLY_RIDING"},
        )
    if key == "swimming.swimming_environment":
        return _choice(value, {"POOL", "OPEN_WATER", "BOTH", "NONE"})
    if key == "triathlon.prior_experience":
        return _choice(value, {"NONE", "SPRINT", "OLYMPIC", "LONG_COURSE"})
    if key == "triathlon.weakest_discipline":
        return _choice(
            value,
            {"RUNNING", "CYCLING", "SWIMMING", "NO_CLEAR_WEAKNESS"},
        )
    if key == "triathlon.open_water_confidence":
        return _choice(value, {"NOT_CONFIDENT", "SOME_EXPERIENCE", "CONFIDENT"})
    raise ValueError("unknown baseline field")


def build_baseline(values: dict[str, object]) -> AthleteBaselineData:
    """Build the typed document written to the athlete-owned JSON baseline."""

    return AthleteBaselineData(
        running=_running(values),
        cycling=_cycling(values),
        swimming=_swimming(values),
        triathlon=_triathlon(values),
    )


def _running(values: dict[str, object]) -> RunningBaseline | None:
    if "running.typical_weekly_sessions" not in values:
        return None
    race = values.get("running.recent_race_result")
    return RunningBaseline(
        typical_weekly_sessions=values["running.typical_weekly_sessions"],
        typical_weekly_duration_minutes=values[
            "running.typical_weekly_duration_minutes"
        ],
        longest_recent_run_minutes=values["running.longest_recent_run_minutes"],
        recent_race_result=RecentRaceResult.model_validate(race) if race else None,
    )


def _cycling(values: dict[str, object]) -> CyclingBaseline | None:
    if "cycling.typical_weekly_sessions" not in values:
        return None
    return CyclingBaseline(
        typical_weekly_sessions=values["cycling.typical_weekly_sessions"],
        typical_weekly_duration_minutes=values[
            "cycling.typical_weekly_duration_minutes"
        ],
        longest_recent_ride_minutes=values["cycling.longest_recent_ride_minutes"],
        riding_environment=values["cycling.riding_environment"],
        riding_confidence=values["cycling.riding_confidence"],
        recent_ftp_watts=values.get("cycling.recent_ftp_watts"),
    )


def _swimming(values: dict[str, object]) -> SwimmingBaseline | None:
    if "swimming.typical_weekly_sessions" not in values:
        return None
    return SwimmingBaseline(
        typical_weekly_sessions=values["swimming.typical_weekly_sessions"],
        typical_weekly_duration_minutes=values[
            "swimming.typical_weekly_duration_minutes"
        ],
        longest_continuous_swim_meters=values[
            "swimming.longest_continuous_swim_meters"
        ],
        swimming_environment=values["swimming.swimming_environment"],
        pool_length_meters=values.get("swimming.pool_length_meters"),
        recent_400m_seconds=values.get("swimming.recent_400m_seconds"),
    )


def _triathlon(values: dict[str, object]) -> TriathlonBaseline | None:
    if "triathlon.prior_experience" not in values:
        return None
    return TriathlonBaseline(
        prior_experience=values["triathlon.prior_experience"],
        weakest_discipline=values["triathlon.weakest_discipline"],
        open_water_confidence=values["triathlon.open_water_confidence"],
    )


def _integer(value: str, *, minimum: int, maximum: int) -> int:
    if not value.isdigit():
        raise ValueError("invalid integer")
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise ValueError("integer out of range")
    return parsed


def _decimal(value: str, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError("invalid decimal") from error
    if not minimum <= parsed <= maximum:
        raise ValueError("decimal out of range")
    return parsed


def _duration_seconds(value: str, *, allow_hours: bool, maximum: int) -> int:
    match = _DURATION.fullmatch(value)
    if match is None:
        raise ValueError("invalid duration")
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes"))
    seconds = int(match.group("seconds"))
    if seconds >= 60 or minutes >= 60 or (hours and not allow_hours):
        raise ValueError("invalid duration")
    parsed = hours * 3600 + minutes * 60 + seconds
    if not 0 < parsed <= maximum:
        raise ValueError("duration out of range")
    return parsed


def _choice(value: str, choices: set[str]) -> str:
    normalized = value.strip().upper().replace(" ", "_").replace("-", "_")
    if normalized not in choices:
        raise ValueError("invalid choice")
    return normalized
