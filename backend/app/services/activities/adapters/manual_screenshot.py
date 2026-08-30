"""Manual (AI-assisted screenshot) payload to canonical activity import mapping.

Companion to ``adapters/healthkit.py``. This path exists because a source
app can keep a more accurate number privately (real training time, an
active/total calorie split, pool lap and stroke detail, true per-workout
heart rate) than it ever exports through HealthKit. See the HealthKit
adapter's ``_heart_rate_summary`` for the matching gap on that path.
"""

from __future__ import annotations

import hashlib

from app.domain.enums import ActivitySource, Discipline, SwimmingEnvironment
from app.schemas.manual_import import ManualWorkoutImportRequest
from app.schemas.workouts import PoolSwimmingDetailsData
from app.services.activities.contracts import ActivityImportData
from app.services.activities.normalization import workout_title


def from_manual_screenshot(payload: ManualWorkoutImportRequest) -> ActivityImportData:
    """Map one AI-extracted screenshot workout to the canonical import shape.

    ``calories_kcal`` prefers active-only calories to match what HealthKit
    sync stores for the same field (active energy burned), so the two
    sources stay comparable; total calories are not currently persisted
    anywhere the app can read them back.
    """

    discipline = Discipline(payload.discipline)
    pool_details = _pool_details(payload)
    return ActivityImportData(
        source=ActivitySource.MANUAL,
        external_id=_external_id(payload),
        discipline=discipline,
        raw_sport=payload.discipline.lower(),
        title=workout_title(discipline, payload.source_app_name),
        started_at=payload.started_at,
        duration_seconds=payload.duration_seconds,
        moving_duration_seconds=payload.duration_seconds,
        distance_meters=payload.distance_meters,
        calories_kcal=payload.calories_active_kcal or payload.calories_total_kcal,
        average_heart_rate=payload.average_heart_rate,
        max_heart_rate=payload.max_heart_rate,
        swimming_environment=(
            SwimmingEnvironment(payload.swimming.environment)
            if payload.swimming is not None
            else None
        ),
        pool_details=pool_details,
        source_metadata={
            "ingestion_channel": "AI_SCREENSHOT_EXTRACTION",
            "source_app_name": payload.source_app_name,
            "calories_active_kcal": payload.calories_active_kcal,
            "calories_total_kcal": payload.calories_total_kcal,
        },
    )


def _pool_details(
    payload: ManualWorkoutImportRequest,
) -> PoolSwimmingDetailsData | None:
    swimming = payload.swimming
    if swimming is None or swimming.environment != "POOL":
        return None

    pool_length_meters = swimming.pool_length_meters
    if (
        pool_length_meters is None
        and swimming.total_lengths
        and payload.distance_meters
    ):
        # Common pool lengths a source app usually knows precisely; derive
        # it rather than guess when the app didn't say so directly.
        pool_length_meters = payload.distance_meters / swimming.total_lengths

    if pool_length_meters is None:
        raise ValueError(
            "A pool swim needs pool_length_meters, or enough data "
            "(distance and total_lengths) to derive it"
        )

    return PoolSwimmingDetailsData(
        pool_length_meters=pool_length_meters,
        total_lengths=swimming.total_lengths,
        primary_stroke=swimming.primary_stroke,
        total_strokes=swimming.total_strokes,
    )


def _external_id(payload: ManualWorkoutImportRequest) -> str:
    """A stable identity so re-submitting the same screenshot is a no-op.

    Unlike HealthKit, a screenshot carries no provider UUID. Hashing the
    fields a duplicate submission would reproduce exactly (sport, start
    time, duration, distance) keeps re-imports idempotent without one.
    """

    fingerprint = "|".join(
        [
            payload.discipline,
            payload.started_at.isoformat(),
            str(payload.duration_seconds),
            str(payload.distance_meters),
        ]
    )
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:32]
    return f"manual-screenshot:{digest}"


__all__ = ["from_manual_screenshot"]
