"""Explicit deterministic onboarding state machine.

This module intentionally has no LangChain or LangGraph imports. Telegram
callbacks, supported dates, numbers, and multi-select options are validated
here; explicit free-text is interpreted elsewhere and only re-enters this
state machine after user confirmation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from enum import StrEnum
from typing import Final, cast

from pydantic import JsonValue

from app.domain.enums import (
    BaselineSource,
    CoachTone,
    DayOfWeek,
    DetailLevel,
    GoalPriority,
    OnboardingStep,
    PrimarySport,
)
from app.schemas.onboarding import (
    MultiselectUpdate,
    OnboardingTransition,
    SummaryEditSection,
)

GOAL_TYPES: Final[tuple[str, ...]] = (
    "FIVE_K",
    "TEN_K",
    "HALF_MARATHON",
    "MARATHON",
    "TRAIL",
    "CYCLING_EVENT",
    "GRAN_FONDO",
    "SPRINT_TRIATHLON",
    "OLYMPIC_TRIATHLON",
    "HALF_IRONMAN_70_3",
    "IRONMAN",
    "FIRST_TRIATHLON",
    "IMPROVE_TECHNIQUE",
    "OPEN_WATER_SWIMMING",
    "SPECIFIC_EVENT",
    "GENERAL_HEALTH",
    "IMPROVE_ENDURANCE",
    "IMPROVE_PERFORMANCE",
    "LOSE_BODY_FAT",
    "BUILD_STRENGTH",
    "OTHER",
)

_GOAL_TYPES_BY_SPORT: Final[dict[str, frozenset[str]]] = {
    PrimarySport.RUNNING.value: frozenset(
        {
            "FIVE_K",
            "TEN_K",
            "HALF_MARATHON",
            "MARATHON",
            "TRAIL",
            "IMPROVE_PERFORMANCE",
            "OTHER",
        }
    ),
    PrimarySport.CYCLING.value: frozenset(
        {
            "CYCLING_EVENT",
            "GRAN_FONDO",
            "IMPROVE_ENDURANCE",
            "IMPROVE_PERFORMANCE",
            "OTHER",
        }
    ),
    PrimarySport.TRIATHLON.value: frozenset(
        {
            "SPRINT_TRIATHLON",
            "OLYMPIC_TRIATHLON",
            "HALF_IRONMAN_70_3",
            "IRONMAN",
            "FIRST_TRIATHLON",
            "OTHER",
        }
    ),
    PrimarySport.SWIMMING.value: frozenset(
        {
            "IMPROVE_TECHNIQUE",
            "OPEN_WATER_SWIMMING",
            "SPECIFIC_EVENT",
            "IMPROVE_ENDURANCE",
            "OTHER",
        }
    ),
    PrimarySport.GENERAL_FITNESS.value: frozenset(
        {
            "GENERAL_HEALTH",
            "IMPROVE_ENDURANCE",
            "LOSE_BODY_FAT",
            "BUILD_STRENGTH",
            "OTHER",
        }
    ),
    PrimarySport.OTHER.value: frozenset(
        {
            "GENERAL_HEALTH",
            "IMPROVE_ENDURANCE",
            "IMPROVE_PERFORMANCE",
            "BUILD_STRENGTH",
            "OTHER",
        }
    ),
}

EQUIPMENT_TYPES: Final[tuple[str, ...]] = (
    "RUNNING_SHOES",
    "ROAD_BIKE",
    "MOUNTAIN_BIKE",
    "INDOOR_BIKE_TRAINER",
    "SWIMMING_POOL",
    "GYM",
    "RESISTANCE_BANDS",
    "SPORTS_WATCH",
    "HEART_RATE_CHEST_STRAP",
    "OTHER",
)

HEALTH_AREAS: Final[tuple[str, ...]] = (
    "NONE",
    "SHOULDER",
    "BACK",
    "HIP",
    "KNEE",
    "ANKLE_FOOT",
    "OTHER",
)

ACCESS_TYPES: Final[tuple[str, ...]] = (
    "REGULAR",
    "IRREGULAR",
    "NO_REGULAR_ACCESS",
)

WEEKDAY_DURATIONS: Final[tuple[int | str, ...]] = (
    30,
    45,
    60,
    90,
    "OVER_90",
    "VARIABLE",
)

WEEKEND_DURATIONS: Final[tuple[int | str, ...]] = (
    60,
    90,
    120,
    180,
    "OVER_180",
    "VARIABLE",
)

_DAY_VALUES: Final[tuple[str, ...]] = tuple(day.value for day in DayOfWeek)
_HEALTH_TIMINGS: Final[tuple[str, ...]] = ("CURRENT", "HISTORICAL", "BOTH")
_EDIT_STARTS: Final[dict[SummaryEditSection, OnboardingStep]] = {
    "goal": OnboardingStep.GOAL_TYPE,
    "availability": OnboardingStep.TRAINING_DAYS,
    "equipment": OnboardingStep.EQUIPMENT,
    "limitations": OnboardingStep.HEALTH_AREAS,
    "coach_style": OnboardingStep.COACH_TONE,
    "baseline": OnboardingStep.BASELINE_SOURCE,
}
_FREE_TEXT_STEPS: Final[frozenset[OnboardingStep]] = frozenset(
    {
        OnboardingStep.PRIMARY_SPORT,
        OnboardingStep.GOAL_TYPE,
        OnboardingStep.GOAL_PRIORITY,
        OnboardingStep.EQUIPMENT,
        OnboardingStep.HEALTH_AREAS,
        OnboardingStep.HEALTH_DESCRIPTION,
    }
)


class OnboardingStateMachineError(ValueError):
    """Base exception carrying a stable, non-user-facing error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class InvalidOnboardingAnswer(OnboardingStateMachineError):
    """Raised when a deterministic answer violates the step contract."""


def answer_key(step: OnboardingStep) -> str:
    """Return the stable JSONB key for a step."""

    return step.value.lower()


def _canonical_token(value: object) -> str:
    if isinstance(value, StrEnum):
        return value.value
    if not isinstance(value, str):
        raise InvalidOnboardingAnswer("invalid_option")
    return value.strip().upper().replace("-", "_").replace(" ", "_")


def _enum_value(value: object, allowed: Iterable[str]) -> str:
    normalized = _canonical_token(value)
    if normalized not in allowed:
        raise InvalidOnboardingAnswer("invalid_option")
    return normalized


def _optional_number(
    value: object,
    *,
    minimum: float,
    maximum: float,
    integer: bool,
) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().upper() in {"SKIP", "NONE", ""}:
        return None
    if isinstance(value, bool):
        raise InvalidOnboardingAnswer("invalid_number")
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise InvalidOnboardingAnswer("invalid_number") from exc
    if not numeric.is_integer() and integer:
        raise InvalidOnboardingAnswer("integer_required")
    if not minimum <= numeric <= maximum:
        raise InvalidOnboardingAnswer("number_out_of_range")
    if integer:
        return int(numeric)
    return numeric


def _event_date(value: object, *, today: date) -> str:
    if isinstance(value, date):
        parsed = value
    elif isinstance(value, str):
        stripped = value.strip()
        parsed = _parse_supported_date(stripped)
    else:
        raise InvalidOnboardingAnswer("invalid_date_format")
    if parsed < today:
        raise InvalidOnboardingAnswer("event_date_in_past")
    return parsed.isoformat()


def _parse_supported_date(value: str) -> date:
    try:
        if len(value) == 10 and value[4] == "-" and value[7] == "-":
            return date.fromisoformat(value)
        if len(value) == 10 and value[2] == "/" and value[5] == "/":
            day_text, month_text, year_text = value.split("/")
            return date(int(year_text), int(month_text), int(day_text))
    except ValueError as exc:
        raise InvalidOnboardingAnswer("invalid_date") from exc
    raise InvalidOnboardingAnswer("invalid_date_format")


def _bool_answer(
    value: object,
    *,
    true_values: set[str],
    false_values: set[str],
) -> bool:
    if isinstance(value, bool):
        return value
    token = _canonical_token(value)
    if token in true_values:
        return True
    if token in false_values:
        return False
    raise InvalidOnboardingAnswer("invalid_boolean")


def _ordered_unique(values: Iterable[str], order: tuple[str, ...]) -> list[str]:
    selected = set(values)
    return [candidate for candidate in order if candidate in selected]


def _multiselect(
    value: object,
    *,
    allowed: tuple[str, ...],
    require_one: bool,
    none_is_exclusive: bool = False,
) -> list[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise InvalidOnboardingAnswer("invalid_multiselect")
    normalized = [_enum_value(item, allowed) for item in value]
    result = _ordered_unique(normalized, allowed)
    if require_one and not result:
        raise InvalidOnboardingAnswer("selection_required")
    if none_is_exclusive and "NONE" in result and len(result) > 1:
        raise InvalidOnboardingAnswer("none_must_be_exclusive")
    return result


def _duration(value: object, allowed: tuple[int | str, ...]) -> int | str:
    if isinstance(value, bool):
        raise InvalidOnboardingAnswer("invalid_duration")
    candidate: int | str
    if isinstance(value, int):
        candidate = value
    elif isinstance(value, str):
        stripped = value.strip().upper().replace(" ", "_")
        if stripped.isdigit():
            candidate = int(stripped)
        else:
            candidate = stripped
    else:
        raise InvalidOnboardingAnswer("invalid_duration")
    if candidate not in allowed:
        raise InvalidOnboardingAnswer("invalid_duration")
    return candidate


def _access(value: object) -> dict[str, JsonValue]:
    if isinstance(value, str):
        access_type = _enum_value(value, ACCESS_TYPES)
        if access_type == "REGULAR":
            raise InvalidOnboardingAnswer("access_days_required")
        return {"type": access_type, "days": []}
    if isinstance(value, (list, tuple, set, frozenset)):
        options = _multiselect(
            value,
            allowed=(*_DAY_VALUES, "IRREGULAR", "NO_REGULAR_ACCESS"),
            require_one=True,
        )
        special = {
            option for option in options if option in {"IRREGULAR", "NO_REGULAR_ACCESS"}
        }
        if special:
            if len(options) != 1:
                raise InvalidOnboardingAnswer("access_type_must_be_exclusive")
            return {"type": options[0], "days": []}
        days = options
        return {"type": "REGULAR", "days": cast(JsonValue, days)}
    if not isinstance(value, Mapping):
        raise InvalidOnboardingAnswer("invalid_access")
    access_type = _enum_value(value.get("type"), ACCESS_TYPES)
    raw_days = value.get("days", [])
    if access_type == "REGULAR":
        days = _multiselect(raw_days, allowed=_DAY_VALUES, require_one=True)
    else:
        if raw_days not in (None, [], (), set(), frozenset()):
            raise InvalidOnboardingAnswer("access_days_not_allowed")
        days = []
    return {"type": access_type, "days": cast(JsonValue, days)}


def _text(value: object, *, optional: bool, maximum: int) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise InvalidOnboardingAnswer("text_required")
    stripped = value.strip()
    if optional and stripped.upper() == "SKIP":
        return None
    if not stripped:
        if optional:
            return None
        raise InvalidOnboardingAnswer("text_required")
    if len(stripped) > maximum:
        raise InvalidOnboardingAnswer("text_too_long")
    return stripped


def _primary_sport(answers: Mapping[str, JsonValue]) -> str | None:
    value = answers.get(answer_key(OnboardingStep.PRIMARY_SPORT))
    return value if isinstance(value, str) else None


def _swimming_relevant(answers: Mapping[str, JsonValue]) -> bool:
    return _primary_sport(answers) in {
        PrimarySport.SWIMMING.value,
        PrimarySport.TRIATHLON.value,
    }


def _cycling_relevant(answers: Mapping[str, JsonValue]) -> bool:
    return _primary_sport(answers) in {
        PrimarySport.CYCLING.value,
        PrimarySport.TRIATHLON.value,
    }


def _has_health_constraints(answers: Mapping[str, JsonValue]) -> bool:
    value = answers.get(answer_key(OnboardingStep.HEALTH_AREAS))
    return isinstance(value, list) and bool(value) and value != ["NONE"]


class OnboardingStateMachine:
    """Pure, explicit transition and validation API."""

    @staticmethod
    def requires_free_text(step: OnboardingStep, option: object) -> bool:
        """Tell the application service whether a callback enters a text path.

        This method never invokes a model. The service can deterministically
        mark the session as awaiting text and invoke the compiled graph only
        after a subsequent text message arrives.
        """

        if not isinstance(option, (str, StrEnum)):
            return False
        return step in _FREE_TEXT_STEPS and _canonical_token(option) in {
            "OTHER",
            "WRITE_ANSWER",
        }

    @classmethod
    def advance(
        cls,
        *,
        current_step: OnboardingStep,
        answers: Mapping[str, JsonValue],
        value: object,
        return_to_summary: bool = False,
        today: date | None = None,
    ) -> OnboardingTransition:
        """Validate an answer and return a new state without mutating input."""

        if current_step is OnboardingStep.SUMMARY:
            raise OnboardingStateMachineError("summary_has_no_next_step")
        next_answers = dict(answers)
        normalized = cls.validate(current_step, value, today=today)
        cls._validate_contextual_answer(
            step=current_step,
            normalized=normalized,
            answers=next_answers,
        )
        next_answers[answer_key(current_step)] = normalized
        cls._remove_stale_dependent_answers(current_step, normalized, next_answers)
        next_step = cls.next_step(current_step, next_answers)
        if return_to_summary and cls._edit_section_finished(
            current_step=current_step,
            answers=next_answers,
        ):
            next_step = OnboardingStep.SUMMARY
            return_to_summary = False
        return OnboardingTransition(
            current_step=next_step,
            answers=next_answers,
            return_to_summary=return_to_summary,
        )

    @staticmethod
    def validate(
        step: OnboardingStep,
        value: object,
        *,
        today: date | None = None,
    ) -> JsonValue:
        """Normalize one deterministic answer into a JSON-compatible value."""

        effective_today = today or date.today()
        if step is OnboardingStep.CONSENT:
            accepted = _bool_answer(
                value,
                true_values={"CONTINUE", "ACCEPT", "YES", "TRUE"},
                false_values={"CANCEL", "DECLINE", "NO", "FALSE"},
            )
            if not accepted:
                raise InvalidOnboardingAnswer("consent_not_accepted")
            return True
        if step is OnboardingStep.PRIMARY_SPORT:
            return _enum_value(value, (sport.value for sport in PrimarySport))
        if step is OnboardingStep.GOAL_TYPE:
            return _enum_value(value, GOAL_TYPES)
        if step is OnboardingStep.EVENT_STATUS:
            return _bool_answer(
                value,
                true_values={"YES", "HAS_EVENT", "TRUE"},
                false_values={"NO", "NOT_YET", "FALSE"},
            )
        if step is OnboardingStep.EVENT_NAME:
            return _text(value, optional=False, maximum=120)
        if step is OnboardingStep.EVENT_DATE:
            return _event_date(value, today=effective_today)
        if step is OnboardingStep.GOAL_PRIORITY:
            return _enum_value(value, (priority.value for priority in GoalPriority))
        if step is OnboardingStep.AGE:
            age = _optional_number(
                value,
                minimum=16,
                maximum=100,
                integer=True,
            )
            if age is None:
                raise InvalidOnboardingAnswer("number_required")
            return age
        if step is OnboardingStep.HEIGHT:
            return _optional_number(
                value,
                minimum=120,
                maximum=230,
                integer=False,
            )
        if step is OnboardingStep.WEIGHT:
            return _optional_number(
                value,
                minimum=35,
                maximum=250,
                integer=False,
            )
        if step is OnboardingStep.TRAINING_DAYS:
            return cast(
                JsonValue,
                _multiselect(value, allowed=_DAY_VALUES, require_one=True),
            )
        if step is OnboardingStep.WEEKDAY_DURATION:
            return _duration(value, WEEKDAY_DURATIONS)
        if step is OnboardingStep.WEEKEND_DURATION:
            return _duration(value, WEEKEND_DURATIONS)
        if step is OnboardingStep.EQUIPMENT:
            return cast(
                JsonValue,
                _multiselect(
                    value,
                    allowed=EQUIPMENT_TYPES,
                    require_one=False,
                ),
            )
        if step in {OnboardingStep.POOL_ACCESS, OnboardingStep.BIKE_ACCESS}:
            return _access(value)
        if step is OnboardingStep.HEALTH_AREAS:
            return cast(
                JsonValue,
                _multiselect(
                    value,
                    allowed=HEALTH_AREAS,
                    require_one=True,
                    none_is_exclusive=True,
                ),
            )
        if step is OnboardingStep.HEALTH_TIMING:
            return _enum_value(value, _HEALTH_TIMINGS)
        if step is OnboardingStep.HEALTH_DESCRIPTION:
            return _text(value, optional=True, maximum=500)
        if step is OnboardingStep.COACH_TONE:
            return _enum_value(value, (tone.value for tone in CoachTone))
        if step is OnboardingStep.COACH_DETAIL:
            return _enum_value(value, (detail.value for detail in DetailLevel))
        if step is OnboardingStep.BASELINE_SOURCE:
            return _enum_value(value, (source.value for source in BaselineSource))
        raise OnboardingStateMachineError("unsupported_step")

    @staticmethod
    def next_step(
        current_step: OnboardingStep,
        answers: Mapping[str, JsonValue],
    ) -> OnboardingStep:
        """Return the explicit conditional successor for a confirmed answer."""

        fixed_successors = {
            OnboardingStep.CONSENT: OnboardingStep.PRIMARY_SPORT,
            OnboardingStep.PRIMARY_SPORT: OnboardingStep.GOAL_TYPE,
            OnboardingStep.GOAL_TYPE: OnboardingStep.EVENT_STATUS,
            OnboardingStep.EVENT_NAME: OnboardingStep.EVENT_DATE,
            OnboardingStep.EVENT_DATE: OnboardingStep.GOAL_PRIORITY,
            OnboardingStep.GOAL_PRIORITY: OnboardingStep.AGE,
            OnboardingStep.AGE: OnboardingStep.HEIGHT,
            OnboardingStep.HEIGHT: OnboardingStep.WEIGHT,
            OnboardingStep.WEIGHT: OnboardingStep.TRAINING_DAYS,
            OnboardingStep.TRAINING_DAYS: OnboardingStep.WEEKDAY_DURATION,
            OnboardingStep.WEEKDAY_DURATION: OnboardingStep.WEEKEND_DURATION,
            OnboardingStep.WEEKEND_DURATION: OnboardingStep.EQUIPMENT,
            OnboardingStep.HEALTH_TIMING: OnboardingStep.HEALTH_DESCRIPTION,
            OnboardingStep.HEALTH_DESCRIPTION: OnboardingStep.COACH_TONE,
            OnboardingStep.COACH_TONE: OnboardingStep.COACH_DETAIL,
            OnboardingStep.COACH_DETAIL: OnboardingStep.BASELINE_SOURCE,
        }
        if current_step in fixed_successors:
            return fixed_successors[current_step]
        if current_step is OnboardingStep.EVENT_STATUS:
            has_event = answers.get(answer_key(OnboardingStep.EVENT_STATUS))
            return (
                OnboardingStep.EVENT_NAME
                if has_event is True
                else OnboardingStep.GOAL_PRIORITY
            )
        if current_step is OnboardingStep.EQUIPMENT:
            if _swimming_relevant(answers):
                return OnboardingStep.POOL_ACCESS
            if _cycling_relevant(answers):
                return OnboardingStep.BIKE_ACCESS
            return OnboardingStep.HEALTH_AREAS
        if current_step is OnboardingStep.POOL_ACCESS:
            if _cycling_relevant(answers):
                return OnboardingStep.BIKE_ACCESS
            return OnboardingStep.HEALTH_AREAS
        if current_step is OnboardingStep.BIKE_ACCESS:
            return OnboardingStep.HEALTH_AREAS
        if current_step is OnboardingStep.HEALTH_AREAS:
            if _has_health_constraints(answers):
                return OnboardingStep.HEALTH_TIMING
            return OnboardingStep.COACH_TONE
        if current_step is OnboardingStep.BASELINE_SOURCE:
            source = answers.get(answer_key(OnboardingStep.BASELINE_SOURCE))
            if source == BaselineSource.FILE_IMPORT.value:
                return OnboardingStep.FILE_IMPORT_WAITING
            if source == BaselineSource.APPLE_HEALTH_EXPORT.value:
                return OnboardingStep.APPLE_HEALTH_PRIVACY_NOTICE
            return OnboardingStep.SUMMARY
        raise OnboardingStateMachineError("unsupported_transition")

    @classmethod
    def toggle_multiselect(
        cls,
        *,
        step: OnboardingStep,
        current_values: Iterable[object],
        option: object,
    ) -> MultiselectUpdate:
        """Toggle one explicit callback option with stable ordering."""

        allowed: tuple[str, ...]
        exclusive: set[str] = set()
        if step is OnboardingStep.TRAINING_DAYS:
            allowed = _DAY_VALUES
        elif step is OnboardingStep.EQUIPMENT:
            allowed = EQUIPMENT_TYPES
        elif step is OnboardingStep.HEALTH_AREAS:
            allowed = HEALTH_AREAS
            exclusive = {"NONE"}
        elif step in {OnboardingStep.POOL_ACCESS, OnboardingStep.BIKE_ACCESS}:
            allowed = (*_DAY_VALUES, "IRREGULAR", "NO_REGULAR_ACCESS")
            exclusive = {"IRREGULAR", "NO_REGULAR_ACCESS"}
        else:
            raise OnboardingStateMachineError("step_is_not_multiselect")

        existing = [_enum_value(item, allowed) for item in current_values]
        selected = set(existing)
        normalized_option = _enum_value(option, allowed)
        if normalized_option in selected:
            selected.remove(normalized_option)
        else:
            if normalized_option in exclusive:
                selected.clear()
            else:
                selected.difference_update(exclusive)
                if normalized_option != "NONE":
                    selected.discard("NONE")
            selected.add(normalized_option)
        values = _ordered_unique(selected, allowed)
        return MultiselectUpdate(values=values, changed=set(existing) != set(values))

    @staticmethod
    def begin_summary_edit(
        *,
        section: SummaryEditSection,
        answers: Mapping[str, JsonValue],
    ) -> OnboardingTransition:
        """Start a bounded edit while preserving all staged answers."""

        return OnboardingTransition(
            current_step=_EDIT_STARTS[section],
            answers=dict(answers),
            return_to_summary=True,
        )

    @classmethod
    def back(
        cls,
        *,
        current_step: OnboardingStep,
        answers: Mapping[str, JsonValue],
        return_to_summary: bool = False,
    ) -> OnboardingTransition:
        """Navigate to the preceding currently relevant deterministic step."""

        if current_step is OnboardingStep.CONSENT:
            raise OnboardingStateMachineError("already_at_first_step")
        if current_step is OnboardingStep.APPLE_HEALTH_PRIVACY_NOTICE:
            return OnboardingTransition(
                current_step=OnboardingStep.BASELINE_SOURCE,
                answers=dict(answers),
                return_to_summary=return_to_summary,
            )
        if current_step is OnboardingStep.FILE_IMPORT_WAITING:
            return OnboardingTransition(
                current_step=OnboardingStep.BASELINE_SOURCE,
                answers=dict(answers),
                return_to_summary=return_to_summary,
            )
        if current_step is OnboardingStep.FILE_IMPORT_COMPLETE:
            return OnboardingTransition(
                current_step=OnboardingStep.FILE_IMPORT_WAITING,
                answers=dict(answers),
                return_to_summary=return_to_summary,
            )
        if current_step is OnboardingStep.APPLE_HEALTH_WAITING_FOR_FILE:
            return OnboardingTransition(
                current_step=OnboardingStep.APPLE_HEALTH_PRIVACY_NOTICE,
                answers=dict(answers),
                return_to_summary=return_to_summary,
            )
        if current_step is OnboardingStep.APPLE_HEALTH_IMPORT_FAILED:
            return OnboardingTransition(
                current_step=OnboardingStep.BASELINE_SOURCE,
                answers=dict(answers),
                return_to_summary=return_to_summary,
            )
        if return_to_summary and current_step in _EDIT_STARTS.values():
            return OnboardingTransition(
                current_step=OnboardingStep.SUMMARY,
                answers=dict(answers),
                return_to_summary=False,
            )
        flow = cls.relevant_steps(answers)
        try:
            index = flow.index(current_step)
        except ValueError as exc:
            raise OnboardingStateMachineError("step_not_in_current_flow") from exc
        if index == 0:
            raise OnboardingStateMachineError("already_at_first_step")
        return OnboardingTransition(
            current_step=flow[index - 1],
            answers=dict(answers),
            return_to_summary=return_to_summary,
        )

    @staticmethod
    def relevant_steps(answers: Mapping[str, JsonValue]) -> list[OnboardingStep]:
        """Build the currently relevant flow for resume and back navigation."""

        steps = [
            OnboardingStep.CONSENT,
            OnboardingStep.PRIMARY_SPORT,
            OnboardingStep.GOAL_TYPE,
            OnboardingStep.EVENT_STATUS,
        ]
        if answers.get(answer_key(OnboardingStep.EVENT_STATUS)) is True:
            steps.extend([OnboardingStep.EVENT_NAME, OnboardingStep.EVENT_DATE])
        steps.extend(
            [
                OnboardingStep.GOAL_PRIORITY,
                OnboardingStep.AGE,
                OnboardingStep.HEIGHT,
                OnboardingStep.WEIGHT,
                OnboardingStep.TRAINING_DAYS,
                OnboardingStep.WEEKDAY_DURATION,
                OnboardingStep.WEEKEND_DURATION,
                OnboardingStep.EQUIPMENT,
            ]
        )
        if _swimming_relevant(answers):
            steps.append(OnboardingStep.POOL_ACCESS)
        if _cycling_relevant(answers):
            steps.append(OnboardingStep.BIKE_ACCESS)
        steps.append(OnboardingStep.HEALTH_AREAS)
        if _has_health_constraints(answers):
            steps.extend(
                [
                    OnboardingStep.HEALTH_TIMING,
                    OnboardingStep.HEALTH_DESCRIPTION,
                ]
            )
        steps.extend(
            [
                OnboardingStep.COACH_TONE,
                OnboardingStep.COACH_DETAIL,
                OnboardingStep.BASELINE_SOURCE,
                OnboardingStep.SUMMARY,
            ]
        )
        return steps

    @staticmethod
    def restart() -> OnboardingTransition:
        """Return a fresh onboarding state after confirmed cancellation/restart."""

        return OnboardingTransition(
            current_step=OnboardingStep.CONSENT,
            answers={},
            return_to_summary=False,
        )

    @staticmethod
    def _remove_stale_dependent_answers(
        step: OnboardingStep,
        normalized: JsonValue,
        answers: dict[str, JsonValue],
    ) -> None:
        contains_other = normalized == "OTHER" or (
            isinstance(normalized, list) and "OTHER" in normalized
        )
        if not contains_other:
            answers.pop(f"{answer_key(step)}_other_description", None)
        if step is OnboardingStep.EVENT_STATUS and normalized is False:
            answers.pop(answer_key(OnboardingStep.EVENT_NAME), None)
            answers.pop(answer_key(OnboardingStep.EVENT_DATE), None)
        if step is OnboardingStep.HEALTH_AREAS and normalized == ["NONE"]:
            answers.pop(answer_key(OnboardingStep.HEALTH_TIMING), None)
            answers.pop(answer_key(OnboardingStep.HEALTH_DESCRIPTION), None)

    @staticmethod
    def _validate_contextual_answer(
        *,
        step: OnboardingStep,
        normalized: JsonValue,
        answers: Mapping[str, JsonValue],
    ) -> None:
        if step is not OnboardingStep.GOAL_TYPE or not isinstance(normalized, str):
            return
        sport = _primary_sport(answers)
        if sport is None:
            raise InvalidOnboardingAnswer("primary_sport_required")
        allowed = _GOAL_TYPES_BY_SPORT.get(sport)
        if allowed is None or normalized not in allowed:
            raise InvalidOnboardingAnswer("goal_not_available_for_sport")

    @staticmethod
    def _edit_section_finished(
        *,
        current_step: OnboardingStep,
        answers: Mapping[str, JsonValue],
    ) -> bool:
        if current_step in {
            OnboardingStep.GOAL_PRIORITY,
            OnboardingStep.WEEKEND_DURATION,
            OnboardingStep.HEALTH_DESCRIPTION,
            OnboardingStep.COACH_DETAIL,
            OnboardingStep.BIKE_ACCESS,
        }:
            return True
        if current_step is OnboardingStep.BASELINE_SOURCE:
            return (
                answers.get(answer_key(OnboardingStep.BASELINE_SOURCE))
                != BaselineSource.APPLE_HEALTH_EXPORT.value
            )
        if current_step is OnboardingStep.EQUIPMENT:
            return not _swimming_relevant(answers) and not _cycling_relevant(answers)
        if current_step is OnboardingStep.POOL_ACCESS:
            return not _cycling_relevant(answers)
        if current_step is OnboardingStep.HEALTH_AREAS:
            return not _has_health_constraints(answers)
        return False
