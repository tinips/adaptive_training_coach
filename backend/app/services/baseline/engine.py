"""Pure deterministic baseline calculations."""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from app.domain.enums import BaselineStatus, Discipline, LevelLabel
from app.schemas.baseline import (
    BaselineActivity,
    BaselineCalculation,
    DisciplineBaselineResult,
)
from app.services.baseline.thresholds import (
    DISTANCE_MEANINGFUL_DISCIPLINES,
    HEURISTIC_VERSION,
    PROVISIONAL_LEVEL_THRESHOLDS,
)


class BaselineEngine:
    """Calculate versionable baseline metrics without external state or an LLM."""

    def calculate(
        self,
        *,
        activities: Iterable[BaselineActivity],
        analysis_start: datetime,
        analysis_end: datetime,
        generated_at: datetime | None = None,
    ) -> BaselineCalculation:
        """Calculate every stable discipline independently."""

        start = self._as_utc(analysis_start)
        end = self._as_utc(analysis_end)
        if start > end:
            raise ValueError("analysis_start must not be after analysis_end.")
        generated = self._as_utc(generated_at or end)
        included = [
            activity
            for activity in activities
            if start <= self._as_utc(activity.started_at) <= end
        ]
        disciplines = [
            self._calculate_discipline(
                discipline=discipline,
                activities=[
                    activity
                    for activity in included
                    if activity.discipline == discipline
                ],
                analysis_start=start,
                analysis_end=end,
            )
            for discipline in Discipline
        ]
        populated = [item for item in disciplines if item.sessions_count]
        overall_confidence = (
            round(
                sum(item.confidence * item.sessions_count for item in populated)
                / sum(item.sessions_count for item in populated),
                4,
            )
            if populated
            else 0.0
        )
        status = (
            BaselineStatus.READY
            if populated and overall_confidence >= 0.35
            else BaselineStatus.INSUFFICIENT_DATA
        )
        return BaselineCalculation(
            generated_at=generated,
            analysis_start=start,
            analysis_end=end,
            status=status,
            overall_confidence=overall_confidence,
            disciplines=disciplines,
        )

    def _calculate_discipline(
        self,
        *,
        discipline: Discipline,
        activities: list[BaselineActivity],
        analysis_start: datetime,
        analysis_end: datetime,
    ) -> DisciplineBaselineResult:
        window_weeks = max(
            (analysis_end - analysis_start).total_seconds()
            / timedelta(weeks=1).total_seconds(),
            1.0,
        )
        if not activities:
            return DisciplineBaselineResult(
                discipline=discipline,
                level_label=LevelLabel.UNKNOWN,
                confidence=0,
                sessions_count=0,
                active_weeks=0,
                total_duration_seconds=0,
                average_weekly_duration_seconds=0,
                total_distance_meters=None,
                average_weekly_distance_meters=None,
                longest_session_seconds=None,
                longest_distance_meters=None,
                recent_session_count=0,
                metrics={
                    "window_weeks": round(window_weeks, 4),
                    "data_recency_days": None,
                    "consistency_ratio": 0.0,
                    "field_coverage": 0.0,
                    "heuristic_version": HEURISTIC_VERSION,
                    "heuristic_is_provisional": True,
                },
            )

        ordered = sorted(
            activities,
            key=lambda item: (
                self._as_utc(item.started_at),
                str(item.id or ""),
            ),
        )
        sessions_count = len(ordered)
        active_week_keys = {
            self._as_utc(activity.started_at).date().isocalendar()[:2]
            for activity in ordered
        }
        active_weeks = len(active_week_keys)
        total_duration = sum(activity.duration_seconds for activity in ordered)
        average_weekly_duration = total_duration / window_weeks
        latest_start = self._as_utc(ordered[-1].started_at)
        recency_days = max(
            (analysis_end - latest_start).total_seconds()
            / timedelta(days=1).total_seconds(),
            0.0,
        )
        recent_cutoff = analysis_end - timedelta(days=14)
        recent_count = sum(
            self._as_utc(activity.started_at) >= recent_cutoff for activity in ordered
        )
        meaningful_distance = discipline in DISTANCE_MEANINGFUL_DISCIPLINES
        observed_distances = [
            activity.distance_meters
            for activity in ordered
            if activity.distance_meters is not None
        ]
        total_distance = (
            float(sum(observed_distances))
            if meaningful_distance and observed_distances
            else None
        )
        average_weekly_distance = (
            total_distance / window_weeks if total_distance is not None else None
        )
        longest_distance = (
            max(observed_distances)
            if meaningful_distance and observed_distances
            else None
        )
        distance_coverage = (
            len(observed_distances) / sessions_count if meaningful_distance else 1.0
        )
        duration_coverage = (
            sum(activity.duration_seconds > 0 for activity in ordered) / sessions_count
        )
        field_coverage = (duration_coverage + distance_coverage) / 2
        consistency_ratio = min(
            active_weeks / max(math.ceil(window_weeks), 1),
            1.0,
        )
        confidence = self._confidence(
            sessions_count=sessions_count,
            active_weeks=active_weeks,
            recency_days=recency_days,
            field_coverage=field_coverage,
        )
        level = self._level(
            discipline=discipline,
            sessions_count=sessions_count,
            average_weekly_duration_seconds=average_weekly_duration,
        )
        return DisciplineBaselineResult(
            discipline=discipline,
            level_label=level,
            confidence=confidence,
            sessions_count=sessions_count,
            active_weeks=active_weeks,
            total_duration_seconds=total_duration,
            average_weekly_duration_seconds=round(average_weekly_duration, 2),
            total_distance_meters=(
                round(total_distance, 2) if total_distance is not None else None
            ),
            average_weekly_distance_meters=(
                round(average_weekly_distance, 2)
                if average_weekly_distance is not None
                else None
            ),
            longest_session_seconds=max(
                activity.duration_seconds for activity in ordered
            ),
            longest_distance_meters=(
                round(longest_distance, 2) if longest_distance is not None else None
            ),
            recent_session_count=recent_count,
            metrics={
                "window_weeks": round(window_weeks, 4),
                "data_recency_days": round(recency_days, 2),
                "last_activity_at": latest_start.isoformat(),
                "consistency_ratio": round(consistency_ratio, 4),
                "sessions_per_window_week": round(sessions_count / window_weeks, 4),
                "duration_coverage": round(duration_coverage, 4),
                "distance_coverage": round(distance_coverage, 4),
                "field_coverage": round(field_coverage, 4),
                "heart_rate_coverage": round(
                    sum(activity.average_heart_rate is not None for activity in ordered)
                    / sessions_count,
                    4,
                ),
                "heuristic_version": HEURISTIC_VERSION,
                "heuristic_is_provisional": True,
            },
        )

    @staticmethod
    def _confidence(
        *,
        sessions_count: int,
        active_weeks: int,
        recency_days: float,
        field_coverage: float,
    ) -> float:
        session_score = min(sessions_count / 8, 1.0)
        week_score = min(active_weeks / 4, 1.0)
        if recency_days <= 7:
            recency_score = 1.0
        elif recency_days <= 14:
            recency_score = 0.85
        elif recency_days <= 28:
            recency_score = 0.55
        elif recency_days <= 56:
            recency_score = 0.25
        else:
            recency_score = 0.0
        confidence = (
            0.35 * session_score
            + 0.25 * week_score
            + 0.25 * recency_score
            + 0.15 * field_coverage
        )
        return round(min(max(confidence, 0.0), 1.0), 4)

    @staticmethod
    def _level(
        *,
        discipline: Discipline,
        sessions_count: int,
        average_weekly_duration_seconds: float,
    ) -> LevelLabel:
        result = LevelLabel.UNKNOWN
        for threshold in PROVISIONAL_LEVEL_THRESHOLDS[discipline]:
            if (
                sessions_count >= threshold.minimum_sessions
                and average_weekly_duration_seconds
                >= threshold.minimum_weekly_duration_seconds
            ):
                result = threshold.label
        return result

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Baseline timestamps must be timezone-aware.")
        return value.astimezone(UTC)
