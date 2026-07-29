"""Owner-scoped canonical activity matching, provenance, and enrichment."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Activity,
    ActivitySourceLink,
    AppleHealthImportJob,
    HeartRateObservation,
)
from app.domain.enums import (
    ActivitySource,
    Discipline,
    HeartRateSource,
    HeartRateTemporalQuality,
)
from app.integrations.apple_health.models import ParsedWorkout
from app.repositories.errors import OwnedRecordNotFoundError

if TYPE_CHECKING:
    from app.integrations.tcx.models import ParsedTCXActivity, ParsedTCXPosition

ActivityUpsertOutcome = Literal["inserted", "updated", "unchanged"]
ActivityMatchKind = Literal["source_key", "cross_source", "new", "ambiguous"]


@dataclass(slots=True, frozen=True)
class ActivityMatchThresholds:
    """Centralized conservative thresholds for automatic cross-source matching."""

    start_tolerance_seconds: int = 5 * 60
    duration_absolute_tolerance_seconds: int = 5 * 60
    duration_relative_tolerance: float = 0.10
    distance_absolute_tolerance_meters: float = 500
    distance_relative_tolerance: float = 0.10


DEFAULT_MATCH_THRESHOLDS = ActivityMatchThresholds()


@dataclass(slots=True, frozen=True)
class ActivityImportData:
    """Persistence-neutral normalized fields for one imported activity."""

    source: ActivitySource
    external_id: str
    sport: Discipline
    source_sport_type: str
    name: str
    started_at: datetime
    duration_seconds: int
    ended_at: datetime | None = None
    timezone: str | None = None
    moving_time_seconds: int | None = None
    distance_meters: float | None = None
    elevation_gain_meters: float | None = None
    calories_kcal: float | None = None
    average_heart_rate: float | None = None
    average_heart_rate_source: HeartRateSource = HeartRateSource.UNAVAILABLE
    max_heart_rate: float | None = None
    heart_rate_sample_count: int = 0
    heart_rate_quality: HeartRateTemporalQuality = HeartRateTemporalQuality.UNKNOWN
    heart_rate_reliable: bool = False
    average_cadence: float | None = None
    route_points: tuple[dict[str, object], ...] = ()


class ActivitySourceConflictError(ValueError):
    """A provider key is already linked to another owned canonical activity."""


class ActivityImportValidationError(ValueError):
    """A parsed record cannot satisfy the canonical activity boundary."""


def metric_quality_rank(
    source: HeartRateSource,
    *,
    reliable: bool = True,
) -> int:
    """Return the documented deterministic metric precedence."""

    if (
        source
        in {
            HeartRateSource.MEASURED_SENSOR,
            HeartRateSource.PROVIDER_SUMMARY,
        }
        and not reliable
    ):
        return 1
    return {
        HeartRateSource.MEASURED_SENSOR: 5,
        HeartRateSource.PROVIDER_SUMMARY: 4,
        HeartRateSource.DERIVED: 3,
        HeartRateSource.USER_REPORTED: 2,
        HeartRateSource.UNAVAILABLE: 0,
    }[source]


def should_replace_metric(
    *,
    existing_value: object | None,
    incoming_value: object | None,
    existing_quality: HeartRateSource,
    incoming_quality: HeartRateSource,
    existing_reliable: bool = True,
    incoming_reliable: bool = True,
) -> bool:
    """Never erase data or replace a higher-quality value with a lower one."""

    if incoming_value is None:
        return False
    if existing_value is None:
        return True
    return metric_quality_rank(
        incoming_quality,
        reliable=incoming_reliable,
    ) > metric_quality_rank(
        existing_quality,
        reliable=existing_reliable,
    )


def activities_are_compatible(
    existing: Activity,
    incoming: ActivityImportData,
    *,
    thresholds: ActivityMatchThresholds = DEFAULT_MATCH_THRESHOLDS,
) -> bool:
    """Return whether all available high-confidence matching evidence agrees."""

    if existing.sport is not incoming.sport:
        return False
    start_delta = abs(
        (_as_utc(existing.started_at) - _as_utc(incoming.started_at)).total_seconds()
    )
    if start_delta > thresholds.start_tolerance_seconds:
        return False
    if not _similar_quantity(
        float(existing.duration_seconds),
        float(incoming.duration_seconds),
        absolute_tolerance=float(thresholds.duration_absolute_tolerance_seconds),
        relative_tolerance=thresholds.duration_relative_tolerance,
    ):
        return False
    if (
        existing.distance_meters is not None
        and incoming.distance_meters is not None
        and not _similar_quantity(
            existing.distance_meters,
            incoming.distance_meters,
            absolute_tolerance=thresholds.distance_absolute_tolerance_meters,
            relative_tolerance=thresholds.distance_relative_tolerance,
        )
    ):
        return False
    return True


def find_unambiguous_cross_source_match(
    candidates: list[Activity],
    incoming: ActivityImportData,
    *,
    thresholds: ActivityMatchThresholds = DEFAULT_MATCH_THRESHOLDS,
) -> tuple[Activity | None, ActivityMatchKind]:
    """Return one compatible other-source activity or an explicit outcome."""

    compatible = [
        candidate
        for candidate in candidates
        if candidate.source is not incoming.source
        and activities_are_compatible(
            candidate,
            incoming,
            thresholds=thresholds,
        )
    ]
    if len(compatible) == 1:
        return compatible[0], "cross_source"
    if len(compatible) > 1:
        return None, "ambiguous"
    return None, "new"


class TrainingActivityRepository:
    """Canonical activity writes with strict owner and source-key scoping."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        thresholds: ActivityMatchThresholds = DEFAULT_MATCH_THRESHOLDS,
    ) -> None:
        self._session = session
        self._thresholds = thresholds

    async def list_owned_candidates(
        self,
        *,
        user_id: uuid.UUID,
        sport: Discipline,
        started_at: datetime,
        incoming_source: ActivitySource,
    ) -> list[Activity]:
        """List nearby candidates that do not already carry this source."""

        normalized_start = _as_utc(started_at)
        tolerance = timedelta(seconds=self._thresholds.start_tolerance_seconds)
        incoming_link_exists = exists(
            select(ActivitySourceLink.id).where(
                ActivitySourceLink.user_id == user_id,
                ActivitySourceLink.activity_id == Activity.id,
                ActivitySourceLink.source == incoming_source,
            )
        )
        result = await self._session.scalars(
            select(Activity)
            .where(
                Activity.user_id == user_id,
                Activity.sport == sport,
                Activity.source != incoming_source,
                ~incoming_link_exists,
                Activity.deleted_at.is_(None),
                Activity.started_at >= normalized_start - tolerance,
                Activity.started_at <= normalized_start + tolerance,
            )
            .order_by(Activity.started_at, Activity.id)
        )
        return list(result.all())

    async def get_by_source_key(
        self,
        *,
        user_id: uuid.UUID,
        source: ActivitySource,
        external_id: str,
    ) -> Activity | None:
        """Resolve a provider identity only within its owning user."""

        linked = await self._session.scalar(
            select(Activity)
            .join(
                ActivitySourceLink,
                ActivitySourceLink.activity_id == Activity.id,
            )
            .where(
                ActivitySourceLink.user_id == user_id,
                ActivitySourceLink.source == source,
                ActivitySourceLink.external_id == external_id,
                Activity.user_id == user_id,
            )
        )
        if linked is not None:
            return linked
        result = await self._session.execute(
            select(Activity).where(
                Activity.user_id == user_id,
                Activity.source == source,
                Activity.external_id == external_id,
            )
        )
        return result.scalar_one_or_none()

    async def import_apple_workout(
        self,
        *,
        user_id: uuid.UUID,
        workout: ParsedWorkout,
        file_sha256: str | None,
        import_job_id: uuid.UUID | None,
    ) -> tuple[Activity, ActivityUpsertOutcome]:
        """Import or conservatively enrich one normalized Apple workout."""

        heart_rate_source = HeartRateSource.UNAVAILABLE
        if workout.average_heart_rate is not None:
            heart_rate_source = (
                HeartRateSource.MEASURED_SENSOR
                if workout.heart_rate_sample_count > 0
                else HeartRateSource.PROVIDER_SUMMARY
            )
        data = ActivityImportData(
            source=ActivitySource.APPLE_HEALTH,
            external_id=workout.source_record_key,
            sport=workout.discipline,
            source_sport_type=workout.source_workout_type,
            name=_activity_name(workout.discipline, "Apple Health"),
            started_at=workout.started_at,
            ended_at=workout.ended_at,
            duration_seconds=workout.duration_seconds,
            distance_meters=workout.distance_meters,
            calories_kcal=workout.calories_kcal,
            average_heart_rate=workout.average_heart_rate,
            average_heart_rate_source=heart_rate_source,
            max_heart_rate=workout.max_heart_rate,
            heart_rate_sample_count=workout.heart_rate_sample_count,
            heart_rate_quality=workout.heart_rate_quality,
            heart_rate_reliable=workout.heart_rate_reliable,
        )
        activity, outcome, _ = await self._import_activity(
            user_id=user_id,
            incoming=data,
            file_sha256=file_sha256,
            import_job_id=import_job_id,
        )
        observations_changed = await self._upsert_apple_observations(
            user_id=user_id,
            activity=activity,
            workout=workout,
        )
        if observations_changed and outcome == "unchanged":
            outcome = "updated"
        await self._session.flush()
        return activity, outcome

    async def import_tcx_activity(
        self,
        *,
        user_id: uuid.UUID,
        parsed: ParsedTCXActivity,
        file_sha256: str | None,
        import_job_id: uuid.UUID | None,
    ) -> tuple[Activity, ActivityUpsertOutcome, ActivityMatchKind]:
        """Import one parsed TCX activity or enrich one unambiguous match."""

        if parsed.started_at is None or parsed.duration_seconds is None:
            raise ActivityImportValidationError(
                "TCX activity requires a start time and duration"
            )
        route_points = tuple(
            _route_point(position) for position in parsed.route_positions
        )
        data = ActivityImportData(
            source=ActivitySource.TCX,
            external_id=parsed.source_record_key,
            sport=parsed.discipline,
            source_sport_type=parsed.source_sport_type,
            name=_activity_name(parsed.discipline, "TCX"),
            started_at=parsed.started_at,
            ended_at=parsed.ended_at,
            duration_seconds=parsed.duration_seconds,
            distance_meters=parsed.distance_meters,
            elevation_gain_meters=parsed.elevation_gain_meters,
            calories_kcal=parsed.calories_kcal,
            average_heart_rate=parsed.average_heart_rate,
            average_heart_rate_source=HeartRateSource(parsed.heart_rate_provenance),
            max_heart_rate=parsed.max_heart_rate,
            heart_rate_sample_count=parsed.heart_rate_sample_count,
            heart_rate_quality=parsed.heart_rate_quality,
            heart_rate_reliable=parsed.heart_rate_reliable,
            average_cadence=parsed.average_cadence,
            route_points=route_points,
        )
        return await self._import_activity(
            user_id=user_id,
            incoming=data,
            file_sha256=file_sha256,
            import_job_id=import_job_id,
        )

    async def ensure_source_link(
        self,
        *,
        user_id: uuid.UUID,
        activity_id: uuid.UUID,
        source: ActivitySource,
        external_id: str,
        file_sha256: str | None,
        import_job_id: uuid.UUID | None,
    ) -> ActivitySourceLink:
        """Idempotently attach one provider identity to one owned activity."""

        activity = await self._owned_activity(
            user_id=user_id,
            activity_id=activity_id,
        )
        if activity is None:
            raise OwnedRecordNotFoundError("Activity not found")
        await self._owned_import_job(
            user_id=user_id,
            import_job_id=import_job_id,
        )
        existing = await self._source_link(
            user_id=user_id,
            source=source,
            external_id=external_id,
        )
        if existing is not None:
            if existing.activity_id != activity.id:
                raise ActivitySourceConflictError(
                    "Source key is already linked to another activity"
                )
            changed = False
            if file_sha256 is not None and existing.file_sha256 != file_sha256:
                existing.file_sha256 = file_sha256
                changed = True
            if import_job_id is not None and existing.import_job_id != import_job_id:
                existing.import_job_id = import_job_id
                changed = True
            if changed:
                await self._session.flush()
            return existing

        link = ActivitySourceLink(
            user_id=user_id,
            activity_id=activity.id,
            source=source,
            external_id=external_id,
            file_sha256=file_sha256,
            import_job_id=import_job_id,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(link)
                await self._session.flush()
        except IntegrityError as error:
            existing = await self._source_link(
                user_id=user_id,
                source=source,
                external_id=external_id,
            )
            if existing is None or existing.activity_id != activity.id:
                raise ActivitySourceConflictError(
                    "Source key is already linked to another activity"
                ) from error
            return existing
        return link

    async def _import_activity(
        self,
        *,
        user_id: uuid.UUID,
        incoming: ActivityImportData,
        file_sha256: str | None,
        import_job_id: uuid.UUID | None,
    ) -> tuple[Activity, ActivityUpsertOutcome, ActivityMatchKind]:
        _validate_import(incoming)
        exact = await self.get_by_source_key(
            user_id=user_id,
            source=incoming.source,
            external_id=incoming.external_id,
        )
        if exact is not None:
            changed = merge_activity_non_destructively(
                exact,
                incoming,
                # A secondary source key can resolve to a canonical activity
                # whose primary source is different. Without per-metric
                # provenance for every objective field, treat that replay as
                # cross-source enrichment and preserve existing values.
                same_source=exact.source is incoming.source,
            )
            await self.ensure_source_link(
                user_id=user_id,
                activity_id=exact.id,
                source=incoming.source,
                external_id=incoming.external_id,
                file_sha256=file_sha256,
                import_job_id=import_job_id,
            )
            await self._attach_job(
                user_id=user_id,
                import_job_id=import_job_id,
                activity=exact,
            )
            await self._session.flush()
            return exact, "updated" if changed else "unchanged", "source_key"

        candidates = await self.list_owned_candidates(
            user_id=user_id,
            sport=incoming.sport,
            started_at=incoming.started_at,
            incoming_source=incoming.source,
        )
        matched, match_kind = find_unambiguous_cross_source_match(
            candidates,
            incoming,
            thresholds=self._thresholds,
        )
        if matched is not None:
            changed = merge_activity_non_destructively(
                matched,
                incoming,
                same_source=False,
            )
            await self.ensure_source_link(
                user_id=user_id,
                activity_id=matched.id,
                source=incoming.source,
                external_id=incoming.external_id,
                file_sha256=file_sha256,
                import_job_id=import_job_id,
            )
            await self._attach_job(
                user_id=user_id,
                import_job_id=import_job_id,
                activity=matched,
            )
            await self._session.flush()
            return (
                matched,
                "updated" if changed else "unchanged",
                "cross_source",
            )

        inserted = Activity(
            user_id=user_id,
            source=incoming.source,
            external_id=incoming.external_id,
            sport=incoming.sport,
            source_sport_type=incoming.source_sport_type,
            name=incoming.name,
            started_at=_as_utc(incoming.started_at),
            ended_at=(
                _as_utc(incoming.ended_at) if incoming.ended_at is not None else None
            ),
            timezone=incoming.timezone,
            duration_seconds=incoming.duration_seconds,
            moving_time_seconds=incoming.moving_time_seconds,
            distance_meters=incoming.distance_meters,
            elevation_gain_meters=incoming.elevation_gain_meters,
            calories_kcal=incoming.calories_kcal,
            average_heart_rate=incoming.average_heart_rate,
            average_heart_rate_source=incoming.average_heart_rate_source,
            max_heart_rate=(
                incoming.max_heart_rate if incoming.heart_rate_reliable else None
            ),
            heart_rate_sample_count=incoming.heart_rate_sample_count,
            heart_rate_quality=incoming.heart_rate_quality,
            heart_rate_reliable=incoming.heart_rate_reliable,
            average_cadence=incoming.average_cadence,
            route_points=(
                list(incoming.route_points) if incoming.route_points else None
            ),
            average_speed=None,
            average_watts=None,
            trainer=False,
            commute=False,
            manual=False,
            raw_summary=None,
            deleted_at=None,
        )
        self._session.add(inserted)
        await self._session.flush()
        await self.ensure_source_link(
            user_id=user_id,
            activity_id=inserted.id,
            source=incoming.source,
            external_id=incoming.external_id,
            file_sha256=file_sha256,
            import_job_id=import_job_id,
        )
        await self._attach_job(
            user_id=user_id,
            import_job_id=import_job_id,
            activity=inserted,
        )
        await self._session.flush()
        return inserted, "inserted", match_kind

    async def _owned_activity(
        self,
        *,
        user_id: uuid.UUID,
        activity_id: uuid.UUID,
    ) -> Activity | None:
        result = await self._session.execute(
            select(Activity).where(
                Activity.user_id == user_id,
                Activity.id == activity_id,
            )
        )
        return result.scalar_one_or_none()

    async def _owned_import_job(
        self,
        *,
        user_id: uuid.UUID,
        import_job_id: uuid.UUID | None,
    ) -> AppleHealthImportJob | None:
        if import_job_id is None:
            return None
        job = await self._session.scalar(
            select(AppleHealthImportJob).where(
                AppleHealthImportJob.user_id == user_id,
                AppleHealthImportJob.id == import_job_id,
            )
        )
        if job is None:
            raise OwnedRecordNotFoundError("Training import job not found")
        return job

    async def _source_link(
        self,
        *,
        user_id: uuid.UUID,
        source: ActivitySource,
        external_id: str,
    ) -> ActivitySourceLink | None:
        result = await self._session.execute(
            select(ActivitySourceLink).where(
                ActivitySourceLink.user_id == user_id,
                ActivitySourceLink.source == source,
                ActivitySourceLink.external_id == external_id,
            )
        )
        return result.scalar_one_or_none()

    async def _attach_job(
        self,
        *,
        user_id: uuid.UUID,
        import_job_id: uuid.UUID | None,
        activity: Activity,
    ) -> None:
        job = await self._owned_import_job(
            user_id=user_id,
            import_job_id=import_job_id,
        )
        if job is not None and job.activity_id != activity.id:
            job.activity_id = activity.id

    async def _upsert_apple_observations(
        self,
        *,
        user_id: uuid.UUID,
        activity: Activity,
        workout: ParsedWorkout,
    ) -> bool:
        if not workout.observations:
            return False
        keys = [item.source_record_key for item in workout.observations]
        existing = {
            item.source_record_key: item
            for item in (
                await self._session.scalars(
                    select(HeartRateObservation).where(
                        HeartRateObservation.user_id == user_id,
                        HeartRateObservation.source_record_key.in_(keys),
                    )
                )
            ).all()
        }
        changed = False
        for observation in workout.observations:
            record = existing.get(observation.source_record_key)
            values: dict[str, object] = {
                "activity_id": activity.id,
                "source_name": observation.source_name,
                "started_at": observation.started_at,
                "ended_at": observation.ended_at,
                "beats_per_minute": observation.beats_per_minute,
                "temporal_quality": observation.temporal_quality,
            }
            if record is None:
                self._session.add(
                    HeartRateObservation(
                        user_id=user_id,
                        source_record_key=observation.source_record_key,
                        **values,
                    )
                )
                changed = True
            elif any(getattr(record, name) != value for name, value in values.items()):
                for name, value in values.items():
                    setattr(record, name, value)
                changed = True
        return changed


def merge_activity_non_destructively(
    activity: Activity,
    incoming: ActivityImportData,
    *,
    same_source: bool,
) -> bool:
    changed = False
    existing_hr_source = activity.average_heart_rate_source
    existing_hr_reliable = activity.heart_rate_reliable
    existing_hr_sample_count = activity.heart_rate_sample_count

    def assign(name: str, value: object) -> None:
        nonlocal changed
        if getattr(activity, name) != value:
            setattr(activity, name, value)
            changed = True

    if same_source:
        assign("sport", incoming.sport)
        assign("source_sport_type", incoming.source_sport_type)
        assign("name", incoming.name)
        assign("started_at", _as_utc(incoming.started_at))
        assign("duration_seconds", incoming.duration_seconds)
        if incoming.ended_at is not None:
            assign("ended_at", _as_utc(incoming.ended_at))
        if incoming.timezone is not None:
            assign("timezone", incoming.timezone)
        if incoming.moving_time_seconds is not None:
            assign("moving_time_seconds", incoming.moving_time_seconds)
    else:
        if activity.ended_at is None and incoming.ended_at is not None:
            assign("ended_at", _as_utc(incoming.ended_at))
        if activity.timezone is None and incoming.timezone is not None:
            assign("timezone", incoming.timezone)
        if (
            activity.moving_time_seconds is None
            and incoming.moving_time_seconds is not None
        ):
            assign("moving_time_seconds", incoming.moving_time_seconds)

    for name, value in (
        ("distance_meters", incoming.distance_meters),
        ("elevation_gain_meters", incoming.elevation_gain_meters),
        ("calories_kcal", incoming.calories_kcal),
        ("average_cadence", incoming.average_cadence),
    ):
        if value is not None and (same_source or getattr(activity, name) is None):
            assign(name, value)

    if incoming.route_points and (same_source or not activity.route_points):
        assign("route_points", list(incoming.route_points))

    replace_average_hr = should_replace_metric(
        existing_value=activity.average_heart_rate,
        incoming_value=incoming.average_heart_rate,
        existing_quality=existing_hr_source,
        incoming_quality=incoming.average_heart_rate_source,
        existing_reliable=existing_hr_reliable,
        incoming_reliable=incoming.heart_rate_reliable,
    )
    if (
        same_source
        and incoming.average_heart_rate is not None
        and metric_quality_rank(
            incoming.average_heart_rate_source,
            reliable=incoming.heart_rate_reliable,
        )
        == metric_quality_rank(
            existing_hr_source,
            reliable=existing_hr_reliable,
        )
    ):
        replace_average_hr = True
    if replace_average_hr:
        assign("average_heart_rate", incoming.average_heart_rate)
        assign(
            "average_heart_rate_source",
            incoming.average_heart_rate_source,
        )
        assign("heart_rate_quality", incoming.heart_rate_quality)
        assign("heart_rate_reliable", incoming.heart_rate_reliable)
        assign("heart_rate_sample_count", incoming.heart_rate_sample_count)

    if incoming.max_heart_rate is not None and incoming.heart_rate_reliable:
        if (
            activity.max_heart_rate is None
            or not existing_hr_reliable
            or metric_quality_rank(
                incoming.average_heart_rate_source,
                reliable=incoming.heart_rate_reliable,
            )
            > metric_quality_rank(
                existing_hr_source,
                reliable=existing_hr_reliable,
            )
            or (
                same_source
                and incoming.heart_rate_sample_count >= existing_hr_sample_count
            )
        ):
            assign("max_heart_rate", incoming.max_heart_rate)
    return changed


def _validate_import(incoming: ActivityImportData) -> None:
    if not incoming.external_id or len(incoming.external_id) > 128:
        raise ActivityImportValidationError("Invalid activity source key")
    if incoming.duration_seconds < 0:
        raise ActivityImportValidationError("Activity duration cannot be negative")
    _as_utc(incoming.started_at)
    if incoming.ended_at is not None:
        _as_utc(incoming.ended_at)


def _similar_quantity(
    first: float,
    second: float,
    *,
    absolute_tolerance: float,
    relative_tolerance: float,
) -> bool:
    tolerance = max(
        absolute_tolerance,
        max(abs(first), abs(second)) * relative_tolerance,
    )
    return abs(first - second) <= tolerance


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        # SQLite does not preserve timezone offsets on round-trip. Treat its
        # portable-test values as UTC; application boundaries remain aware.
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _activity_name(discipline: Discipline, provider: str) -> str:
    labels = {
        Discipline.RUN: "run",
        Discipline.RIDE: "ride",
        Discipline.SWIM: "swim",
        Discipline.WALK_HIKE: "walk or hike",
        Discipline.STRENGTH: "strength workout",
        Discipline.OTHER: "workout",
    }
    return f"{provider} {labels[discipline]}"


def _route_point(position: ParsedTCXPosition) -> dict[str, object]:
    values = asdict(position)
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in values.items()
        if value is not None
    }


__all__ = [
    "DEFAULT_MATCH_THRESHOLDS",
    "ActivityImportData",
    "ActivityImportValidationError",
    "ActivityMatchKind",
    "ActivityMatchThresholds",
    "ActivitySourceConflictError",
    "ActivityUpsertOutcome",
    "TrainingActivityRepository",
    "activities_are_compatible",
    "find_unambiguous_cross_source_match",
    "merge_activity_non_destructively",
    "metric_quality_rank",
    "should_replace_metric",
]
