"""Central provisional product heuristics for baseline level labels.

These thresholds are intentionally simple and are not scientifically or medically
validated. A label describes the imported training history in the analysis window;
it does not diagnose fitness or prescribe training.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import Discipline, LevelLabel


@dataclass(frozen=True, slots=True)
class LevelThreshold:
    """Minimum observations and weekly duration for a product label."""

    label: LevelLabel
    minimum_sessions: int
    minimum_weekly_duration_seconds: int


def _thresholds(
    *,
    developing_minutes: int,
    intermediate_minutes: int,
    advanced_minutes: int,
) -> tuple[LevelThreshold, ...]:
    return (
        LevelThreshold(LevelLabel.BEGINNER, 1, 0),
        LevelThreshold(
            LevelLabel.DEVELOPING,
            4,
            developing_minutes * 60,
        ),
        LevelThreshold(
            LevelLabel.INTERMEDIATE,
            8,
            intermediate_minutes * 60,
        ),
        LevelThreshold(
            LevelLabel.ADVANCED,
            16,
            advanced_minutes * 60,
        ),
    )


PROVISIONAL_LEVEL_THRESHOLDS: dict[
    Discipline,
    tuple[LevelThreshold, ...],
] = {
    Discipline.RUNNING: _thresholds(
        developing_minutes=90,
        intermediate_minutes=180,
        advanced_minutes=300,
    ),
    Discipline.CYCLING: _thresholds(
        developing_minutes=120,
        intermediate_minutes=240,
        advanced_minutes=420,
    ),
    Discipline.SWIMMING: _thresholds(
        developing_minutes=60,
        intermediate_minutes=120,
        advanced_minutes=210,
    ),
    Discipline.STRENGTH: _thresholds(
        developing_minutes=60,
        intermediate_minutes=120,
        advanced_minutes=180,
    ),
    Discipline.HIKING: _thresholds(
        developing_minutes=90,
        intermediate_minutes=180,
        advanced_minutes=300,
    ),
    Discipline.OTHER: _thresholds(
        developing_minutes=60,
        intermediate_minutes=150,
        advanced_minutes=240,
    ),
}

DISTANCE_MEANINGFUL_DISCIPLINES = frozenset(
    {
        Discipline.RUNNING,
        Discipline.CYCLING,
        Discipline.SWIMMING,
        Discipline.HIKING,
    }
)

HEURISTIC_VERSION = "baseline-v1-provisional"
