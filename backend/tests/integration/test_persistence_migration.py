"""Alembic smoke test from an empty portable database."""

from __future__ import annotations

import sqlite3
from pathlib import Path

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

    assert revision == ("0003_unified_training_import",)
    assert len(tables - {"alembic_version"}) == 22
    assert {
        "activity_feedback",
        "activity_source_links",
        "workout_flow_sessions",
    }.issubset(tables)
    assert active_sync_index is not None
    assert "WHERE status IN" in active_sync_index[0]
    assert active_apple_index is not None
    assert "WHERE status IN" in active_apple_index[0]
    assert import_hash_index is not None
    assert "temporary_path" in import_job_columns

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
        activity = connection.execute(
            "SELECT average_heart_rate_source, heart_rate_reliable "
            "FROM activities WHERE id = ?",
            (activity_id,),
        ).fetchone()
        apple_sources = connection.execute(
            "SELECT id, average_heart_rate_source, heart_rate_reliable "
            "FROM activities WHERE source = 'APPLE_HEALTH' ORDER BY id",
        ).fetchall()
        source_link = connection.execute(
            "SELECT activity_id, source, external_id "
            "FROM activity_source_links "
            "WHERE user_id = ? AND external_id = 'strava-42'",
            (user_id,),
        ).fetchone()
        import_job = connection.execute(
            "SELECT file_format, context, onboarding_session_id, temporary_path "
            "FROM apple_health_import_jobs WHERE id = ?",
            (import_job_id,),
        ).fetchone()

    assert revision == ("0003_unified_training_import",)
    assert activity == ("PROVIDER_SUMMARY", 1)
    assert apple_sources == [
        (exact_apple_id, "MEASURED_SENSOR", 1),
        (summary_apple_id, "PROVIDER_SUMMARY", 0),
    ]
    assert source_link == (activity_id, "STRAVA", "strava-42")
    assert import_job == (
        "APPLE_HEALTH_ZIP",
        "ONBOARDING",
        onboarding_id,
        None,
    )
    get_settings.cache_clear()
