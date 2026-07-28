"""Focused security and normalization tests for Apple Health exports."""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest

from app.domain.enums import Discipline, HeartRateTemporalQuality
from app.integrations.apple_health import (
    AppleHealthArchiveLimits,
    AppleHealthParser,
    AppleHealthParserError,
)


def parser(
    *,
    compressed: int = 1_000_000,
    uncompressed: int = 2_000_000,
    members: int = 20,
    ratio: float = 200,
) -> AppleHealthParser:
    return AppleHealthParser(
        AppleHealthArchiveLimits(
            max_compressed_bytes=compressed,
            max_uncompressed_bytes=uncompressed,
            max_members=members,
            max_compression_ratio=ratio,
        )
    )


def write_archive(
    path: Path,
    xml: str,
    *,
    name: str = "健康資料/健康匯出.xml",
) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("apple_health_export/export_cda.xml", "<Clinical/>")
        archive.writestr(name, xml)


def complete_xml(*, coarse: bool = False) -> str:
    heart_end = "2026-07-20 08:30:00 +0000" if coarse else "2026-07-20 08:00:00 +0000"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData [
  <!ELEMENT HealthData ANY>
  <!ELEMENT Workout ANY>
]>
<HealthData locale="en_US">
  <Record type="HKClinicalTypeIdentifierAllergyRecord"
          sourceName="Hospital" startDate="2026-07-20 00:00:00 +0000"
          endDate="2026-07-20 00:00:00 +0000" value="ignored"/>
  <Workout workoutActivityType="HKWorkoutActivityTypeRunning"
           duration="1" durationUnit="h" totalDistance="3.1"
           totalDistanceUnit="mi" totalEnergyBurned="418.4"
           totalEnergyBurnedUnit="kJ" sourceName="Athlete Watch"
           sourceVersion="11.0" device="Watch"
           startDate="2026-07-20 08:00:00 +0000"
           endDate="2026-07-20 09:00:00 +0000">
    <WorkoutStatistics type="HKQuantityTypeIdentifierDistanceWalkingRunning"
                       sum="5" unit="km"/>
  </Workout>
  <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Athlete Watch"
          unit="count/min" value="150"
          startDate="2026-07-20 08:00:00 +0000"
          endDate="{heart_end}"/>
</HealthData>"""


def test_discovers_unicode_healthdata_and_normalizes_supported_units(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "export.zip"
    write_archive(archive, complete_xml())

    result = parser().parse(archive)

    assert result.workouts_found == 1
    assert result.heart_rate_records_matched == 1
    assert result.warnings == ()
    workout = result.workouts[0]
    assert workout.discipline is Discipline.RUN
    assert workout.source_workout_type == "HKWorkoutActivityTypeRunning"
    assert workout.duration_seconds == 3600
    assert workout.distance_meters == pytest.approx(4988.9664)
    assert workout.calories_kcal == pytest.approx(100)
    assert workout.average_heart_rate == 150
    assert workout.max_heart_rate == 150
    assert workout.heart_rate_quality is HeartRateTemporalQuality.EXACT_SAMPLE
    assert workout.heart_rate_reliable is True


def test_coarse_heart_rate_is_preserved_without_fake_average(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "export.zip"
    write_archive(archive, complete_xml(coarse=True))

    workout = parser().parse(archive).workouts[0]

    assert workout.heart_rate_sample_count == 1
    assert workout.max_heart_rate == 150
    assert workout.average_heart_rate is None
    assert workout.heart_rate_quality is HeartRateTemporalQuality.COARSE_INTERVAL
    assert workout.heart_rate_reliable is False


@pytest.mark.parametrize(
    ("source_type", "discipline", "duration", "unit", "seconds"),
    [
        ("HKWorkoutActivityTypeRunning", Discipline.RUN, "90", "s", 90),
        ("HKWorkoutActivityTypeCycling", Discipline.RIDE, "2", "min", 120),
        ("HKWorkoutActivityTypeSwimming", Discipline.SWIM, "1", "h", 3600),
        (
            "HKWorkoutActivityTypeWalking",
            Discipline.WALK_HIKE,
            "2",
            "min",
            120,
        ),
        (
            "HKWorkoutActivityTypeHiking",
            Discipline.WALK_HIKE,
            "2",
            "min",
            120,
        ),
        (
            "HKWorkoutActivityTypeTraditionalStrengthTraining",
            Discipline.STRENGTH,
            "2",
            "min",
            120,
        ),
        ("HKWorkoutActivityTypeYoga", Discipline.OTHER, "2", "min", 120),
    ],
)
def test_maps_supported_sports_and_duration_units(
    tmp_path: Path,
    source_type: str,
    discipline: Discipline,
    duration: str,
    unit: str,
    seconds: int,
) -> None:
    archive = tmp_path / f"{discipline.value}.zip"
    write_archive(
        archive,
        f"""<HealthData><Workout workoutActivityType="{source_type}"
        duration="{duration}" durationUnit="{unit}" totalDistance="1000"
        totalDistanceUnit="m" totalEnergyBurned="100"
        totalEnergyBurnedUnit="kcal" sourceName="Watch"
        startDate="2026-07-20 08:00:00 +0000"
        endDate="2026-07-20 09:00:00 +0000"/></HealthData>""",
    )

    workout = parser().parse(archive).workouts[0]

    assert workout.discipline is discipline
    assert workout.duration_seconds == seconds
    assert workout.distance_meters == 1000
    assert workout.calories_kcal == 100


def test_heart_rate_matching_prefers_same_source_and_short_interval_is_reliable(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "matching.zip"
    xml = """<HealthData>
      <Workout workoutActivityType="HKWorkoutActivityTypeRunning"
        duration="60" durationUnit="min" sourceName="Phone"
        startDate="2026-07-20 08:00:00 +0000"
        endDate="2026-07-20 09:00:00 +0000"/>
      <Workout workoutActivityType="HKWorkoutActivityTypeCycling"
        duration="60" durationUnit="min" sourceName="Watch"
        startDate="2026-07-20 08:00:00 +0000"
        endDate="2026-07-20 09:00:00 +0000"/>
      <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Watch"
        unit="count/min" value="155"
        startDate="2026-07-20 08:10:00 +0000"
        endDate="2026-07-20 08:10:45 +0000"/>
    </HealthData>"""
    write_archive(archive, xml)

    result = parser().parse(archive)
    by_source = {workout.source_name: workout for workout in result.workouts}

    assert by_source["Phone"].heart_rate_sample_count == 0
    assert by_source["Watch"].heart_rate_sample_count == 1
    assert (
        by_source["Watch"].heart_rate_quality is HeartRateTemporalQuality.SHORT_INTERVAL
    )
    assert by_source["Watch"].average_heart_rate == 155


@pytest.mark.parametrize(
    ("member_name", "error_code"),
    [
        ("../export.xml", "unsafe_archive_member_path"),
        ("/absolute/export.xml", "unsafe_archive_member_path"),
        ("C:/export.xml", "unsafe_archive_member_path"),
    ],
)
def test_rejects_unsafe_member_paths(
    tmp_path: Path,
    member_name: str,
    error_code: str,
) -> None:
    archive = tmp_path / "unsafe.zip"
    write_archive(archive, "<HealthData/>", name=member_name)

    with pytest.raises(AppleHealthParserError, match=error_code):
        parser().validate(archive)


def test_rejects_symlink_encryption_nested_archives_and_conflicting_names(
    tmp_path: Path,
) -> None:
    symlink_archive = tmp_path / "symlink.zip"
    symlink = zipfile.ZipInfo("export.xml")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(symlink_archive, "w") as archive:
        archive.writestr(symlink, "target")
    with pytest.raises(AppleHealthParserError, match="archive_symlink_not_allowed"):
        parser().validate(symlink_archive)

    encrypted = zipfile.ZipInfo("export.xml")
    encrypted.flag_bits |= 0x1
    with pytest.raises(
        AppleHealthParserError,
        match="encrypted_archive_not_allowed",
    ):
        AppleHealthParser._validate_member(encrypted)

    nested_archive = tmp_path / "nested.zip"
    with zipfile.ZipFile(nested_archive, "w") as archive:
        archive.writestr("inside.zip", b"PK\x03\x04payload")
        archive.writestr("export.xml", "<HealthData/>")
    with pytest.raises(AppleHealthParserError, match="nested_archive_not_allowed"):
        parser().validate(nested_archive)

    duplicate_archive = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(duplicate_archive, "w") as archive:
        archive.writestr("export.xml", "<HealthData/>")
        archive.writestr("EXPORT.XML", "<HealthData locale='different'/>")
    with pytest.raises(
        AppleHealthParserError,
        match="archive_conflicting_duplicate_member",
    ):
        parser().validate(duplicate_archive)


def test_enforces_magic_size_member_and_ratio_limits(tmp_path: Path) -> None:
    not_zip = tmp_path / "not.zip"
    not_zip.write_bytes(b"not a ZIP")
    with pytest.raises(AppleHealthParserError, match="archive_not_zip"):
        parser().validate(not_zip)

    archive = tmp_path / "limits.zip"
    write_archive(archive, "<HealthData>" + (" " * 20_000) + "</HealthData>")
    with pytest.raises(
        AppleHealthParserError,
        match="archive_compressed_size_exceeded",
    ):
        parser(compressed=10).validate(archive)
    with pytest.raises(
        AppleHealthParserError,
        match="archive_uncompressed_size_exceeded",
    ):
        parser(uncompressed=100).validate(archive)
    with pytest.raises(
        AppleHealthParserError,
        match="archive_member_limit_exceeded",
    ):
        parser(members=1).validate(archive)
    with pytest.raises(
        AppleHealthParserError,
        match="archive_compression_ratio_exceeded",
    ):
        parser(ratio=2).validate(archive)


@pytest.mark.parametrize(
    ("declaration", "error_code"),
    [
        (
            '<!DOCTYPE HealthData SYSTEM "https://attacker.example/export.dtd">',
            "unsafe_external_dtd",
        ),
        (
            '<!DOCTYPE HealthData [<!ENTITY x "expanded">]>',
            "unsafe_xml_entity",
        ),
    ],
)
def test_rejects_external_dtd_and_entity_declarations(
    tmp_path: Path,
    declaration: str,
    error_code: str,
) -> None:
    archive = tmp_path / "unsafe-xml.zip"
    write_archive(
        archive,
        f'<?xml version="1.0"?>{declaration}<HealthData/>',
    )

    with pytest.raises(AppleHealthParserError, match=error_code):
        parser().validate(archive)


def test_rejects_unsupported_duration_unit_without_guessing(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "unsupported.zip"
    xml = complete_xml().replace('durationUnit="h"', 'durationUnit="fortnight"')
    write_archive(archive, xml)

    with pytest.raises(AppleHealthParserError, match="unsupported_duration_unit"):
        parser().parse(archive)
