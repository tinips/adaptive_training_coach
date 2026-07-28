"""Focused invariants for the pure deterministic onboarding flow."""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.enums import OnboardingStep
from app.services.onboarding.state_machine import (
    InvalidOnboardingAnswer,
    OnboardingStateMachine,
    answer_key,
)


def test_conditional_flow_skips_irrelevant_event_access_and_health_steps() -> None:
    machine = OnboardingStateMachine()
    answers: dict[str, object] = {}

    transition = machine.advance(
        current_step=OnboardingStep.CONSENT,
        answers=answers,
        value="continue",
    )
    assert transition.current_step is OnboardingStep.PRIMARY_SPORT
    assert answers == {}

    transition = machine.advance(
        current_step=transition.current_step,
        answers=transition.answers,
        value="running",
    )
    transition = machine.advance(
        current_step=transition.current_step,
        answers=transition.answers,
        value="TEN_K",
    )
    transition = machine.advance(
        current_step=transition.current_step,
        answers=transition.answers,
        value="not_yet",
    )
    assert transition.current_step is OnboardingStep.GOAL_PRIORITY

    equipment_answers = {
        **transition.answers,
        answer_key(OnboardingStep.PRIMARY_SPORT): "RUNNING",
    }
    assert (
        machine.next_step(OnboardingStep.EQUIPMENT, equipment_answers)
        is OnboardingStep.HEALTH_AREAS
    )

    no_health = {
        **equipment_answers,
        answer_key(OnboardingStep.HEALTH_AREAS): ["NONE"],
    }
    assert (
        machine.next_step(OnboardingStep.HEALTH_AREAS, no_health)
        is OnboardingStep.COACH_TONE
    )


@pytest.mark.parametrize(
    ("sport", "after_equipment", "after_pool"),
    [
        ("SWIMMING", OnboardingStep.POOL_ACCESS, OnboardingStep.HEALTH_AREAS),
        ("CYCLING", OnboardingStep.BIKE_ACCESS, None),
        ("TRIATHLON", OnboardingStep.POOL_ACCESS, OnboardingStep.BIKE_ACCESS),
    ],
)
def test_sport_controls_pool_and_bike_access(
    sport: str,
    after_equipment: OnboardingStep,
    after_pool: OnboardingStep | None,
) -> None:
    answers = {answer_key(OnboardingStep.PRIMARY_SPORT): sport}
    assert (
        OnboardingStateMachine.next_step(OnboardingStep.EQUIPMENT, answers)
        is after_equipment
    )
    if after_pool is not None:
        assert (
            OnboardingStateMachine.next_step(
                OnboardingStep.POOL_ACCESS,
                answers,
            )
            is after_pool
        )


def test_date_parser_supports_only_documented_formats_and_rejects_past() -> None:
    today = date(2026, 7, 28)

    assert (
        OnboardingStateMachine.validate(
            OnboardingStep.EVENT_DATE,
            "31/12/2026",
            today=today,
        )
        == "2026-12-31"
    )
    assert (
        OnboardingStateMachine.validate(
            OnboardingStep.EVENT_DATE,
            "2026-12-31",
            today=today,
        )
        == "2026-12-31"
    )
    with pytest.raises(InvalidOnboardingAnswer, match="event_date_in_past"):
        OnboardingStateMachine.validate(
            OnboardingStep.EVENT_DATE,
            "2026-07-27",
            today=today,
        )
    with pytest.raises(InvalidOnboardingAnswer, match="invalid_date_format"):
        OnboardingStateMachine.validate(
            OnboardingStep.EVENT_DATE,
            "12-31-2026",
            today=today,
        )


@pytest.mark.parametrize(
    ("step", "value", "expected"),
    [
        (OnboardingStep.AGE, "42", 42),
        (OnboardingStep.HEIGHT, "181.5", 181.5),
        (OnboardingStep.HEIGHT, "skip", None),
        (OnboardingStep.WEIGHT, 72, 72.0),
        (OnboardingStep.WEIGHT, None, None),
    ],
)
def test_numeric_validation_normalizes_supported_values(
    step: OnboardingStep,
    value: object,
    expected: object,
) -> None:
    assert OnboardingStateMachine.validate(step, value) == expected


@pytest.mark.parametrize(
    ("step", "value", "error_code"),
    [
        (OnboardingStep.AGE, None, "number_required"),
        (OnboardingStep.AGE, 15, "number_out_of_range"),
        (OnboardingStep.AGE, 42.5, "integer_required"),
        (OnboardingStep.HEIGHT, 231, "number_out_of_range"),
        (OnboardingStep.WEIGHT, "heavy", "invalid_number"),
    ],
)
def test_numeric_validation_rejects_invalid_values(
    step: OnboardingStep,
    value: object,
    error_code: str,
) -> None:
    with pytest.raises(InvalidOnboardingAnswer, match=error_code):
        OnboardingStateMachine.validate(step, value)


def test_training_day_toggle_is_idempotent_and_requires_one_on_continue() -> None:
    monday = OnboardingStateMachine.toggle_multiselect(
        step=OnboardingStep.TRAINING_DAYS,
        current_values=[],
        option="monday",
    )
    assert monday.values == ["MONDAY"]
    assert monday.changed is True

    removed = OnboardingStateMachine.toggle_multiselect(
        step=OnboardingStep.TRAINING_DAYS,
        current_values=monday.values,
        option="MONDAY",
    )
    assert removed.values == []
    with pytest.raises(InvalidOnboardingAnswer, match="selection_required"):
        OnboardingStateMachine.validate(
            OnboardingStep.TRAINING_DAYS,
            removed.values,
        )


def test_exclusive_multiselect_options_clear_conflicting_values() -> None:
    health = OnboardingStateMachine.toggle_multiselect(
        step=OnboardingStep.HEALTH_AREAS,
        current_values=["KNEE"],
        option="NONE",
    )
    assert health.values == ["NONE"]

    knee = OnboardingStateMachine.toggle_multiselect(
        step=OnboardingStep.HEALTH_AREAS,
        current_values=health.values,
        option="KNEE",
    )
    assert knee.values == ["KNEE"]

    irregular = OnboardingStateMachine.toggle_multiselect(
        step=OnboardingStep.POOL_ACCESS,
        current_values=["MONDAY", "WEDNESDAY"],
        option="IRREGULAR",
    )
    assert irregular.values == ["IRREGULAR"]
    assert OnboardingStateMachine.validate(
        OnboardingStep.POOL_ACCESS,
        irregular.values,
    ) == {"type": "IRREGULAR", "days": []}


def test_goal_callback_is_validated_against_confirmed_sport() -> None:
    answers = {"primary_sport": "RUNNING"}
    with pytest.raises(
        InvalidOnboardingAnswer,
        match="goal_not_available_for_sport",
    ):
        OnboardingStateMachine.advance(
            current_step=OnboardingStep.GOAL_TYPE,
            answers=answers,
            value="IRONMAN",
        )


def test_goal_summary_edit_removes_stale_event_and_preserves_other_sections() -> None:
    original: dict[str, object] = {
        "primary_sport": "RUNNING",
        "goal_type": "MARATHON",
        "goal_type_other_description": "Ultra adventure",
        "event_status": True,
        "event_name": "Valencia Marathon",
        "event_date": "2026-12-06",
        "goal_priority": "FINISH_SAFELY",
        "age": 38,
        "training_days": ["TUESDAY", "SATURDAY"],
        "coach_tone": "CONCISE_PRACTICAL",
        "baseline_source": "STRAVA",
    }
    edit = OnboardingStateMachine.begin_summary_edit(
        section="goal",
        answers=original,
    )
    assert edit.current_step is OnboardingStep.GOAL_TYPE
    assert edit.return_to_summary is True
    assert edit.answers == original

    goal = OnboardingStateMachine.advance(
        current_step=edit.current_step,
        answers=edit.answers,
        value="TEN_K",
        return_to_summary=edit.return_to_summary,
    )
    event = OnboardingStateMachine.advance(
        current_step=goal.current_step,
        answers=goal.answers,
        value="NOT_YET",
        return_to_summary=goal.return_to_summary,
    )
    assert event.current_step is OnboardingStep.GOAL_PRIORITY
    assert "event_name" not in event.answers
    assert "event_date" not in event.answers
    assert "goal_type_other_description" not in event.answers

    complete = OnboardingStateMachine.advance(
        current_step=event.current_step,
        answers=event.answers,
        value="PERSONAL_BEST",
        return_to_summary=event.return_to_summary,
    )
    assert complete.current_step is OnboardingStep.SUMMARY
    assert complete.return_to_summary is False
    assert complete.answers["age"] == 38
    assert complete.answers["training_days"] == ["TUESDAY", "SATURDAY"]
    assert complete.answers["coach_tone"] == "CONCISE_PRACTICAL"
    assert complete.answers["baseline_source"] == "STRAVA"
    assert original["goal_type"] == "MARATHON"


def test_limitations_edit_with_none_returns_to_summary_and_clears_details() -> None:
    original: dict[str, object] = {
        "health_areas": ["KNEE"],
        "health_timing": "CURRENT",
        "health_description": "Limited flexion",
        "coach_tone": "SUPPORTIVE_MOTIVATIONAL",
    }
    edit = OnboardingStateMachine.begin_summary_edit(
        section="limitations",
        answers=original,
    )
    complete = OnboardingStateMachine.advance(
        current_step=edit.current_step,
        answers=edit.answers,
        value=["NONE"],
        return_to_summary=True,
    )
    assert complete.current_step is OnboardingStep.SUMMARY
    assert complete.answers["health_areas"] == ["NONE"]
    assert "health_timing" not in complete.answers
    assert "health_description" not in complete.answers
    assert complete.answers["coach_tone"] == "SUPPORTIVE_MOTIVATIONAL"


def test_other_callback_only_signals_free_text_and_never_calls_a_graph() -> None:
    class GraphSpy:
        calls = 0

        async def ainvoke(self, _: object) -> None:
            self.calls += 1

    spy = GraphSpy()
    assert OnboardingStateMachine.requires_free_text(
        OnboardingStep.PRIMARY_SPORT,
        "other",
    )
    assert not OnboardingStateMachine.requires_free_text(
        OnboardingStep.CONSENT,
        "other",
    )
    assert not OnboardingStateMachine.requires_free_text(
        OnboardingStep.AGE,
        "write_answer",
    )
    assert spy.calls == 0
