"""Age-based reference HR zones and display-zone composition, display-only."""

from __future__ import annotations

from app.schemas.baseline import (
    AthleteBaselineData,
    CyclingBaseline,
    RecentRaceResult,
    RunningBaseline,
)
from app.services.athlete_zones import (
    estimate_max_hr_bpm,
    resolve_athlete_display_zones,
    resolve_reference_hr_zones,
)


def test_estimate_max_hr_uses_tanaka_formula() -> None:
    # 208 - 0.7 * (2026 - 1990) = 208 - 0.7 * 36 = 182.8
    assert estimate_max_hr_bpm(birth_year=1990, current_year=2026) == 182.8


def test_reference_hr_zones_carry_an_approximation_caveat() -> None:
    zones = resolve_reference_hr_zones(birth_year=1990, current_year=2026)

    assert zones.estimated_max_hr_bpm == 182.8
    assert zones.easy == (round(182.8 * 0.60), round(182.8 * 0.75))
    assert zones.moderate == (round(182.8 * 0.76), round(182.8 * 0.85))
    assert zones.hard == (round(182.8 * 0.86), round(182.8 * 0.92))
    assert "approximate" in zones.caveat
    assert "±10-12 bpm" in zones.caveat or "10-12 bpm" in zones.caveat


def test_display_zones_compose_hr_pace_and_power_from_baseline() -> None:
    baseline = AthleteBaselineData(
        running=RunningBaseline(
            typical_weekly_sessions=4,
            typical_weekly_duration_minutes=200,
            longest_recent_run_minutes=90,
            recent_race_result=RecentRaceResult(distance_km=10, duration_seconds=2520),
        ),
        cycling=CyclingBaseline(
            typical_weekly_sessions=3,
            typical_weekly_duration_minutes=240,
            longest_recent_ride_minutes=120,
            riding_environment="INDOOR",
            riding_confidence="CONFIDENT",
            recent_ftp_watts=260,
        ),
    )

    zones = resolve_athlete_display_zones(
        birth_year=1990, baseline=baseline, current_year=2026
    )

    assert zones.heart_rate is not None
    assert zones.heart_rate.estimated_max_hr_bpm == 182.8
    assert zones.running is not None
    assert zones.running.metric == "PACE_SECONDS_PER_KM"
    assert zones.cycling is not None
    assert zones.cycling.metric == "POWER_WATTS"
    assert zones.swimming is None  # no swim baseline supplied


def test_display_zones_handle_missing_birth_year_and_baseline() -> None:
    zones = resolve_athlete_display_zones(
        birth_year=None, baseline=None, current_year=2026
    )

    assert zones.heart_rate is None
    assert zones.running is None
    assert zones.cycling is None
    assert zones.swimming is None
