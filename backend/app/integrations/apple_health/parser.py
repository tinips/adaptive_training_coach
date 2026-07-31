"""Secure, streaming parser for Apple Health export ZIP archives."""

from __future__ import annotations

import hashlib
import math
import re
import stat
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import IO

from app.domain.enums import Discipline, HeartRateTemporalQuality
from app.integrations.apple_health.models import (
    ParsedAppleHealthExport,
    ParsedHeartRateObservation,
    ParsedWorkout,
    SwimmingEnvironmentHint,
)

HEART_RATE_TYPE = "HKQuantityTypeIdentifierHeartRate"
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_NESTED_ARCHIVE_SUFFIXES = {
    ".zip",
    ".7z",
    ".rar",
    ".tar",
    ".tgz",
    ".gz",
    ".bz2",
    ".xz",
}
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class AppleHealthParserError(ValueError):
    """Safe parser failure with a stable code suitable for user messaging."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(slots=True, frozen=True)
class AppleHealthArchiveLimits:
    """Resource ceilings applied before any XML parsing begins."""

    max_compressed_bytes: int
    max_uncompressed_bytes: int
    max_members: int
    max_compression_ratio: float


@dataclass(slots=True, frozen=True)
class _ValidatedArchive:
    archive_path: Path
    health_data_member: str


@dataclass(slots=True)
class _WorkoutBuilder:
    attributes: dict[str, str]
    statistics: list[dict[str, str]]
    metadata: dict[str, str]


class _SecureXMLReader:
    """Block external definitions and entity declarations while streaming."""

    def __init__(self, stream: IO[bytes]) -> None:
        self._stream = stream
        self._prolog = True
        self._tail = b""
        self._doctype_seen = False

    def read(self, size: int = -1) -> bytes:
        # ElementTree asks for fixed-size chunks. Keeping this bounded also
        # prevents an accidental consumer from turning read(-1) into a full
        # in-memory XML load.
        chunk = self._stream.read(65_536 if size < 0 else size)
        if b"\x00" in chunk:
            # Reject multibyte XML encodings rather than let their NUL-padded
            # declarations bypass the byte-level DTD/entity policy.
            raise AppleHealthParserError("unsafe_xml_encoding")
        if self._prolog and chunk:
            combined = self._tail + chunk
            upper = combined.upper()
            root_index = upper.find(b"<HEALTHDATA")
            namespace_root_index = upper.find(b":HEALTHDATA")
            if root_index < 0 or (
                namespace_root_index >= 0 and namespace_root_index < root_index
            ):
                root_index = namespace_root_index
            prolog = upper if root_index < 0 else upper[:root_index]
            if b"<!ENTITY" in prolog:
                raise AppleHealthParserError("unsafe_xml_entity")
            if b"<!DOCTYPE" in prolog:
                self._doctype_seen = True
            if self._doctype_seen and (b"SYSTEM" in prolog or b"PUBLIC" in prolog):
                raise AppleHealthParserError("unsafe_external_dtd")
            if root_index >= 0:
                self._prolog = False
                self._tail = b""
            else:
                self._tail = combined[-64:]
        return chunk


class AppleHealthParser:
    """Validate an archive and parse only workouts and relevant heart rate."""

    def __init__(self, limits: AppleHealthArchiveLimits) -> None:
        self._limits = limits

    @property
    def max_compressed_bytes(self) -> int:
        return self._limits.max_compressed_bytes

    def validate(self, archive_path: Path) -> str:
        """Validate ZIP structure and return the discovered HealthData member."""

        validated = self._validate_archive(archive_path)
        return validated.health_data_member

    def read_workouts(
        self,
        archive_path: Path,
        health_data_member: str,
    ) -> tuple[list[ParsedWorkout], int, int, list[str]]:
        """First streaming pass: normalize workouts and workout statistics."""

        warnings: list[str] = []
        unique: dict[str, ParsedWorkout] = {}
        found = 0
        duplicate_count = 0
        builder: _WorkoutBuilder | None = None
        workout_depth: int | None = None

        try:
            with zipfile.ZipFile(archive_path) as archive:
                with archive.open(health_data_member) as member:
                    reader = _SecureXMLReader(member)
                    root: ET.Element | None = None
                    for event, element in ET.iterparse(
                        reader,
                        events=("start", "end"),
                    ):
                        tag = _local_name(element.tag)
                        if root is None and event == "start":
                            root = element
                            if tag != "HealthData":
                                raise AppleHealthParserError(
                                    "health_data_xml_not_found"
                                )
                            continue
                        if event == "start" and tag == "Workout":
                            builder = _WorkoutBuilder(
                                attributes=dict(element.attrib),
                                statistics=[],
                                metadata={},
                            )
                            workout_depth = 0
                        elif event == "start" and builder is not None:
                            if workout_depth is None:
                                raise RuntimeError("workout depth is unavailable")
                            workout_depth += 1
                        elif (
                            event == "end"
                            and tag == "WorkoutStatistics"
                            and builder is not None
                        ):
                            builder.statistics.append(dict(element.attrib))
                            element.clear()
                        elif (
                            event == "end"
                            and tag == "MetadataEntry"
                            and builder is not None
                            and workout_depth == 1
                        ):
                            key = element.attrib.get("key", "").strip()
                            value = element.attrib.get("value")
                            if key and value is not None:
                                builder.metadata[key] = value
                            element.clear()
                        elif event == "end" and tag == "Workout":
                            found += 1
                            if builder is not None:
                                workout = self._normalize_workout(builder, warnings)
                                if workout.source_record_key in unique:
                                    duplicate_count += 1
                                else:
                                    unique[workout.source_record_key] = workout
                            builder = None
                            workout_depth = None
                            element.clear()
                            if root is not None:
                                root.clear()
                        elif event == "end" and tag in {"Record", "Correlation"}:
                            element.clear()
                            if root is not None:
                                root.clear()
                        if event == "end" and builder is not None and tag != "Workout":
                            if workout_depth is None or workout_depth < 1:
                                raise RuntimeError("invalid workout element depth")
                            workout_depth -= 1
        except AppleHealthParserError:
            raise
        except (ET.ParseError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise AppleHealthParserError("malformed_health_data_xml") from exc
        return list(unique.values()), found, duplicate_count, warnings

    def read_heart_rate(
        self,
        archive_path: Path,
        health_data_member: str,
        workouts: list[ParsedWorkout],
    ) -> int:
        """Second streaming pass: match only heart-rate records to workouts."""

        matched_keys: set[str] = set()
        matched_count = 0
        try:
            with zipfile.ZipFile(archive_path) as archive:
                with archive.open(health_data_member) as member:
                    reader = _SecureXMLReader(member)
                    root: ET.Element | None = None
                    for event, element in ET.iterparse(
                        reader,
                        events=("start", "end"),
                    ):
                        tag = _local_name(element.tag)
                        if root is None and event == "start":
                            root = element
                            if tag != "HealthData":
                                raise AppleHealthParserError(
                                    "health_data_xml_not_found"
                                )
                            continue
                        if event != "end":
                            continue
                        if (
                            tag == "Record"
                            and element.attrib.get("type") == HEART_RATE_TYPE
                        ):
                            observation = self._normalize_heart_rate(element.attrib)
                            if (
                                observation is not None
                                and observation.source_record_key not in matched_keys
                            ):
                                workout = _match_workout(observation, workouts)
                                if workout is not None:
                                    workout.observations.append(observation)
                                    matched_keys.add(observation.source_record_key)
                                    matched_count += 1
                        if tag in {"Record", "Correlation", "Workout"}:
                            element.clear()
                            if root is not None:
                                root.clear()
        except AppleHealthParserError:
            raise
        except (ET.ParseError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise AppleHealthParserError("malformed_health_data_xml") from exc

        for workout in workouts:
            _summarize_heart_rate(workout)
        return matched_count

    def parse(self, archive_path: Path) -> ParsedAppleHealthExport:
        """Convenience API executing validation and both bounded passes."""

        member = self.validate(archive_path)
        workouts, found, duplicates, warnings = self.read_workouts(
            archive_path,
            member,
        )
        matched = self.read_heart_rate(archive_path, member, workouts)
        return ParsedAppleHealthExport(
            workouts=tuple(workouts),
            workouts_found=found,
            duplicate_workouts=duplicates,
            heart_rate_records_matched=matched,
            warnings=tuple(warnings),
        )

    def _validate_archive(self, archive_path: Path) -> _ValidatedArchive:
        try:
            compressed_size = archive_path.stat().st_size
        except OSError as exc:
            raise AppleHealthParserError("archive_unavailable") from exc
        if compressed_size > self._limits.max_compressed_bytes:
            raise AppleHealthParserError("archive_compressed_size_exceeded")
        try:
            with archive_path.open("rb") as stream:
                if stream.read(4) not in _ZIP_MAGICS:
                    raise AppleHealthParserError("archive_not_zip")
            with zipfile.ZipFile(archive_path) as archive:
                members = archive.infolist()
                if not members:
                    raise AppleHealthParserError("archive_empty")
                if len(members) > self._limits.max_members:
                    raise AppleHealthParserError("archive_member_limit_exceeded")

                total_size = 0
                seen_names: dict[str, tuple[int, int, int]] = {}
                for member in members:
                    self._validate_member(member)
                    total_size += member.file_size
                    if total_size > self._limits.max_uncompressed_bytes:
                        raise AppleHealthParserError(
                            "archive_uncompressed_size_exceeded"
                        )
                    if member.file_size:
                        if member.compress_size == 0:
                            raise AppleHealthParserError(
                                "archive_compression_ratio_exceeded"
                            )
                        ratio = member.file_size / member.compress_size
                        if ratio > self._limits.max_compression_ratio:
                            raise AppleHealthParserError(
                                "archive_compression_ratio_exceeded"
                            )
                    normalized_name = _normalized_member_name(member.filename)
                    signature = (member.CRC, member.file_size, member.compress_size)
                    previous = seen_names.get(normalized_name)
                    if previous is not None and previous != signature:
                        raise AppleHealthParserError(
                            "archive_conflicting_duplicate_member"
                        )
                    seen_names[normalized_name] = signature
                    if (
                        not member.is_dir()
                        and Path(normalized_name).suffix.casefold()
                        in _NESTED_ARCHIVE_SUFFIXES
                    ):
                        raise AppleHealthParserError("nested_archive_not_allowed")

                for member in members:
                    if member.is_dir():
                        continue
                    normalized_name = _normalized_member_name(member.filename)
                    with archive.open(member) as stream:
                        member_magic = stream.read(4)
                    if member_magic in _ZIP_MAGICS:
                        raise AppleHealthParserError("nested_archive_not_allowed")
                    if PurePosixPath(normalized_name).name.casefold() == (
                        "export_cda.xml"
                    ):
                        continue
                    if not normalized_name.casefold().endswith(".xml"):
                        continue
                    if self._root_name(archive, member) == "HealthData":
                        return _ValidatedArchive(
                            archive_path=archive_path,
                            health_data_member=member.filename,
                        )
        except AppleHealthParserError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise AppleHealthParserError("invalid_archive") from exc
        raise AppleHealthParserError("health_data_xml_not_found")

    @staticmethod
    def _validate_member(member: zipfile.ZipInfo) -> None:
        name = member.filename
        normalized_slashes = name.replace("\\", "/")
        if (
            not name
            or normalized_slashes.startswith("/")
            or _WINDOWS_DRIVE.match(normalized_slashes)
            or ".." in PurePosixPath(normalized_slashes).parts
        ):
            raise AppleHealthParserError("unsafe_archive_member_path")
        unix_mode = member.external_attr >> 16
        if unix_mode and stat.S_ISLNK(unix_mode):
            raise AppleHealthParserError("archive_symlink_not_allowed")
        if member.flag_bits & 0x1:
            raise AppleHealthParserError("encrypted_archive_not_allowed")

    @staticmethod
    def _root_name(archive: zipfile.ZipFile, member: zipfile.ZipInfo) -> str | None:
        try:
            with archive.open(member) as stream:
                reader = _SecureXMLReader(stream)
                for event, element in ET.iterparse(reader, events=("start",)):
                    del event
                    return _local_name(element.tag)
        except AppleHealthParserError:
            raise
        except (ET.ParseError, OSError, RuntimeError):
            return None
        return None

    def _normalize_workout(
        self,
        builder: _WorkoutBuilder,
        warnings: list[str],
    ) -> ParsedWorkout:
        attributes = builder.attributes
        source_type = _required(attributes, "workoutActivityType")
        started_at = _parse_datetime(_required(attributes, "startDate"))
        ended_at = _parse_datetime(_required(attributes, "endDate"))
        if ended_at < started_at:
            raise AppleHealthParserError("workout_time_invalid")
        duration = _duration_seconds(
            _required(attributes, "duration"),
            _required(attributes, "durationUnit"),
        )
        distance = _optional_quantity(
            attributes.get("totalDistance"),
            attributes.get("totalDistanceUnit"),
            kind="distance",
            warnings=warnings,
        )
        calories = _optional_quantity(
            attributes.get("totalEnergyBurned"),
            attributes.get("totalEnergyBurnedUnit"),
            kind="energy",
            warnings=warnings,
        )
        for statistic in builder.statistics:
            statistic_type = statistic.get("type", "")
            if distance is None and "Distance" in statistic_type:
                distance = _optional_quantity(
                    statistic.get("sum"),
                    statistic.get("unit"),
                    kind="distance",
                    warnings=warnings,
                )
            if calories is None and "ActiveEnergyBurned" in statistic_type:
                calories = _optional_quantity(
                    statistic.get("sum"),
                    statistic.get("unit"),
                    kind="energy",
                    warnings=warnings,
                )
        source_name = attributes.get("sourceName")
        discipline = _discipline(source_type)
        source_metadata = dict(builder.metadata)
        source_key = _stable_hash(
            source_type,
            started_at.isoformat(),
            ended_at.isoformat(),
            str(duration),
            source_name or "",
        )
        return ParsedWorkout(
            source_record_key=source_key,
            source_workout_type=source_type,
            discipline=discipline,
            source_name=source_name,
            source_version=attributes.get("sourceVersion"),
            device=attributes.get("device"),
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=duration,
            distance_meters=distance,
            calories_kcal=calories,
            source_metadata=source_metadata,
            raw_sub_sport=_raw_sub_sport(source_metadata),
            swimming_environment=_swimming_environment(
                discipline,
                source_metadata,
            ),
            pool_length_meters=_pool_length_meters(
                source_metadata,
                warnings=warnings,
            ),
        )

    @staticmethod
    def _normalize_heart_rate(
        attributes: dict[str, str],
    ) -> ParsedHeartRateObservation | None:
        unit = attributes.get("unit")
        if unit not in {"count/min", "count/minute", "bpm"}:
            return None
        try:
            value = float(_required(attributes, "value"))
        except ValueError:
            return None
        if not math.isfinite(value) or value <= 0:
            return None
        started_at = _parse_datetime(_required(attributes, "startDate"))
        ended_at = _parse_datetime(_required(attributes, "endDate"))
        interval = (ended_at - started_at).total_seconds()
        if interval < 0:
            quality = HeartRateTemporalQuality.UNKNOWN
        elif interval == 0:
            quality = HeartRateTemporalQuality.EXACT_SAMPLE
        elif interval <= 60:
            quality = HeartRateTemporalQuality.SHORT_INTERVAL
        else:
            quality = HeartRateTemporalQuality.COARSE_INTERVAL
        source_name = attributes.get("sourceName")
        record_key = _stable_hash(
            HEART_RATE_TYPE,
            source_name or "",
            started_at.isoformat(),
            ended_at.isoformat(),
            format(value, ".12g"),
        )
        return ParsedHeartRateObservation(
            source_record_key=record_key,
            source_name=source_name,
            started_at=started_at,
            ended_at=ended_at,
            beats_per_minute=value,
            temporal_quality=quality,
        )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _normalized_member_name(name: str) -> str:
    return unicodedata.normalize("NFC", name.replace("\\", "/")).casefold()


def _required(attributes: dict[str, str], key: str) -> str:
    value = attributes.get(key)
    if value is None or not value.strip():
        raise AppleHealthParserError("required_workout_field_missing")
    return value


def _parse_datetime(value: str) -> datetime:
    formats = (
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S.%f %z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
    )
    for date_format in formats:
        try:
            parsed = datetime.strptime(value, date_format)
            return parsed.astimezone(UTC)
        except ValueError:
            continue
    raise AppleHealthParserError("invalid_health_timestamp")


def _duration_seconds(value: str, unit: str) -> int:
    try:
        numeric = float(value)
    except ValueError as exc:
        raise AppleHealthParserError("invalid_workout_duration") from exc
    if not math.isfinite(numeric):
        raise AppleHealthParserError("invalid_workout_duration")
    factors = {
        "s": 1.0,
        "sec": 1.0,
        "second": 1.0,
        "seconds": 1.0,
        "min": 60.0,
        "minute": 60.0,
        "minutes": 60.0,
        "h": 3600.0,
        "hr": 3600.0,
        "hour": 3600.0,
        "hours": 3600.0,
    }
    factor = factors.get(unit.strip().casefold())
    if factor is None:
        raise AppleHealthParserError("unsupported_duration_unit")
    seconds = round(numeric * factor)
    if seconds < 0:
        raise AppleHealthParserError("invalid_workout_duration")
    return seconds


def _optional_quantity(
    value: str | None,
    unit: str | None,
    *,
    kind: str,
    warnings: list[str],
) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except ValueError:
        warnings.append(f"invalid_{kind}_value")
        return None
    if not math.isfinite(numeric):
        warnings.append(f"invalid_{kind}_value")
        return None
    normalized_unit = (unit or "").strip().casefold()
    factors = {
        "distance": {
            "m": 1.0,
            "meter": 1.0,
            "meters": 1.0,
            "km": 1000.0,
            "kilometer": 1000.0,
            "kilometers": 1000.0,
            "mi": 1609.344,
            "mile": 1609.344,
            "miles": 1609.344,
        },
        "energy": {
            "kcal": 1.0,
            "cal": 0.001,
            "kj": 0.239005736,
        },
    }
    factor = factors[kind].get(normalized_unit)
    if factor is None:
        warnings.append(f"unsupported_{kind}_unit")
        return None
    normalized = numeric * factor
    if not math.isfinite(normalized) or normalized < 0:
        warnings.append(f"invalid_{kind}_value")
        return None
    return normalized


def _discipline(source_type: str) -> Discipline:
    token = source_type.rsplit("Identifier", 1)[-1].casefold()
    if "running" in token:
        return Discipline.RUNNING
    if "cycling" in token:
        return Discipline.CYCLING
    if "swimming" in token:
        return Discipline.SWIMMING
    if "walking" in token or "hiking" in token:
        return Discipline.HIKING
    if any(
        strength in token
        for strength in (
            "strengthtraining",
            "coretraining",
            "crosstraining",
        )
    ):
        return Discipline.STRENGTH
    return Discipline.OTHER


def _raw_sub_sport(metadata: dict[str, str]) -> str | None:
    for key in (
        "HKWorkoutSubActivityType",
        "HKWorkoutSubType",
        "SubSport",
    ):
        value = metadata.get(key)
        if value is not None and (normalized := value.strip()):
            return normalized
    return None


def _swimming_environment(
    discipline: Discipline,
    metadata: dict[str, str],
) -> SwimmingEnvironmentHint | None:
    if discipline is not Discipline.SWIMMING:
        return None
    value = metadata.get("HKSwimmingLocationType")
    if value is None:
        return None
    normalized = "".join(
        character for character in value.casefold() if character.isalnum()
    )
    if normalized in {
        "1",
        "pool",
        "poolswimming",
        "hkworkoutswimminglocationtypepool",
    }:
        return "POOL"
    if normalized in {
        "2",
        "openwater",
        "openwaterswimming",
        "hkworkoutswimminglocationtypeopenwater",
    }:
        return "OPEN_WATER"
    return None


def _pool_length_meters(
    metadata: dict[str, str],
    *,
    warnings: list[str],
) -> float | None:
    raw_value = metadata.get("HKLapLength")
    if raw_value is None:
        return None
    match = re.fullmatch(
        r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([A-Za-z]+)?\s*",
        raw_value,
    )
    if match is None:
        if "invalid_pool_length" not in warnings:
            warnings.append("invalid_pool_length")
        return None
    unit = match.group(2) or metadata.get("HKLapLengthUnit")
    return _optional_quantity(
        match.group(1),
        unit,
        kind="distance",
        warnings=warnings,
    )


def _stable_hash(*values: str) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _match_workout(
    observation: ParsedHeartRateObservation,
    workouts: list[ParsedWorkout],
) -> ParsedWorkout | None:
    candidates = [
        workout
        for workout in workouts
        if observation.started_at <= workout.ended_at
        and observation.ended_at >= workout.started_at
    ]
    if not candidates:
        return None
    same_source = [
        workout
        for workout in candidates
        if observation.source_name and workout.source_name == observation.source_name
    ]
    selected = same_source or candidates
    observation_midpoint = (
        observation.started_at + (observation.ended_at - observation.started_at) / 2
    )
    return min(
        selected,
        key=lambda workout: abs(
            (
                workout.started_at
                + (workout.ended_at - workout.started_at) / 2
                - observation_midpoint
            ).total_seconds()
        ),
    )


def _summarize_heart_rate(workout: ParsedWorkout) -> None:
    precise = [
        item
        for item in workout.observations
        if item.temporal_quality
        in {
            HeartRateTemporalQuality.EXACT_SAMPLE,
            HeartRateTemporalQuality.SHORT_INTERVAL,
        }
    ]
    if workout.observations:
        workout.max_heart_rate = max(
            item.beats_per_minute for item in workout.observations
        )
    if precise:
        workout.average_heart_rate = sum(
            item.beats_per_minute for item in precise
        ) / len(precise)
