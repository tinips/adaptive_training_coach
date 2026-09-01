"""Read-only, timezone-aware workout-history dashboard service."""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Workout
from app.repositories.users import UserRepository
from app.repositories.workout_history import WorkoutHistoryRepository
from app.schemas.common import TelegramIdentity
from app.schemas.workout_history import (
    WorkoutHistoryCard,
    WorkoutHistoryChartBucket,
    WorkoutHistoryQuery,
    WorkoutHistoryResponse,
    WorkoutHistoryTotals,
)
from app.schemas.workouts import workout_metrics

_PAGE_SIZE = 20


class WorkoutHistoryUserNotFoundError(RuntimeError):
    """The signed Telegram identity no longer has a local account."""


class WorkoutHistoryCursorError(ValueError):
    """The client supplied a stale or malformed opaque cursor."""


class WorkoutHistoryService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def query(
        self,
        *,
        identity: TelegramIdentity,
        request: WorkoutHistoryQuery,
    ) -> WorkoutHistoryResponse:
        async with self._session_factory() as session:
            user = await UserRepository(session).get_by_telegram_id(
                identity.telegram_user_id
            )
            if user is None:
                raise WorkoutHistoryUserNotFoundError
            timezone = _timezone(user.timezone)
            range_start = datetime.combine(request.start_date, time.min, timezone)
            range_end = datetime.combine(
                request.end_date + timedelta(days=1), time.min, timezone
            )
            repository = WorkoutHistoryRepository(session)
            workouts = await repository.list_owned_for_range(
                athlete_id=user.id,
                started_at=range_start.astimezone(UTC),
                ended_before=range_end.astimezone(UTC),
                discipline=request.discipline,
            )
            available_disciplines = await repository.available_disciplines_for_range(
                athlete_id=user.id,
                started_at=range_start.astimezone(UTC),
                ended_before=range_end.astimezone(UTC),
            )

        cursor = _decode_cursor(request.cursor) if request.cursor else None
        page_start = _page_start(workouts, cursor)
        page = workouts[page_start : page_start + _PAGE_SIZE]
        next_cursor = (
            _encode_cursor(page[-1])
            if len(workouts) > page_start + len(page) and page
            else None
        )
        return WorkoutHistoryResponse(
            timezone=timezone.key,
            available_disciplines=list(available_disciplines),
            totals=_totals(workouts),
            chart_buckets=_chart_buckets(
                workouts=workouts,
                start_date=request.start_date,
                end_date=request.end_date,
                timezone=timezone,
            ),
            workouts=[_card(workout, timezone) for workout in page],
            next_cursor=next_cursor,
        )


def _timezone(value: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(value or "UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _totals(workouts: tuple[Workout, ...]) -> WorkoutHistoryTotals:
    metrics = [workout_metrics(workout) for workout in workouts]
    return WorkoutHistoryTotals(
        session_count=len(workouts),
        duration_seconds=sum(item.duration_seconds for item in metrics),
        distance_meters=sum(item.distance_meters or 0 for item in metrics),
    )


def _card(workout: Workout, timezone: ZoneInfo) -> WorkoutHistoryCard:
    metrics = workout_metrics(workout)
    title = workout.title
    if title is None and metrics.raw_sport:
        title = metrics.raw_sport.replace("_", " ").title()
    return WorkoutHistoryCard(
        discipline=workout.discipline,
        started_at=_as_utc(workout.started_at).astimezone(timezone),
        title=title,
        duration_seconds=workout.duration_seconds,
        distance_meters=metrics.distance_meters,
        calories_kcal=metrics.calories_kcal,
        average_heart_rate=metrics.average_heart_rate,
    )


def _chart_buckets(
    *,
    workouts: tuple[Workout, ...],
    start_date: date,
    end_date: date,
    timezone: ZoneInfo,
) -> list[WorkoutHistoryChartBucket]:
    daily = (end_date - start_date).days <= 30
    bucket_start = start_date if daily else start_date - timedelta(start_date.weekday())
    final_bucket = end_date if daily else end_date - timedelta(end_date.weekday())
    values: dict[date, dict[str, dict[str, float]]] = defaultdict(
        lambda: {"duration": defaultdict(float), "distance": defaultdict(float)}
    )
    for workout in workouts:
        local_date = _as_utc(workout.started_at).astimezone(timezone).date()
        key = local_date if daily else local_date - timedelta(local_date.weekday())
        metrics = workout_metrics(workout)
        discipline = workout.discipline.value
        values[key]["duration"][discipline] += workout.duration_seconds
        if metrics.distance_meters is not None:
            values[key]["distance"][discipline] += metrics.distance_meters

    buckets: list[WorkoutHistoryChartBucket] = []
    current = bucket_start
    increment = timedelta(days=1 if daily else 7)
    while current <= final_bucket:
        item = values[current]
        buckets.append(
            WorkoutHistoryChartBucket(
                start_date=current,
                label=current.strftime("%d %b"),
                duration_seconds_by_discipline={
                    key: round(value) for key, value in item["duration"].items()
                },
                distance_meters_by_discipline={
                    key: round(value, 2) for key, value in item["distance"].items()
                },
            )
        )
        current += increment
    return buckets


def _encode_cursor(workout: Workout) -> str:
    payload = json.dumps(
        {
            "started_at": _as_utc(workout.started_at).isoformat(),
            "id": str(workout.id),
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        started_at = datetime.fromisoformat(payload["started_at"])
        workout_id = uuid.UUID(payload["id"])
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        binascii.Error,
    ) as error:
        raise WorkoutHistoryCursorError("invalid cursor") from error
    if started_at.tzinfo is None:
        raise WorkoutHistoryCursorError("invalid cursor")
    return started_at.astimezone(UTC), workout_id


def _page_start(
    workouts: tuple[Workout, ...], cursor: tuple[datetime, uuid.UUID] | None
) -> int:
    if cursor is None:
        return 0
    for index, workout in enumerate(workouts):
        if (
            _as_utc(workout.started_at) == cursor[0]
            and workout.id == cursor[1]
        ):
            return index + 1
    raise WorkoutHistoryCursorError("cursor is no longer in this result set")


def _as_utc(value: datetime) -> datetime:
    """SQLite test storage can lose tzinfo; persisted production values are UTC."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
