"""Secure, bounded and deterministic TCX parsing."""

from __future__ import annotations

import hashlib
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import IO

from app.domain.enums import Discipline
from app.integrations.tcx.models import (
    ParsedTCXActivity,
    ParsedTCXPosition,
)

_SUPPORTED_NAMESPACES = {
    "",
    "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v1",
    "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2",
}
_DATED_SPORT_PREFIX = re.compile(r"^\d{8}\s*")
_UNSAFE_XML_MARKERS = {
    b"<!DOCTYPE": "unsafe_xml_doctype",
    b"<!ENTITY": "unsafe_xml_entity",
}


class TCXParserError(ValueError):
    """Safe deterministic parser failure suitable for application rendering."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(slots=True, frozen=True)
class TCXParserLimits:
    """Resource ceiling applied before and while XML is parsed."""

    max_bytes: int

    def __post_init__(self) -> None:
        if self.max_bytes < 1:
            raise ValueError("max_bytes must be positive")


@dataclass(slots=True, frozen=True)
class _TimedValue:
    value: float
    timestamp: datetime | None


@dataclass(slots=True)
class _ParsedLap:
    start_time: datetime | None
    duration_summary: float | None
    distance_summary: float | None
    calories_summary: float | None
    average_heart_rate_summary: float | None
    max_heart_rate_summary: float | None
    cadence_summary: float | None
    trackpoint_times: list[datetime] = field(default_factory=list)
    trackpoint_distances: list[float] = field(default_factory=list)
    heart_rate_samples: list[_TimedValue] = field(default_factory=list)
    cadence_samples: list[float] = field(default_factory=list)
    altitude_segments: list[list[float]] = field(default_factory=list)
    route_positions: list[ParsedTCXPosition] = field(default_factory=list)


class _SecureBoundedXMLReader:
    """Reject declarations and enforce the byte ceiling as XML is consumed."""

    _CHUNK_SIZE = 65_536
    _MARKER_OVERLAP = max(len(marker) for marker in _UNSAFE_XML_MARKERS) - 1

    def __init__(self, stream: IO[bytes], *, max_bytes: int) -> None:
        self._stream = stream
        self._max_bytes = max_bytes
        self._bytes_read = 0
        self._tail = b""

    def read(self, size: int = -1) -> bytes:
        requested = self._CHUNK_SIZE if size < 0 else min(size, self._CHUNK_SIZE)
        if requested == 0:
            return b""
        remaining = self._max_bytes - self._bytes_read
        chunk = self._stream.read(min(requested, remaining + 1))
        if len(chunk) > remaining:
            raise TCXParserError("tcx_file_size_exceeded")
        self._bytes_read += len(chunk)
        if b"\x00" in chunk:
            # Reject multibyte XML encodings rather than let their NUL-padded
            # declarations bypass the byte-level DTD/entity policy.
            raise TCXParserError("unsafe_xml_encoding")
        combined = (self._tail + chunk).upper()
        for marker, code in _UNSAFE_XML_MARKERS.items():
            if marker in combined:
                raise TCXParserError(code)
        self._tail = combined[-self._MARKER_OVERLAP :]
        return chunk


class TCXParser:
    """Parse exactly one TCX activity without guessing unavailable metrics."""

    def __init__(self, limits: TCXParserLimits) -> None:
        self._limits = limits

    @property
    def max_bytes(self) -> int:
        return self._limits.max_bytes

    def parse(self, path: Path) -> ParsedTCXActivity:
        """Validate and normalize one bounded TCX document."""

        try:
            size = path.stat().st_size
        except OSError as exc:
            raise TCXParserError("tcx_file_unavailable") from exc
        if size > self._limits.max_bytes:
            raise TCXParserError("tcx_file_size_exceeded")

        try:
            with path.open("rb") as stream:
                root = ET.parse(
                    _SecureBoundedXMLReader(
                        stream,
                        max_bytes=self._limits.max_bytes,
                    )
                ).getroot()
        except TCXParserError:
            raise
        except (ET.ParseError, OSError, RuntimeError) as exc:
            raise TCXParserError("malformed_tcx_xml") from exc

        self._validate_root(root)
        activity = self._single_activity(root)
        laps = _direct_children(activity, "Lap")
        if not laps:
            raise TCXParserError("tcx_lap_not_found")

        warnings: list[str] = []
        parsed_laps = [self._parse_lap(lap, warnings=warnings) for lap in laps]
        source_sport_type = activity.attrib.get("Sport", "").strip() or "Unknown"
        raw_sub_sport = _first_descendant_text(
            activity,
            names={"SubSport"},
        )
        discipline = _discipline(source_sport_type)
        activity_id = _direct_text(activity, "Id")
        parsed_activity_id = _parse_timestamp(activity_id)
        all_trackpoint_times = [
            timestamp for lap in parsed_laps for timestamp in lap.trackpoint_times
        ]
        lap_start_times = [
            lap.start_time for lap in parsed_laps if lap.start_time is not None
        ]
        observed_starts = [*lap_start_times, *all_trackpoint_times]
        started_at = parsed_activity_id or (
            min(observed_starts) if observed_starts else None
        )
        ended_at = max(all_trackpoint_times) if all_trackpoint_times else None
        if activity_id is None and started_at is None:
            raise TCXParserError("tcx_activity_identity_missing")

        resolved_durations = [
            _lap_duration(lap, warnings=warnings) for lap in parsed_laps
        ]
        duration_seconds = _complete_sum(resolved_durations)
        resolved_distances = [
            _lap_distance(lap, warnings=warnings) for lap in parsed_laps
        ]
        distance_meters = _complete_sum(resolved_distances)
        calories_kcal = _complete_sum([lap.calories_summary for lap in parsed_laps])
        (
            average_heart_rate,
            max_heart_rate,
            heart_rate_records_matched,
        ) = _heart_rate_summary(
            parsed_laps,
            resolved_durations=resolved_durations,
            warnings=warnings,
        )
        average_cadence, cadence_sample_count, max_cadence = _cadence_summary(
            parsed_laps,
            resolved_durations=resolved_durations,
            warnings=warnings,
        )
        (
            elevation_gain_meters,
            elevation_loss_meters,
            minimum_altitude_meters,
            maximum_altitude_meters,
        ) = _elevation_summary(parsed_laps)
        route_positions = tuple(
            position for lap in parsed_laps for position in lap.route_positions
        )
        normalized_activity_id = (
            parsed_activity_id.isoformat()
            if parsed_activity_id is not None
            else activity_id
        )
        identity = normalized_activity_id or started_at.isoformat()  # type: ignore[union-attr]

        return ParsedTCXActivity(
            source_record_key=_stable_hash("TCX", identity),
            activity_id=activity_id,
            source_sport_type=source_sport_type,
            discipline=discipline,
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=(
                round(duration_seconds) if duration_seconds is not None else None
            ),
            distance_meters=distance_meters,
            calories_kcal=calories_kcal,
            elevation_gain_meters=elevation_gain_meters,
            minimum_altitude_meters=minimum_altitude_meters,
            maximum_altitude_meters=maximum_altitude_meters,
            average_heart_rate=average_heart_rate,
            max_heart_rate=max_heart_rate,
            heart_rate_records_matched=heart_rate_records_matched,
            average_cadence=average_cadence,
            cadence_sample_count=cadence_sample_count,
            route_positions=route_positions,
            warnings=tuple(warnings),
            raw_sub_sport=raw_sub_sport,
            elevation_loss_meters=elevation_loss_meters,
            max_cadence=max_cadence,
        )

    @staticmethod
    def _validate_root(root: ET.Element) -> None:
        if _local_name(root.tag) != "TrainingCenterDatabase":
            raise TCXParserError("tcx_root_invalid")
        if _namespace(root.tag) not in _SUPPORTED_NAMESPACES:
            raise TCXParserError("tcx_namespace_unsupported")

    @staticmethod
    def _single_activity(root: ET.Element) -> ET.Element:
        activities = [
            activity
            for container in _direct_children(root, "Activities")
            for activity in _direct_children(container, "Activity")
        ]
        if not activities:
            raise TCXParserError("tcx_activity_not_found")
        if len(activities) != 1:
            raise TCXParserError("tcx_multiple_activities_not_supported")
        return activities[0]

    @staticmethod
    def _parse_lap(
        lap: ET.Element,
        *,
        warnings: list[str],
    ) -> _ParsedLap:
        start_time_text = lap.attrib.get("StartTime")
        start_time = _parse_timestamp(start_time_text)
        if start_time_text and start_time is None:
            _warn(warnings, "invalid_lap_start_time")
        result = _ParsedLap(
            start_time=start_time,
            duration_summary=_nonnegative_float(
                _direct_text(lap, "TotalTimeSeconds"),
                "invalid_duration",
                warnings,
            ),
            distance_summary=_nonnegative_float(
                _direct_text(lap, "DistanceMeters"),
                "invalid_distance",
                warnings,
            ),
            calories_summary=_nonnegative_float(
                _direct_text(lap, "Calories"),
                "invalid_calories",
                warnings,
            ),
            average_heart_rate_summary=_heart_rate_value(
                _direct_child(lap, "AverageHeartRateBpm"),
                warnings=warnings,
            ),
            max_heart_rate_summary=_heart_rate_value(
                _direct_child(lap, "MaximumHeartRateBpm"),
                warnings=warnings,
            ),
            cadence_summary=_nonnegative_float(
                _direct_text(lap, "Cadence"),
                "invalid_cadence",
                warnings,
            ),
        )
        for track in _direct_children(lap, "Track"):
            TCXParser._parse_track(
                track,
                result=result,
                warnings=warnings,
            )
        return result

    @staticmethod
    def _parse_track(
        track: ET.Element,
        *,
        result: _ParsedLap,
        warnings: list[str],
    ) -> None:
        altitude_segment: list[float] = []
        for trackpoint in _direct_children(track, "Trackpoint"):
            timestamp_text = _direct_text(trackpoint, "Time")
            timestamp = _parse_timestamp(timestamp_text)
            if timestamp_text and timestamp is None:
                _warn(warnings, "invalid_trackpoint_time")
            if timestamp is not None:
                result.trackpoint_times.append(timestamp)

            distance = _nonnegative_float(
                _direct_text(trackpoint, "DistanceMeters"),
                "invalid_distance",
                warnings,
            )
            if distance is not None:
                result.trackpoint_distances.append(distance)

            altitude = _finite_float(
                _direct_text(trackpoint, "AltitudeMeters"),
                "invalid_altitude",
                warnings,
            )
            if altitude is None:
                if altitude_segment:
                    result.altitude_segments.append(altitude_segment)
                    altitude_segment = []
            else:
                altitude_segment.append(altitude)

            heart_rate = _heart_rate_value(
                _direct_child(trackpoint, "HeartRateBpm"),
                warnings=warnings,
            )
            if heart_rate is not None:
                result.heart_rate_samples.append(_TimedValue(heart_rate, timestamp))

            cadence_text = _direct_text(trackpoint, "Cadence")
            if cadence_text is None:
                cadence_text = _first_descendant_text(
                    trackpoint,
                    names={"RunCadence"},
                )
            cadence = _nonnegative_float(
                cadence_text,
                "invalid_cadence",
                warnings,
            )
            if cadence is not None:
                result.cadence_samples.append(cadence)

            position = _position(
                trackpoint,
                timestamp=timestamp,
                altitude=altitude,
                distance=distance,
                warnings=warnings,
            )
            if position is not None:
                result.route_positions.append(position)
        if altitude_segment:
            result.altitude_segments.append(altitude_segment)


def _position(
    trackpoint: ET.Element,
    *,
    timestamp: datetime | None,
    altitude: float | None,
    distance: float | None,
    warnings: list[str],
) -> ParsedTCXPosition | None:
    element = _direct_child(trackpoint, "Position")
    if element is None:
        return None
    latitude = _finite_float(
        _direct_text(element, "LatitudeDegrees"),
        "invalid_position",
        warnings,
    )
    longitude = _finite_float(
        _direct_text(element, "LongitudeDegrees"),
        "invalid_position",
        warnings,
    )
    if (
        latitude is None
        or longitude is None
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        _warn(warnings, "invalid_position")
        return None
    return ParsedTCXPosition(
        timestamp=timestamp,
        latitude_degrees=latitude,
        longitude_degrees=longitude,
        altitude_meters=altitude,
        distance_meters=distance,
    )


def _lap_duration(lap: _ParsedLap, *, warnings: list[str]) -> float | None:
    if lap.duration_summary is not None:
        return lap.duration_summary
    if len(lap.trackpoint_times) < 2:
        return None
    _warn(warnings, "duration_derived_from_timestamps")
    return (max(lap.trackpoint_times) - min(lap.trackpoint_times)).total_seconds()


def _lap_distance(lap: _ParsedLap, *, warnings: list[str]) -> float | None:
    if lap.distance_summary is not None:
        return lap.distance_summary
    if not lap.trackpoint_distances:
        return None
    _warn(warnings, "distance_from_trackpoints")
    return max(lap.trackpoint_distances)


def _heart_rate_summary(
    laps: Sequence[_ParsedLap],
    *,
    resolved_durations: Sequence[float | None],
    warnings: list[str],
) -> tuple[float | None, float | None, int]:
    samples = [sample for lap in laps for sample in lap.heart_rate_samples]
    if samples:
        return (
            sum(sample.value for sample in samples) / len(samples),
            max(sample.value for sample in samples),
            len(samples),
        )

    averages = [lap.average_heart_rate_summary for lap in laps]
    average = _weighted_complete_average(
        averages,
        weights=resolved_durations,
        warning="average_heart_rate_unavailable_for_incomplete_laps",
        warnings=warnings,
    )
    maximum_values = [
        lap.max_heart_rate_summary
        for lap in laps
        if lap.max_heart_rate_summary is not None
    ]
    maximum = max(maximum_values) if maximum_values else None
    return average, maximum, 0


def _cadence_summary(
    laps: Sequence[_ParsedLap],
    *,
    resolved_durations: Sequence[float | None],
    warnings: list[str],
) -> tuple[float | None, int, float | None]:
    samples = [sample for lap in laps for sample in lap.cadence_samples]
    if samples:
        return sum(samples) / len(samples), len(samples), max(samples)
    summaries = [lap.cadence_summary for lap in laps]
    return (
        _weighted_complete_average(
            summaries,
            weights=resolved_durations,
            warning="average_cadence_unavailable_for_incomplete_laps",
            warnings=warnings,
        ),
        0,
        None,
    )


def _elevation_summary(
    laps: Sequence[_ParsedLap],
) -> tuple[float | None, float | None, float | None, float | None]:
    segments = [segment for lap in laps for segment in lap.altitude_segments]
    samples = [altitude for segment in segments for altitude in segment]
    if not samples:
        return None, None, None, None
    has_pair = any(len(segment) >= 2 for segment in segments)
    gain = (
        sum(
            max(current - previous, 0.0)
            for segment in segments
            for previous, current in pairwise(segment)
        )
        if has_pair
        else None
    )
    loss = (
        sum(
            max(previous - current, 0.0)
            for segment in segments
            for previous, current in pairwise(segment)
        )
        if has_pair
        else None
    )
    return gain, loss, min(samples), max(samples)


def _weighted_complete_average(
    values: Sequence[float | None],
    *,
    weights: Sequence[float | None],
    warning: str,
    warnings: list[str],
) -> float | None:
    if not values or any(value is None for value in values):
        if any(value is not None for value in values):
            _warn(warnings, warning)
        return None
    complete_values = [value for value in values if value is not None]
    if len(complete_values) == 1:
        return complete_values[0]
    if len(weights) != len(complete_values) or any(
        weight is None or weight <= 0 for weight in weights
    ):
        _warn(warnings, warning)
        return None
    complete_weights = [weight for weight in weights if weight is not None]
    return sum(
        value * weight
        for value, weight in zip(
            complete_values,
            complete_weights,
            strict=True,
        )
    ) / sum(complete_weights)


def _complete_sum(values: Sequence[float | None]) -> float | None:
    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _heart_rate_value(
    container: ET.Element | None,
    *,
    warnings: list[str],
) -> float | None:
    if container is None:
        return None
    value = _finite_float(
        _direct_text(container, "Value"),
        "invalid_heart_rate",
        warnings,
    )
    if value is None:
        return None
    if value <= 0:
        _warn(warnings, "invalid_heart_rate")
        return None
    return value


def _finite_float(
    value: str | None,
    warning: str,
    warnings: list[str],
) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        _warn(warnings, warning)
        return None
    if not math.isfinite(parsed):
        _warn(warnings, warning)
        return None
    return parsed


def _nonnegative_float(
    value: str | None,
    warning: str,
    warnings: list[str],
) -> float | None:
    parsed = _finite_float(value, warning, warnings)
    if parsed is not None and parsed < 0:
        _warn(warnings, warning)
        return None
    return parsed


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith(("Z", "z")):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _discipline(source_sport_type: str) -> Discipline:
    normalized = _DATED_SPORT_PREFIX.sub("", source_sport_type).strip().casefold()
    if "running" in normalized:
        return Discipline.RUNNING
    if "cycling" in normalized or "biking" in normalized:
        return Discipline.CYCLING
    if "swimming" in normalized:
        return Discipline.SWIMMING
    if "walking" in normalized or "hiking" in normalized:
        return Discipline.HIKING
    if "strength" in normalized:
        return Discipline.STRENGTH
    return Discipline.OTHER


def _stable_hash(*values: str) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _direct_children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local_name(child.tag) == name]


def _direct_child(element: ET.Element, name: str) -> ET.Element | None:
    return next(
        (child for child in element if _local_name(child.tag) == name),
        None,
    )


def _direct_text(element: ET.Element, name: str) -> str | None:
    child = _direct_child(element, name)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _first_descendant_text(
    element: ET.Element,
    *,
    names: set[str],
) -> str | None:
    for descendant in element.iter():
        if (
            _local_name(descendant.tag) in names
            and descendant.text is not None
            and descendant.text.strip()
        ):
            return descendant.text.strip()
    return None


def _namespace(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _warn(warnings: list[str], code: str) -> None:
    if code not in warnings:
        warnings.append(code)


__all__ = [
    "TCXParser",
    "TCXParserError",
    "TCXParserLimits",
]
