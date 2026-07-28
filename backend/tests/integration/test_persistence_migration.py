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

    assert revision == ("0001_initial",)
    assert len(tables - {"alembic_version"}) == 17
    assert active_sync_index is not None
    assert "WHERE status IN" in active_sync_index[0]

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
