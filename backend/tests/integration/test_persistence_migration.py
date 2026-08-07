"""Alembic smoke test from an empty portable database."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from app.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_initial_migration_upgrade_and_downgrade(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)  # type: ignore[attr-defined]
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "")  # type: ignore[attr-defined]
    get_settings.cache_clear()
    configuration = Config(str(BACKEND_ROOT / "alembic.ini"))

    command.upgrade(configuration, "head")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'",
            )
        }
        revision = connection.execute(
            "SELECT version_num FROM alembic_version",
        ).fetchone()
        active_sync_index = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'index' "
            "AND name = 'uq_strava_sync_jobs_active_user'",
        ).fetchone()
        active_apple_index = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'index' "
            "AND name = 'uq_apple_health_import_jobs_active_user'",
        ).fetchone()
        import_hash_index = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'index' "
            "AND name = 'ix_apple_health_import_jobs_user_file_sha256'",
        ).fetchone()
        import_job_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info('apple_health_import_jobs')"
            )
        }
        source_link_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('activity_source_links')")
        }
        training_goal_info = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info('training_goals')")
        }
        training_goal_columns = set(training_goal_info)
        athlete_profile_info = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info('athlete_profiles')")
        }
        athlete_profile_columns = set(athlete_profile_info)
        onboarding_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('onboarding_sessions')")
        }
        feedback_flow_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('workout_flow_sessions')")
        }

    assert revision == ("0013_add_athlete_profile_context",)
    assert len(tables - {"alembic_version"}) == 28
    assert {
        "activity_feedback",
        "activity_source_links",
        "cycling_workout_details",
        "hiking_workout_details",
        "other_workout_details",
        "pool_swimming_details",
        "running_workout_details",
        "strength_workout_details",
        "swimming_workout_details",
        "workouts",
        "workout_flow_sessions",
    }.issubset(tables)
    assert "activities" not in tables
    assert "heart_rate_observations" not in tables
    assert active_sync_index is not None
    assert "WHERE status IN" in active_sync_index[0]
    assert active_apple_index is not None
    assert "WHERE status IN" in active_apple_index[0]
    assert import_hash_index is not None
    assert "temporary_path" in import_job_columns
    assert {"context", "onboarding_session_id"}.isdisjoint(import_job_columns)
    assert {
        "pending_free_text_step",
        "pending_parsed_value",
        "return_to_summary",
        "completed_at",
    }.isdisjoint(onboarding_columns)
    assert "return_to_onboarding" not in feedback_flow_columns
    assert {
        "heart_rate_source",
        "heart_rate_quality",
        "heart_rate_reliable",
        "heart_rate_sample_count",
    }.isdisjoint(source_link_columns)
    assert {
        "main_goal",
        "target_outcome",
        "secondary_priority",
        "original_description",
        "status",
    }.issubset(training_goal_columns)
    assert {"goal_type", "event_name", "goal_priority"}.isdisjoint(
        training_goal_columns
    )
    assert all(
        training_goal_info[column][3] == 1
        for column in ("main_goal", "target_outcome", "original_description")
    )
    assert {
        "birth_year",
        "gender",
        "weight_kg",
        "height_cm",
    }.issubset(athlete_profile_columns)
    raw_context_columns = {
        "availability_text",
        "equipment_recommendation_text",
        "equipment_text",
        "health_limitations_text",
    }
    assert raw_context_columns.issubset(athlete_profile_columns)
    assert all(
        athlete_profile_info[column][2].upper() == "TEXT"
        and athlete_profile_info[column][3] == 0
        for column in raw_context_columns
    )
    assert "fitness_level" not in athlete_profile_columns

    command.downgrade(configuration, "0004_discipline_workout_models")
    with sqlite3.connect(database_path) as connection:
        restored_revision = connection.execute(
            "SELECT version_num FROM alembic_version",
        ).fetchone()
        restored_observation_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info('heart_rate_observations')"
            )
        }
        restored_observation_count = connection.execute(
            "SELECT COUNT(*) FROM heart_rate_observations",
        ).fetchone()
        restored_source_link_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('activity_source_links')")
        }
    assert restored_revision == ("0004_discipline_workout_models",)
    assert restored_observation_columns == {
        "user_id",
        "workout_id",
        "source_record_key",
        "source_name",
        "started_at",
        "ended_at",
        "beats_per_minute",
        "temporal_quality",
        "id",
        "created_at",
        "updated_at",
    }
    assert restored_observation_count == (0,)
    assert {
        "heart_rate_source",
        "heart_rate_quality",
        "heart_rate_reliable",
        "heart_rate_sample_count",
    }.issubset(restored_source_link_columns)

    command.upgrade(configuration, "head")
    with sqlite3.connect(database_path) as connection:
        reupgraded_revision = connection.execute(
            "SELECT version_num FROM alembic_version",
        ).fetchone()
        reupgraded_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'",
            )
        }
    assert reupgraded_revision == ("0013_add_athlete_profile_context",)
    assert "heart_rate_observations" not in reupgraded_tables

    command.downgrade(configuration, "base")
    with sqlite3.connect(database_path) as connection:
        remaining = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'",
            )
        }
    assert remaining == {"alembic_version"}
    get_settings.cache_clear()


def test_context_migration_keeps_existing_profiles_and_defaults_raw_fields_to_null(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    database_path = tmp_path / "profile-context-migration.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)  # type: ignore[attr-defined]
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "")  # type: ignore[attr-defined]
    get_settings.cache_clear()
    configuration = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(configuration, "0012_remove_fitness_level")

    user_id = "f1000000000000000000000000000000"
    profile_id = "f2000000000000000000000000000000"
    now = "2026-08-07 08:00:00+00:00"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO users "
            "(telegram_user_id, language_code, status, id, created_at, updated_at) "
            "VALUES (777001, 'en', 'ONBOARDING_COMPLETED', ?, ?, ?)",
            (user_id, now, now),
        )
        connection.execute(
            "INSERT INTO athlete_profiles "
            "(user_id, age, birth_year, gender, height_cm, weight_kg, "
            "primary_sport, id, created_at, updated_at) "
            "VALUES (?, 36, 1990, 'FEMALE', 168.0, 62.5, 'RUNNING', ?, ?, ?)",
            (user_id, profile_id, now, now),
        )
        connection.commit()

    command.upgrade(configuration, "head")
    with sqlite3.connect(database_path) as connection:
        profile = connection.execute(
            "SELECT age, birth_year, gender, height_cm, weight_kg, primary_sport, "
            "availability_text, equipment_recommendation_text, equipment_text, "
            "health_limitations_text FROM athlete_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()

    assert profile == (
        36,
        1990,
        "FEMALE",
        168.0,
        62.5,
        "RUNNING",
        None,
        None,
        None,
        None,
    )
    get_settings.cache_clear()


def test_legacy_sessions_normalize_to_retained_checkpoints(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    database_path = tmp_path / "legacy-session-normalization.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)  # type: ignore[attr-defined]
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "")  # type: ignore[attr-defined]
    get_settings.cache_clear()
    configuration = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(configuration, "0007_conversational_goal")

    user_ids = [f"{index:032x}" for index in range(1, 6)]
    session_ids = [f"{index:032x}" for index in range(101, 106)]
    now = "2026-08-03 08:00:00+00:00"
    draft = {
        "main_goal": "Complete a marathon",
        "event_date": None,
        "target_outcome": "Finish safely",
        "secondary_priority": None,
        "missing_fields": [],
        "ambiguous_fields": [],
        "message_status": "COMPLETE",
    }
    staged_answers = {
        "consent": True,
        "raw_goal_text": "I want to complete a marathon safely.",
        "goal_messages": ["I want to complete a marathon safely."],
        "goal_draft": draft,
    }
    rows = [
        ("ACTIVE", "AGE", {}),
        (
            "ACTIVE",
            "PRIMARY_SPORT",
            {"consent": True, "_setup_introduction_pending": True},
        ),
        ("ACTIVE", "GOAL_TYPE", staged_answers),
        ("COMPLETED", "SUMMARY", {"consent": True, **staged_answers}),
        ("CANCELLED", "GOAL_PRIORITY", staged_answers),
    ]
    with sqlite3.connect(database_path) as connection:
        for index, (status, step, answers) in enumerate(rows):
            connection.execute(
                "INSERT INTO users "
                "(telegram_user_id, language_code, status, id, created_at, updated_at) "
                "VALUES (?, 'en', 'ONBOARDING_IN_PROGRESS', ?, ?, ?)",
                (8000 + index, user_ids[index], now, now),
            )
            connection.execute(
                "INSERT INTO onboarding_sessions "
                "(user_id, status, current_step, answers, return_to_summary, "
                "id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
                (
                    user_ids[index],
                    status,
                    step,
                    json.dumps(answers),
                    session_ids[index],
                    now,
                    now,
                ),
            )
        connection.execute(
            "INSERT INTO training_goals "
            "(user_id, goal_type, goal_priority, main_goal, target_outcome, "
            "original_description, id, created_at, updated_at) "
            "VALUES (?, 'MARATHON', 'FINISH_SAFELY', 'Complete a marathon', "
            "'Finish safely', 'I want to complete a marathon safely.', ?, ?, ?)",
            (user_ids[3], f"{201:032x}", now, now),
        )
        connection.execute(
            "INSERT INTO training_goals "
            "(user_id, goal_type, event_name, event_date, goal_priority, "
            "id, created_at, updated_at) "
            "VALUES (?, 'TEN_K', 'Barcelona 10K', '2027-01-17', "
            "'PERSONAL_BEST', ?, ?, ?)",
            (user_ids[1], f"{202:032x}", now, now),
        )
        connection.execute(
            "INSERT INTO llm_usage "
            "(user_id, onboarding_step, provider_mode, status, created_at, id) "
            "VALUES (?, 'GOAL_TYPE', 'mock', 'SUCCEEDED', ?, ?)",
            (user_ids[2], now, f"{301:032x}"),
        )
        connection.commit()

    command.upgrade(configuration, "head")

    with sqlite3.connect(database_path) as connection:
        normalized = connection.execute(
            "SELECT status, current_step, answers FROM onboarding_sessions "
            "ORDER BY user_id"
        ).fetchall()
        llm_step = connection.execute(
            "SELECT onboarding_step FROM llm_usage"
        ).fetchone()
        canonical = connection.execute(
            "SELECT main_goal, target_outcome, original_description "
            "FROM training_goals WHERE user_id = ?",
            (user_ids[3],),
        ).fetchone()
        backfilled = connection.execute(
            "SELECT main_goal, event_date, target_outcome, original_description "
            "FROM training_goals WHERE user_id = ?",
            (user_ids[1],),
        ).fetchone()

    decoded = [
        (status, step, json.loads(answers)) for status, step, answers in normalized
    ]
    assert decoded[0] == ("ACTIVE", "CONSENT", {})
    assert decoded[1] == ("ACTIVE", "SETUP_INTRODUCTION", {"consent": True})
    assert decoded[2][0:2] == ("ACTIVE", "GOAL_INTAKE")
    assert decoded[2][2]["raw_goal_text"] == staged_answers["raw_goal_text"]
    assert decoded[2][2]["_goal_intake_phase"] == "CONFIRMING"
    assert decoded[3][0:2] == ("ACTIVE", "PROFILE_BIRTH_YEAR_INTAKE")
    assert "goal_draft" not in decoded[3][2]
    assert decoded[4][0:2] == ("CANCELLED", "GOAL_INTAKE")
    assert llm_step == ("GOAL_INTAKE",)
    assert canonical == (
        "Complete a marathon",
        "Finish safely",
        "I want to complete a marathon safely.",
    )
    assert backfilled == (
        "Barcelona 10K",
        "2027-01-17",
        "Achieve a personal best",
        "Barcelona 10K",
    )
    get_settings.cache_clear()


def test_unified_import_migration_preserves_and_backfills_0002_data(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    database_path = tmp_path / "migration-from-0002.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)  # type: ignore[attr-defined]
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "")  # type: ignore[attr-defined]
    get_settings.cache_clear()
    configuration = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(configuration, "0002_apple_health_import")

    user_id = "11111111111111111111111111111111"
    onboarding_id = "22222222222222222222222222222222"
    activity_id = "33333333333333333333333333333333"
    exact_apple_id = "55555555555555555555555555555555"
    summary_apple_id = "66666666666666666666666666666666"
    import_job_id = "44444444444444444444444444444444"
    now = "2026-07-29 08:00:00+00:00"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO users "
            "(telegram_user_id, language_code, status, id, created_at, updated_at) "
            "VALUES (?, 'en', 'BASELINE_READY', ?, ?, ?)",
            (123456, user_id, now, now),
        )
        connection.execute(
            "INSERT INTO onboarding_sessions "
            "(user_id, status, current_step, answers, return_to_summary, "
            "id, created_at, updated_at) "
            "VALUES (?, 'COMPLETED', 'SUMMARY', '{}', 0, ?, ?, ?)",
            (user_id, onboarding_id, now, now),
        )
        connection.execute(
            "INSERT INTO activities "
            "(user_id, source, external_id, sport, source_sport_type, name, "
            "started_at, duration_seconds, average_heart_rate, id, "
            "created_at, updated_at) "
            "VALUES (?, 'STRAVA', 'strava-42', 'RUN', 'Run', 'Morning run', "
            "?, 3600, 151, ?, ?, ?)",
            (user_id, now, activity_id, now, now),
        )
        connection.executemany(
            "INSERT INTO activities "
            "(user_id, source, external_id, sport, source_sport_type, name, "
            "started_at, duration_seconds, average_heart_rate, "
            "heart_rate_quality, heart_rate_reliable, id, created_at, updated_at) "
            "VALUES (?, 'APPLE_HEALTH', ?, 'RUN', "
            "'HKWorkoutActivityTypeRunning', 'Apple run', ?, 3600, 149, "
            "?, ?, ?, ?, ?)",
            [
                (
                    user_id,
                    "apple-exact",
                    now,
                    "EXACT_SAMPLE",
                    1,
                    exact_apple_id,
                    now,
                    now,
                ),
                (
                    user_id,
                    "apple-summary",
                    now,
                    "SHORT_INTERVAL",
                    0,
                    summary_apple_id,
                    now,
                    now,
                ),
            ],
        )
        connection.execute(
            "INSERT INTO apple_health_import_jobs "
            "(user_id, onboarding_session_id, telegram_update_id, "
            "telegram_file_id, telegram_file_unique_id, display_filename, "
            "file_sha256, status, id, created_at, updated_at) "
            "VALUES (?, ?, 10, 'file', 'unique', 'export.zip', ?, "
            "'SUCCEEDED', ?, ?, ?)",
            (
                user_id,
                onboarding_id,
                "a" * 64,
                import_job_id,
                now,
                now,
            ),
        )
        connection.commit()

    command.upgrade(configuration, "head")

    with sqlite3.connect(database_path) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version",
        ).fetchone()
        apple_sources = connection.execute(
            "SELECT workout_id, source, external_id "
            "FROM activity_source_links "
            "WHERE source = 'APPLE_HEALTH' ORDER BY workout_id",
        ).fetchall()
        source_link = connection.execute(
            "SELECT workout_id, source, external_id "
            "FROM activity_source_links "
            "WHERE user_id = ? AND external_id = 'strava-42'",
            (user_id,),
        ).fetchone()
        running_details = connection.execute(
            "SELECT COUNT(*) FROM running_workout_details",
        ).fetchone()
        import_job = connection.execute(
            "SELECT file_format, temporary_path "
            "FROM apple_health_import_jobs WHERE id = ?",
            (import_job_id,),
        ).fetchone()

    assert revision == ("0013_add_athlete_profile_context",)
    assert apple_sources == [
        (exact_apple_id, "APPLE_HEALTH", "apple-exact"),
        (summary_apple_id, "APPLE_HEALTH", "apple-summary"),
    ]
    assert source_link == (activity_id, "STRAVA", "strava-42")
    assert running_details == (3,)
    assert import_job == ("APPLE_HEALTH_ZIP", None)
    get_settings.cache_clear()


def test_discipline_workout_migration_preserves_populated_0003_data(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    database_path = tmp_path / "migration-from-0003.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)  # type: ignore[attr-defined]
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "")  # type: ignore[attr-defined]
    get_settings.cache_clear()
    configuration = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(configuration, "0003_unified_training_import")

    user_id = "10000000000000000000000000000000"
    baseline_id = "11000000000000000000000000000000"
    run_id = "20000000000000000000000000000000"
    cycling_id = "30000000000000000000000000000000"
    hiking_id = "40000000000000000000000000000000"
    pool_id = "50000000000000000000000000000000"
    open_water_id = "60000000000000000000000000000000"
    ambiguous_swim_id = "70000000000000000000000000000000"
    strength_id = "80000000000000000000000000000000"
    other_id = "90000000000000000000000000000000"
    zero_duration_id = "91000000000000000000000000000000"
    import_job_id = "a0000000000000000000000000000000"
    extra_source_link_id = "a1000000000000000000000000000000"
    feedback_id = "b0000000000000000000000000000000"
    flow_id = "c0000000000000000000000000000000"
    observation_id = "d0000000000000000000000000000000"
    now = "2026-07-30 06:00:00+00:00"
    deleted_at = "2026-07-30 07:00:00+00:00"

    activity_columns = (
        "user_id",
        "source",
        "external_id",
        "sport",
        "source_sport_type",
        "name",
        "started_at",
        "ended_at",
        "timezone",
        "duration_seconds",
        "moving_time_seconds",
        "distance_meters",
        "elevation_gain_meters",
        "calories_kcal",
        "average_heart_rate",
        "average_heart_rate_source",
        "max_heart_rate",
        "heart_rate_sample_count",
        "heart_rate_quality",
        "heart_rate_reliable",
        "average_cadence",
        "route_points",
        "average_speed",
        "average_watts",
        "trainer",
        "commute",
        "manual",
        "raw_summary",
        "deleted_at",
        "id",
        "created_at",
        "updated_at",
    )

    def activity(
        *,
        activity_id: str,
        external_id: str,
        sport: str,
        source_sport_type: str,
        name: str,
        duration_seconds: int = 3600,
        moving_time_seconds: int | None = 3000,
        distance_meters: float | None = 10_000,
        raw_summary: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "user_id": user_id,
            "source": "STRAVA",
            "external_id": external_id,
            "sport": sport,
            "source_sport_type": source_sport_type,
            "name": name,
            "started_at": now,
            "ended_at": "2026-07-30 07:30:00+00:00",
            "timezone": "Europe/Madrid",
            "duration_seconds": duration_seconds,
            "moving_time_seconds": moving_time_seconds,
            "distance_meters": distance_meters,
            "elevation_gain_meters": 321.5,
            "calories_kcal": 654.5,
            "average_heart_rate": 151.5,
            "average_heart_rate_source": "PROVIDER_SUMMARY",
            "max_heart_rate": 184.0,
            "heart_rate_sample_count": 42,
            "heart_rate_quality": "SHORT_INTERVAL",
            "heart_rate_reliable": 1,
            "average_cadence": 88.0,
            "route_points": json.dumps([{"lat": 41.1, "lon": 2.1}]),
            "average_speed": 8.5,
            "average_watts": 210.5,
            "trainer": 0,
            "commute": 1,
            "manual": 1,
            "raw_summary": json.dumps(
                raw_summary or {"provider": "legacy", "private": True}
            ),
            "deleted_at": deleted_at,
            "id": activity_id,
            "created_at": now,
            "updated_at": now,
        }

    activities = [
        activity(
            activity_id=run_id,
            external_id="run-1",
            sport="RUN",
            source_sport_type="TrailRun",
            name="Trail run",
        ),
        {
            **activity(
                activity_id=cycling_id,
                external_id="ride-1",
                sport="RIDE",
                source_sport_type="Ride",
                name="Indoor ride",
                distance_meters=30_000,
            ),
            "trainer": 1,
        },
        activity(
            activity_id=hiking_id,
            external_id="hike-1",
            sport="WALK_HIKE",
            source_sport_type="Hike",
            name="Mountain hike",
        ),
        activity(
            activity_id=pool_id,
            external_id="pool-1",
            sport="SWIM",
            source_sport_type="PoolSwim",
            name="25m Pool swimming",
            distance_meters=2_000,
        ),
        activity(
            activity_id=open_water_id,
            external_id="open-water-1",
            sport="SWIM",
            source_sport_type="OpenWaterSwim",
            name="Sea swim",
            distance_meters=2_500,
        ),
        activity(
            activity_id=ambiguous_swim_id,
            external_id="swim-unknown-1",
            sport="SWIM",
            source_sport_type="Swim",
            name="Morning swim",
            distance_meters=1_500,
        ),
        activity(
            activity_id=strength_id,
            external_id="strength-1",
            sport="STRENGTH",
            source_sport_type="WeightTraining",
            name="Gym strength",
            distance_meters=None,
        ),
        activity(
            activity_id=other_id,
            external_id="other-1",
            sport="OTHER",
            source_sport_type="Kayaking",
            name="River kayaking",
        ),
        activity(
            activity_id=zero_duration_id,
            external_id="zero-1",
            sport="RUN",
            source_sport_type="Run",
            name="Zero-duration imported run",
            duration_seconds=0,
            moving_time_seconds=0,
            distance_meters=1000,
        ),
    ]

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO users "
            "(telegram_user_id, language_code, status, id, created_at, updated_at) "
            "VALUES (987654, 'en', 'BASELINE_READY', ?, ?, ?)",
            (user_id, now, now),
        )
        connection.execute(
            "INSERT INTO athlete_baselines "
            "(user_id, version, generated_at, analysis_start, analysis_end, "
            "source, status, overall_confidence, id, created_at, updated_at) "
            "VALUES (?, 1, ?, ?, ?, 'STRAVA', 'READY', 0.9, ?, ?, ?)",
            (user_id, now, now, now, baseline_id, now, now),
        )
        baseline_rows = [
            (
                baseline_id,
                discipline,
                index,
                f"e{index:031x}",
                now,
                now,
            )
            for index, discipline in enumerate(
                ("RUN", "RIDE", "SWIM", "STRENGTH", "WALK_HIKE", "OTHER"),
                start=1,
            )
        ]
        connection.executemany(
            "INSERT INTO discipline_baselines "
            "(athlete_baseline_id, discipline, level_label, confidence, "
            "sessions_count, active_weeks, total_duration_seconds, "
            "average_weekly_duration_seconds, recent_session_count, metrics, "
            "id, created_at, updated_at) "
            "VALUES (?, ?, 'INTERMEDIATE', 0.8, ?, 2, 7200, 3600, 2, '{}', "
            "?, ?, ?)",
            baseline_rows,
        )
        placeholders = ", ".join(f":{column}" for column in activity_columns)
        connection.executemany(
            f"INSERT INTO activities ({', '.join(activity_columns)}) "
            f"VALUES ({placeholders})",
            activities,
        )
        connection.execute(
            "INSERT INTO apple_health_import_jobs "
            "(user_id, onboarding_session_id, activity_id, telegram_update_id, "
            "telegram_file_id, telegram_file_unique_id, display_filename, "
            "temporary_path, file_sha256, file_format, context, status, "
            "workouts_found, activities_imported, activities_updated, "
            "activities_skipped, heart_rate_records_matched, warning_count, "
            "id, created_at, updated_at) "
            "VALUES (?, NULL, ?, 20, 'file', 'unique', 'ride.tcx', "
            "'temporary/ride.tcx', ?, 'TCX', 'DAILY', 'SUCCEEDED', "
            "1, 1, 0, 0, 3, 1, ?, ?, ?)",
            (user_id, cycling_id, "f" * 64, import_job_id, now, now),
        )
        connection.executemany(
            "INSERT INTO activity_source_links "
            "(user_id, activity_id, source, external_id, file_sha256, "
            "import_job_id, id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    user_id,
                    run_id,
                    "STRAVA",
                    "run-1",
                    None,
                    None,
                    run_id,
                    now,
                    now,
                ),
                (
                    user_id,
                    run_id,
                    "TCX",
                    "run-extra.tcx",
                    "e" * 64,
                    import_job_id,
                    extra_source_link_id,
                    now,
                    now,
                ),
            ],
        )
        connection.execute(
            "INSERT INTO activity_feedback "
            "(user_id, activity_id, manual_average_heart_rate, reported_rpe, "
            "reported_rpe_label, reported_discomfort, discomfort_body_area, "
            "discomfort_severity, discomfort_description, feedback_created_at, "
            "id, created_at, updated_at) "
            "VALUES (?, ?, 150, 7, 'Hard', 1, 'KNEE', 'MILD', "
            "'Tight after the run', ?, ?, ?, ?)",
            (user_id, run_id, now, feedback_id, now, now),
        )
        connection.execute(
            "INSERT INTO workout_flow_sessions "
            "(user_id, activity_id, state, return_to_onboarding, id, "
            "created_at, updated_at) "
            "VALUES (?, ?, 'RPE', 0, ?, ?, ?)",
            (user_id, pool_id, flow_id, now, now),
        )
        connection.execute(
            "INSERT INTO heart_rate_observations "
            "(user_id, activity_id, source_record_key, source_name, "
            "started_at, ended_at, beats_per_minute, temporal_quality, "
            "id, created_at, updated_at) "
            "VALUES (?, ?, 'sample-1', 'Watch', ?, ?, 155, 'EXACT_SAMPLE', "
            "?, ?, ?)",
            (user_id, run_id, now, now, observation_id, now, now),
        )
        connection.commit()

    command.upgrade(configuration, "head")

    with sqlite3.connect(database_path) as connection:
        revision_at_head = connection.execute(
            "SELECT version_num FROM alembic_version",
        ).fetchone()
        head_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'",
            )
        }
        workout_rows = connection.execute(
            "SELECT id, discipline, duration_seconds, created_at, updated_at "
            "FROM workouts ORDER BY id",
        ).fetchall()
        main_detail_ids = connection.execute(
            "SELECT workout_id FROM running_workout_details "
            "UNION ALL SELECT workout_id FROM cycling_workout_details "
            "UNION ALL SELECT workout_id FROM hiking_workout_details "
            "UNION ALL SELECT workout_id FROM swimming_workout_details "
            "UNION ALL SELECT workout_id FROM strength_workout_details "
            "UNION ALL SELECT workout_id FROM other_workout_details",
        ).fetchall()
        cycling = connection.execute(
            "SELECT cycling_type, average_speed_kph "
            "FROM cycling_workout_details WHERE workout_id = ?",
            (cycling_id,),
        ).fetchone()
        pool = connection.execute(
            "SELECT swimming_environment, pool_length_meters "
            "FROM swimming_workout_details "
            "JOIN pool_swimming_details USING (workout_id) "
            "WHERE workout_id = ?",
            (pool_id,),
        ).fetchone()
        open_water = connection.execute(
            "SELECT swimming_environment FROM swimming_workout_details "
            "WHERE workout_id = ?",
            (open_water_id,),
        ).fetchone()
        ambiguous = connection.execute(
            "SELECT workouts.discipline, other_workout_details.metrics_jsonb "
            "FROM workouts JOIN other_workout_details "
            "ON other_workout_details.workout_id = workouts.id "
            "WHERE workouts.id = ?",
            (ambiguous_swim_id,),
        ).fetchone()
        zero_duration = connection.execute(
            "SELECT workouts.duration_seconds, "
            "running_workout_details.average_pace_seconds_per_km "
            "FROM workouts JOIN running_workout_details "
            "ON running_workout_details.workout_id = workouts.id "
            "WHERE workouts.id = ?",
            (zero_duration_id,),
        ).fetchone()
        cycling_source = connection.execute(
            "SELECT id, raw_sport, source_metadata_jsonb, deleted_at "
            "FROM activity_source_links WHERE workout_id = ? "
            "AND source = 'STRAVA'",
            (cycling_id,),
        ).fetchone()
        supporting_foreign_keys = connection.execute(
            "SELECT "
            "(SELECT workout_id FROM apple_health_import_jobs WHERE id = ?), "
            "(SELECT workout_id FROM activity_feedback WHERE id = ?), "
            "(SELECT mobility_done FROM activity_feedback WHERE id = ?), "
            "(SELECT workout_id FROM workout_flow_sessions WHERE id = ?)",
            (
                import_job_id,
                feedback_id,
                feedback_id,
                flow_id,
            ),
        ).fetchone()
        baseline_disciplines = [
            row[0]
            for row in connection.execute(
                "SELECT discipline FROM discipline_baselines ORDER BY discipline",
            )
        ]
        foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check",
        ).fetchall()

    assert revision_at_head == ("0013_add_athlete_profile_context",)
    assert "heart_rate_observations" not in head_tables
    assert len(workout_rows) == len(activities)
    assert {row[0] for row in workout_rows} == {row["id"] for row in activities}
    assert all(row[3].startswith("2026-07-30 06:00:00") for row in workout_rows)
    assert all(row[4].startswith("2026-07-30 06:00:00") for row in workout_rows)
    assert len(main_detail_ids) == len(activities)
    assert len({row[0] for row in main_detail_ids}) == len(activities)
    assert cycling == ("STATIONARY", 36.0)
    assert pool == ("POOL", 25.0)
    assert open_water == ("OPEN_WATER",)
    assert ambiguous is not None
    assert ambiguous[0] == "OTHER"
    ambiguous_metrics = json.loads(ambiguous[1])
    assert ambiguous_metrics["fallback_reason"] == "ambiguous_swimming_environment"
    assert ambiguous_metrics["legacy_activity"]["legacy_sport"] == "SWIM"
    assert zero_duration == (1, None)
    assert cycling_source is not None
    assert cycling_source[0] == cycling_id
    assert cycling_source[1] == "Ride"
    cycling_metadata = json.loads(cycling_source[2])
    assert cycling_metadata["migration_revision"] == ("0004_discipline_workout_models")
    cycling_snapshot = cycling_metadata["canonical_snapshot"]
    assert set(cycling_snapshot) == {
        "workout",
        "main_detail_table",
        "main_detail",
        "pool_detail",
        "source_links",
    }
    assert cycling_snapshot["workout"]["discipline"] == "CYCLING"
    assert cycling_snapshot["main_detail_table"] == "cycling_workout_details"
    assert cycling_snapshot["main_detail"]["average_speed_kph"] == 36.0
    assert cycling_snapshot["pool_detail"] is None
    assert len(cycling_snapshot["source_links"]) == 1
    assert (
        cycling_snapshot["source_links"][0]["external_id"]
        == cycling_metadata["legacy_activity"]["legacy_external_id"]
    )
    assert "source_metadata_jsonb" not in cycling_snapshot["source_links"][0]
    assert cycling_metadata["legacy_activity"] == {
        "legacy_user_id": user_id,
        "legacy_source": "STRAVA",
        "legacy_external_id": "ride-1",
        "legacy_sport": "RIDE",
        "legacy_source_sport_type": "Ride",
        "legacy_name": "Indoor ride",
        "legacy_started_at": "2026-07-30T06:00:00+00:00",
        "legacy_ended_at": "2026-07-30T07:30:00+00:00",
        "legacy_timezone": "Europe/Madrid",
        "legacy_duration_seconds": 3600,
        "legacy_moving_time_seconds": 3000,
        "legacy_distance_meters": 30000.0,
        "legacy_elevation_gain_meters": 321.5,
        "legacy_calories_kcal": 654.5,
        "legacy_average_heart_rate": 151.5,
        "legacy_average_heart_rate_source": "PROVIDER_SUMMARY",
        "legacy_max_heart_rate": 184.0,
        "legacy_heart_rate_sample_count": 42,
        "legacy_heart_rate_quality": "SHORT_INTERVAL",
        "legacy_heart_rate_reliable": True,
        "legacy_average_cadence": 88.0,
        "legacy_route_points": [{"lat": 41.1, "lon": 2.1}],
        "legacy_average_speed_mps": 8.5,
        "legacy_average_watts": 210.5,
        "legacy_trainer": True,
        "legacy_commute": True,
        "legacy_manual": True,
        "legacy_raw_summary": {"provider": "legacy", "private": True},
        "legacy_deleted_at": "2026-07-30T07:00:00+00:00",
    }
    assert cycling_source[3].startswith("2026-07-30 07:00:00")
    assert supporting_foreign_keys == (
        cycling_id,
        run_id,
        None,
        pool_id,
    )
    assert baseline_disciplines == [
        "CYCLING",
        "HIKING",
        "OTHER",
        "RUNNING",
        "STRENGTH",
        "SWIMMING",
    ]
    assert foreign_key_violations == []

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE workouts SET title = 'Edited after migration' WHERE id = ?",
            (cycling_id,),
        )
        connection.commit()
    with pytest.raises(
        RuntimeError,
        match="canonical workout data changed after migration",
    ):
        command.downgrade(configuration, "0003_unified_training_import")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE workouts SET title = 'Indoor ride' WHERE id = ?",
            (cycling_id,),
        )
        connection.commit()

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE cycling_workout_details SET average_speed_kph = 37.0 "
            "WHERE workout_id = ?",
            (cycling_id,),
        )
        connection.commit()
    with pytest.raises(
        RuntimeError,
        match="canonical main-detail data changed after migration",
    ):
        command.downgrade(configuration, "0003_unified_training_import")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE cycling_workout_details SET average_speed_kph = 36.0 "
            "WHERE workout_id = ?",
            (cycling_id,),
        )
        connection.commit()

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE pool_swimming_details SET pool_length_meters = 50.0 "
            "WHERE workout_id = ?",
            (pool_id,),
        )
        connection.commit()
    with pytest.raises(
        RuntimeError,
        match="canonical pool-detail data changed after migration",
    ):
        command.downgrade(configuration, "0003_unified_training_import")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE pool_swimming_details SET pool_length_meters = 25.0 "
            "WHERE workout_id = ?",
            (pool_id,),
        )
        connection.commit()

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE activity_source_links SET deleted_at = ? WHERE id = ?",
            (deleted_at, extra_source_link_id),
        )
        connection.commit()
    with pytest.raises(
        RuntimeError,
        match="canonical source-link data changed after migration",
    ):
        command.downgrade(configuration, "0003_unified_training_import")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE activity_source_links SET deleted_at = NULL WHERE id = ?",
            (extra_source_link_id,),
        )
        connection.commit()

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE activity_source_links SET source_metadata_jsonb = ? WHERE id = ?",
            (
                json.dumps({"post_migration": "raw provider metrics"}),
                extra_source_link_id,
            ),
        )
        connection.commit()
    with pytest.raises(
        RuntimeError,
        match="source metadata created or changed after 0004",
    ):
        command.downgrade(configuration, "0003_unified_training_import")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE activity_source_links SET source_metadata_jsonb = NULL "
            "WHERE id = ?",
            (extra_source_link_id,),
        )
        connection.commit()

    command.downgrade(configuration, "0003_unified_training_import")

    with sqlite3.connect(database_path) as connection:
        revision_after_downgrade = connection.execute(
            "SELECT version_num FROM alembic_version",
        ).fetchone()
        tables_after_downgrade = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'",
            )
        }
        restored_cycling = connection.execute(
            "SELECT sport, duration_seconds, average_speed, average_watts, "
            "trainer, commute, manual, route_points, raw_summary, deleted_at "
            "FROM activities WHERE id = ?",
            (cycling_id,),
        ).fetchone()
        restored_zero = connection.execute(
            "SELECT duration_seconds, distance_meters FROM activities WHERE id = ?",
            (zero_duration_id,),
        ).fetchone()
        restored_supporting_foreign_keys = connection.execute(
            "SELECT "
            "(SELECT activity_id FROM apple_health_import_jobs WHERE id = ?), "
            "(SELECT activity_id FROM activity_feedback WHERE id = ?), "
            "(SELECT activity_id FROM workout_flow_sessions WHERE id = ?)",
            (import_job_id, feedback_id, flow_id),
        ).fetchone()
        restored_observation_count = connection.execute(
            "SELECT COUNT(*) FROM heart_rate_observations",
        ).fetchone()
        restored_baseline_disciplines = [
            row[0]
            for row in connection.execute(
                "SELECT discipline FROM discipline_baselines ORDER BY discipline",
            )
        ]
        restored_foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check",
        ).fetchall()

    assert revision_after_downgrade == ("0003_unified_training_import",)
    assert "activities" in tables_after_downgrade
    assert "workouts" not in tables_after_downgrade
    assert restored_cycling[:9] == (
        "RIDE",
        3600,
        8.5,
        210.5,
        1,
        1,
        1,
        json.dumps([{"lat": 41.1, "lon": 2.1}]),
        json.dumps({"provider": "legacy", "private": True}),
    )
    assert restored_cycling[9].startswith("2026-07-30 07:00:00")
    assert restored_zero == (0, 1000.0)
    assert restored_supporting_foreign_keys == (
        cycling_id,
        run_id,
        pool_id,
    )
    assert restored_observation_count == (0,)
    assert restored_baseline_disciplines == [
        "OTHER",
        "RIDE",
        "RUN",
        "STRENGTH",
        "SWIM",
        "WALK_HIKE",
    ]
    assert restored_foreign_key_violations == []
    get_settings.cache_clear()
