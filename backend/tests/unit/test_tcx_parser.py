"""Focused normalization and security tests for TCX workout files."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.domain.enums import Discipline, HeartRateTemporalQuality
from app.integrations.tcx import (
    TCXParser,
    TCXParserError,
    TCXParserLimits,
)

V1 = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v1"
V2 = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"


def parser(*, max_bytes: int = 1_000_000) -> TCXParser:
    return TCXParser(TCXParserLimits(max_bytes=max_bytes))


def write_tcx(path: Path, xml: str) -> Path:
    path.write_text(xml, encoding="utf-8")
    return path


def document(
    activity: str,
    *,
    namespace: str = V2,
) -> str:
    namespace_attribute = f' xmlns="{namespace}"' if namespace else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<TrainingCenterDatabase{namespace_attribute}>"
        f"<Activities>{activity}</Activities>"
        "</TrainingCenterDatabase>"
    )


def test_summary_only_multiple_laps_and_empty_track_are_preserved(
    tmp_path: Path,
) -> None:
    activity = """<Activity Sport="20260726Pool swimming">
      <Id>2026-07-26T08:00:00Z</Id>
      <Lap StartTime="2026-07-26T08:00:00Z">
        <TotalTimeSeconds>600</TotalTimeSeconds>
        <DistanceMeters>400</DistanceMeters>
        <Calories>80</Calories>
        <AverageHeartRateBpm><Value>120</Value></AverageHeartRateBpm>
        <MaximumHeartRateBpm><Value>140</Value></MaximumHeartRateBpm>
        <Cadence>30</Cadence>
        <Track/>
      </Lap>
      <Lap StartTime="2026-07-26T08:10:00Z">
        <TotalTimeSeconds>300</TotalTimeSeconds>
        <DistanceMeters>200</DistanceMeters>
        <Calories>40</Calories>
        <AverageHeartRateBpm><Value>150</Value></AverageHeartRateBpm>
        <MaximumHeartRateBpm><Value>160</Value></MaximumHeartRateBpm>
        <Cadence>36</Cadence>
      </Lap>
    </Activity>"""
    path = write_tcx(tmp_path / "summary.tcx", document(activity, namespace=""))

    result = parser().parse(path)

    assert result.discipline is Discipline.SWIM
    assert result.source_sport_type == "20260726Pool swimming"
    assert result.started_at.isoformat() == "2026-07-26T08:00:00+00:00"
    assert result.ended_at is None
    assert result.duration_seconds == 900
    assert result.distance_meters == 600
    assert result.calories_kcal == 120
    assert result.average_heart_rate == 130
    assert result.max_heart_rate == 160
    assert result.heart_rate_sample_count == 0
    assert result.heart_rate_quality is HeartRateTemporalQuality.UNKNOWN
    assert result.heart_rate_reliable is True
    assert result.heart_rate_provenance == "PROVIDER_SUMMARY"
    assert result.average_cadence == 32
    assert result.cadence_sample_count == 0
    assert result.elevation_gain_meters is None
    assert result.route_positions == ()
    assert result.warnings == ()


def test_trackpoints_supply_measured_hr_cadence_route_and_derived_metrics(
    tmp_path: Path,
) -> None:
    activity = """<Activity Sport="Running">
      <Id>2026-07-27T06:00:00+00:00</Id>
      <Lap StartTime="2026-07-27T06:00:00Z">
        <Track>
          <Trackpoint>
            <Time>2026-07-27T06:00:00Z</Time>
            <Position>
              <LatitudeDegrees>41.4</LatitudeDegrees>
              <LongitudeDegrees>2.1</LongitudeDegrees>
            </Position>
            <AltitudeMeters>10</AltitudeMeters>
            <DistanceMeters>0</DistanceMeters>
            <HeartRateBpm><Value>140</Value></HeartRateBpm>
            <Cadence>80</Cadence>
          </Trackpoint>
          <Trackpoint>
            <Time>2026-07-27T06:05:00Z</Time>
            <Position>
              <LatitudeDegrees>41.41</LatitudeDegrees>
              <LongitudeDegrees>2.11</LongitudeDegrees>
            </Position>
            <AltitudeMeters>25</AltitudeMeters>
            <DistanceMeters>1000</DistanceMeters>
            <HeartRateBpm><Value>160</Value></HeartRateBpm>
            <Extensions><TPX><RunCadence>90</RunCadence></TPX></Extensions>
          </Trackpoint>
          <Trackpoint>
            <Time>2026-07-27T06:10:00Z</Time>
            <AltitudeMeters>20</AltitudeMeters>
            <DistanceMeters>2000</DistanceMeters>
            <HeartRateBpm><Value>150</Value></HeartRateBpm>
          </Trackpoint>
        </Track>
      </Lap>
    </Activity>"""
    path = write_tcx(tmp_path / "trackpoints.tcx", document(activity))

    result = parser().parse(path)

    assert result.discipline is Discipline.RUN
    assert result.duration_seconds == 600
    assert result.distance_meters == 2000
    assert result.ended_at.isoformat() == "2026-07-27T06:10:00+00:00"
    assert result.average_heart_rate == 150
    assert result.max_heart_rate == 160
    assert result.heart_rate_sample_count == 3
    assert result.heart_rate_quality is HeartRateTemporalQuality.EXACT_SAMPLE
    assert result.heart_rate_reliable is True
    assert result.heart_rate_provenance == "MEASURED_SENSOR"
    assert result.average_cadence == 85
    assert result.cadence_sample_count == 2
    assert result.elevation_gain_meters == 15
    assert result.minimum_altitude_meters == 10
    assert result.maximum_altitude_meters == 25
    assert len(result.route_positions) == 2
    assert isinstance(result.route_positions, tuple)
    assert result.route_positions[0].latitude_degrees == 41.4
    assert result.route_positions[1].distance_meters == 1000
    with pytest.raises(FrozenInstanceError):
        result.route_positions[0].latitude_degrees = 0  # type: ignore[misc]
    assert result.warnings == (
        "duration_derived_from_timestamps",
        "distance_from_trackpoints",
    )


def test_trackpoint_hr_without_timestamp_is_measured_but_not_reliable(
    tmp_path: Path,
) -> None:
    activity = """<Activity Sport="Biking">
      <Id>2026-07-27T06:00:00Z</Id>
      <Lap StartTime="2026-07-27T06:00:00Z">
        <TotalTimeSeconds>60</TotalTimeSeconds>
        <Track><Trackpoint>
          <HeartRateBpm><Value>155</Value></HeartRateBpm>
        </Trackpoint></Track>
      </Lap>
    </Activity>"""
    result = parser().parse(write_tcx(tmp_path / "untimed-hr.tcx", document(activity)))

    assert result.discipline is Discipline.RIDE
    assert result.average_heart_rate == 155
    assert result.heart_rate_provenance == "MEASURED_SENSOR"
    assert result.heart_rate_quality is HeartRateTemporalQuality.UNKNOWN
    assert result.heart_rate_reliable is False


@pytest.mark.parametrize(
    ("sport", "discipline"),
    [
        ("Running", Discipline.RUN),
        ("20260728Outdoor cycling", Discipline.RIDE),
        ("Biking", Discipline.RIDE),
        ("Pool swimming", Discipline.SWIM),
        ("Swimming", Discipline.SWIM),
        ("Walking", Discipline.WALK_HIKE),
        ("Hiking", Discipline.WALK_HIKE),
        ("Strength", Discipline.STRENGTH),
        ("Unicycle", Discipline.OTHER),
    ],
)
@pytest.mark.parametrize("namespace", [V1, V2, ""])
def test_garmin_namespaces_and_sport_normalization(
    tmp_path: Path,
    sport: str,
    discipline: Discipline,
    namespace: str,
) -> None:
    activity = f"""<Activity Sport="{sport}">
      <Id>2026-07-28T07:00:00Z</Id>
      <Lap StartTime="2026-07-28T07:00:00Z">
        <TotalTimeSeconds>1</TotalTimeSeconds>
      </Lap>
    </Activity>"""
    path = write_tcx(
        tmp_path / f"{discipline.value}-{len(namespace)}.tcx",
        document(activity, namespace=namespace),
    )

    assert parser().parse(path).discipline is discipline


def test_missing_optional_metrics_remain_unavailable(tmp_path: Path) -> None:
    activity = """<Activity>
      <Id>2026-07-28T07:00:00Z</Id>
      <Lap StartTime="2026-07-28T07:00:00Z"><Track/></Lap>
    </Activity>"""

    result = parser().parse(write_tcx(tmp_path / "minimal.tcx", document(activity)))

    assert result.source_sport_type == "Unknown"
    assert result.discipline is Discipline.OTHER
    assert result.duration_seconds is None
    assert result.distance_meters is None
    assert result.calories_kcal is None
    assert result.average_heart_rate is None
    assert result.max_heart_rate is None
    assert result.heart_rate_provenance == "UNAVAILABLE"
    assert result.heart_rate_reliable is False
    assert result.average_cadence is None
    assert result.route_positions == ()


def test_source_key_is_stable_across_metric_enrichment(tmp_path: Path) -> None:
    base = """<Activity Sport="Running">
      <Id>2026-07-28T07:00:00Z</Id>
      <Lap StartTime="2026-07-28T07:00:00Z">{content}</Lap>
    </Activity>"""
    first = parser().parse(
        write_tcx(
            tmp_path / "first.tcx",
            document(base.format(content="<TotalTimeSeconds>60</TotalTimeSeconds>")),
        )
    )
    second = parser().parse(
        write_tcx(
            tmp_path / "second.tcx",
            document(
                base.format(
                    content=(
                        "<TotalTimeSeconds>60</TotalTimeSeconds>"
                        "<DistanceMeters>250</DistanceMeters>"
                        "<Calories>20</Calories>"
                    )
                )
            ),
        )
    )

    assert first.source_record_key == second.source_record_key
    assert len(first.source_record_key) == 64


@pytest.mark.parametrize(
    ("xml", "error_code"),
    [
        ("<NotTCX/>", "tcx_root_invalid"),
        (
            '<TrainingCenterDatabase xmlns="https://example.test/tcx">'
            "<Activities/></TrainingCenterDatabase>",
            "tcx_namespace_unsupported",
        ),
        (
            "<TrainingCenterDatabase><Activities/></TrainingCenterDatabase>",
            "tcx_activity_not_found",
        ),
        (
            "<TrainingCenterDatabase><Activities>"
            '<Activity Sport="Running"><Lap/></Activity>'
            '<Activity Sport="Biking"><Lap/></Activity>'
            "</Activities></TrainingCenterDatabase>",
            "tcx_multiple_activities_not_supported",
        ),
        (
            "<TrainingCenterDatabase><Activities>"
            '<Activity Sport="Running"><Id>2026-07-28T07:00:00Z</Id></Activity>'
            "</Activities></TrainingCenterDatabase>",
            "tcx_lap_not_found",
        ),
        ("<TrainingCenterDatabase>", "malformed_tcx_xml"),
    ],
)
def test_rejects_invalid_structure(
    tmp_path: Path,
    xml: str,
    error_code: str,
) -> None:
    path = write_tcx(tmp_path / "invalid.tcx", xml)

    with pytest.raises(TCXParserError, match=error_code):
        parser().parse(path)


@pytest.mark.parametrize(
    ("declaration", "error_code"),
    [
        (
            '<!DOCTYPE TrainingCenterDatabase SYSTEM "https://attacker.test/x">',
            "unsafe_xml_doctype",
        ),
        (
            '<!DOCTYPE TrainingCenterDatabase [<!ENTITY x "expanded">]>',
            "unsafe_xml_doctype",
        ),
        ('<!ENTITY x "expanded">', "unsafe_xml_entity"),
    ],
)
def test_rejects_doctype_and_entity_declarations(
    tmp_path: Path,
    declaration: str,
    error_code: str,
) -> None:
    xml = (
        '<?xml version="1.0"?>'
        f"{declaration}"
        "<TrainingCenterDatabase><Activities/></TrainingCenterDatabase>"
    )
    path = write_tcx(tmp_path / "unsafe.tcx", xml)

    with pytest.raises(TCXParserError, match=error_code):
        parser().parse(path)


def test_rejects_utf16_before_entity_declarations_can_bypass_scanning(
    tmp_path: Path,
) -> None:
    xml = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE TrainingCenterDatabase [<!ENTITY x "expanded">]>'
        "<TrainingCenterDatabase><Activities>&x;</Activities>"
        "</TrainingCenterDatabase>"
    )
    path = tmp_path / "unsafe-utf16.tcx"
    path.write_bytes(xml.encode("utf-16"))

    with pytest.raises(TCXParserError, match="unsafe_xml_encoding"):
        parser().parse(path)


def test_enforces_actual_file_size_limit(tmp_path: Path) -> None:
    path = write_tcx(
        tmp_path / "large.tcx",
        "<TrainingCenterDatabase>" + (" " * 1000) + "</TrainingCenterDatabase>",
    )

    with pytest.raises(TCXParserError, match="tcx_file_size_exceeded"):
        parser(max_bytes=100).parse(path)


def test_identity_falls_back_to_lap_start_without_activity_id(
    tmp_path: Path,
) -> None:
    activity = """<Activity Sport="Walking">
      <Lap StartTime="2026-07-28T07:00:00Z">
        <TotalTimeSeconds>120</TotalTimeSeconds>
      </Lap>
    </Activity>"""

    result = parser().parse(write_tcx(tmp_path / "no-id.tcx", document(activity)))

    assert result.activity_id is None
    assert result.started_at.isoformat() == "2026-07-28T07:00:00+00:00"
    assert len(result.source_record_key) == 64
