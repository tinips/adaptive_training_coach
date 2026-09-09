"""Coverage for the range-is-the-margin and duration-derivation schema rules.

See docs/decisions/locked.md, "Prescribed-range tolerance model" and
"Duration derivation (continuous and structured sessions)".
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.weekly_plans import PlanSession, PlanSessionPrescription


def _pace_intensity(
    target_range: tuple[float, float] = (300.0, 330.0),
) -> dict[str, object]:
    return {
        "metric": "PACE_SECONDS_PER_KM",
        "target_range": list(target_range),
        "rpe_range": [3, 4],
        "guidance": "Steady, controlled effort.",
    }


def _swim_pace_intensity(
    target_range: tuple[float, float] = (100.0, 110.0),
) -> dict[str, object]:
    return {
        "metric": "SWIM_PACE_SECONDS_PER_100M",
        "target_range": list(target_range),
        "rpe_range": [3, 4],
        "guidance": "Steady, controlled effort.",
    }


def _power_intensity(
    target_range: tuple[float, float] = (200.0, 220.0),
) -> dict[str, object]:
    return {
        "metric": "POWER_WATTS",
        "target_range": list(target_range),
        "rpe_range": [5, 6],
        "guidance": "Sweet-spot effort.",
    }


def _rpe_intensity() -> dict[str, object]:
    return {
        "metric": "RPE",
        "target_range": [2, 3],
        "rpe_range": [2, 3],
        "guidance": "Easy, conversational effort.",
    }


def _running_session(
    *,
    targets: dict[str, object],
    intensity: dict[str, object] | None = None,
    prescribed: bool = False,
) -> PlanSession | PlanSessionPrescription:
    model = PlanSessionPrescription if prescribed else PlanSession
    return model(
        discipline="RUNNING",
        purpose="Build a consistent aerobic habit.",
        intensity=intensity or _pace_intensity(),
        objective="Cover the distance at the prescribed pace.",
        targets=targets,
        execution="Keep the effort controlled throughout.",
    )


def test_running_session_with_a_pace_target_requires_a_distance_range() -> None:
    with pytest.raises(ValidationError, match="distance_range_meters"):
        _running_session(targets={})


def test_running_session_derives_duration_from_average_pace_and_distance() -> None:
    session = _running_session(
        targets={"distance_range_meters": (8000.0, 9000.0)},
        intensity=_pace_intensity((300.0, 330.0)),
    )
    # avg distance 8500 m, avg pace 315 s/km -> 8.5 * 315 = 2677.5 s = 44.6 min
    assert session.targets.duration_minutes == 45


def test_running_session_keeps_an_explicitly_supplied_duration() -> None:
    session = _running_session(
        targets={
            "distance_range_meters": (8000.0, 9000.0),
            "duration_minutes": 50,
        },
    )
    assert session.targets.duration_minutes == 50


def test_swimming_session_derives_duration_from_100m_pace_and_distance() -> None:
    session = PlanSession(
        discipline="SWIMMING",
        purpose="Build steady swim endurance.",
        intensity=_swim_pace_intensity((100.0, 110.0)),
        objective="Cover the distance at the prescribed pace.",
        targets={"distance_range_meters": (1400.0, 1600.0)},
        execution="Hold a controlled, repeatable pace.",
    )
    # avg distance 1500 m = 15 units of 100m, avg pace 105 s/100m
    # -> 15 * 105 = 1575 s = 26.25 min -> rounds to 26
    assert session.targets.duration_minutes == 26


def test_too_narrow_distance_range_is_widened_to_the_floor_not_rejected() -> None:
    session = _running_session(
        targets={"distance_range_meters": (8000.0, 8050.0)},  # 50 m wide
    )
    lower, upper = session.targets.distance_range_meters
    assert upper - lower == pytest.approx(250.0)  # running floor
    assert (lower + upper) / 2 == pytest.approx(8025.0)  # centered on the original


def test_too_narrow_pace_range_is_widened_to_the_floor_not_rejected() -> None:
    session = _running_session(
        targets={"distance_range_meters": (8000.0, 9000.0)},
        intensity=_pace_intensity((310.0, 312.0)),  # 2 s/km wide
    )
    lower, upper = session.intensity.target_range
    assert upper - lower == pytest.approx(10.0)  # running pace floor


def test_wide_enough_ranges_are_left_untouched() -> None:
    session = _running_session(
        targets={"distance_range_meters": (8000.0, 9000.0)},
        intensity=_pace_intensity((300.0, 330.0)),
    )
    assert session.targets.distance_range_meters == (8000.0, 9000.0)
    assert session.intensity.target_range == (300.0, 330.0)


def test_rpe_fallback_running_session_does_not_require_a_distance_range() -> None:
    # Already-locked exception: no pace data means duration stays required
    # and independently prescribed, same as before.
    session = _running_session(
        targets={"duration_minutes": 30},
        intensity=_rpe_intensity(),
    )
    assert session.targets.duration_minutes == 30
    assert session.targets.distance_range_meters is None


def test_rpe_fallback_running_session_without_duration_still_fails() -> None:
    with pytest.raises(ValidationError, match="duration_minutes"):
        _running_session(targets={}, intensity=_rpe_intensity())


def test_cycling_session_keeps_duration_required_and_distance_optional() -> None:
    with pytest.raises(ValidationError, match="duration_minutes"):
        PlanSession(
            discipline="CYCLING",
            purpose="Build steady aerobic power.",
            intensity=_power_intensity(),
            objective="Hold the prescribed power.",
            targets={},
            execution="Keep cadence smooth throughout.",
        )


def test_cycling_session_is_not_required_to_have_a_distance_range() -> None:
    session = PlanSession(
        discipline="CYCLING",
        purpose="Build steady aerobic power.",
        intensity=_power_intensity(),
        objective="Hold the prescribed power.",
        targets={"duration_minutes": 60},
        execution="Keep cadence smooth throughout.",
    )
    assert session.targets.duration_minutes == 60
    assert session.targets.distance_range_meters is None


def test_cycling_session_with_a_distance_range_still_gets_it_widened() -> None:
    session = PlanSession(
        discipline="CYCLING",
        purpose="Build steady aerobic power.",
        intensity=_power_intensity(),
        objective="Hold the prescribed power.",
        targets={
            "duration_minutes": 60,
            "distance_range_meters": (30000.0, 30500.0),  # 500 m wide
        },
        execution="Keep cadence smooth throughout.",
    )
    lower, upper = session.targets.distance_range_meters
    assert upper - lower == pytest.approx(1000.0)  # cycling floor


def test_strength_session_is_unaffected_by_the_endurance_rules() -> None:
    session = PlanSession(
        discipline="STRENGTH",
        purpose="Support endurance training with general strength.",
        intensity=_rpe_intensity(),
        objective="Move well and finish with reserve.",
        targets={"duration_minutes": 30},
        execution="Full-body, controlled tempo, no maxing out.",
    )
    assert session.targets.duration_minutes == 30


def test_model_facing_prescription_uses_the_same_derivation_rules() -> None:
    session = _running_session(
        targets={"distance_range_meters": (8000.0, 9000.0)},
        intensity=_pace_intensity((300.0, 330.0)),
        prescribed=True,
    )
    assert isinstance(session, PlanSessionPrescription)
    assert session.targets.duration_minutes == 45
