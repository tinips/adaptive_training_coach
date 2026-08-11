"""Equipment migration data-safety tests."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa


def _load_migration(filename: str) -> ModuleType:
    path = Path(__file__).parents[2] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_available_backfill_is_current_revision_only_and_idempotent() -> None:
    migration = _load_migration("0017_equipment_catalog.py")
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    resources = sa.Table(
        "equipment_resources",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
    )
    goals = sa.Table(
        "training_goals",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("equipment_context_revision", sa.Integer(), nullable=False),
    )
    statuses = sa.Table(
        "athlete_goal_equipment_statuses",
        metadata,
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("training_goal_id", sa.Uuid(), nullable=False),
        sa.Column("goal_revision", sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
    )
    catalog = sa.Table(
        "equipment_catalog",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("discipline", sa.String(), nullable=False),
        sa.Column("equipment", sa.String(), nullable=False),
    )
    athlete_equipment = sa.Table(
        "athlete_equipment",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("equipment_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("athlete_id", "equipment_id"),
    )
    metadata.create_all(engine)
    athlete_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    road_resource = uuid.uuid4()
    helmet_resource = uuid.uuid4()
    road_catalog_id = migration._catalog_id("CYCLING", "road_bike")
    helmet_catalog_id = migration._catalog_id("CYCLING", "helmet")
    with engine.begin() as connection:
        connection.execute(
            resources.insert(),
            [
                {"id": road_resource, "code": "road_or_tri_bike"},
                {"id": helmet_resource, "code": "helmet"},
            ],
        )
        connection.execute(
            goals.insert(),
            {"id": goal_id, "equipment_context_revision": 2},
        )
        connection.execute(
            catalog.insert(),
            [
                {
                    "id": road_catalog_id,
                    "discipline": "CYCLING",
                    "equipment": "road_bike",
                },
                {
                    "id": helmet_catalog_id,
                    "discipline": "CYCLING",
                    "equipment": "helmet",
                },
            ],
        )
        connection.execute(
            statuses.insert(),
            [
                {
                    "user_id": athlete_id,
                    "training_goal_id": goal_id,
                    "goal_revision": 2,
                    "resource_id": road_resource,
                    "status": "AVAILABLE",
                },
                {
                    "user_id": athlete_id,
                    "training_goal_id": goal_id,
                    "goal_revision": 1,
                    "resource_id": helmet_resource,
                    "status": "AVAILABLE",
                },
                {
                    "user_id": athlete_id,
                    "training_goal_id": goal_id,
                    "goal_revision": 2,
                    "resource_id": helmet_resource,
                    "status": "UNAVAILABLE",
                },
            ],
        )

        migration._backfill_available(connection)
        migration._backfill_available(connection)
        rows = connection.execute(
            sa.select(
                athlete_equipment.c.athlete_id,
                athlete_equipment.c.equipment_id,
            )
        ).all()

    assert rows == [(athlete_id, road_catalog_id)]


def test_session_normalization_removes_only_obsolete_staged_equipment() -> None:
    migration = _load_migration("0017_equipment_catalog.py")
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    onboarding = sa.Table(
        "onboarding_sessions",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("current_step", sa.String(), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
    )
    settings = sa.Table(
        "profile_settings_sessions",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("current_step", sa.String(), nullable=False),
        sa.Column("pending_answers", sa.JSON(), nullable=False),
    )
    metadata.create_all(engine)
    onboarding_id = uuid.uuid4()
    settings_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            onboarding.insert(),
            {
                "id": onboarding_id,
                "current_step": "EQUIPMENT_DETAILS_INTAKE",
                "answers": {
                    "consent": True,
                    "equipment_resource_ids": [str(uuid.uuid4())],
                },
            },
        )
        connection.execute(
            settings.insert(),
            {
                "id": settings_id,
                "current_step": "EQUIPMENT",
                "pending_answers": {"selected": [str(uuid.uuid4())]},
            },
        )

        migration._normalize_sessions(connection)
        onboarding_row = connection.execute(sa.select(onboarding)).one()
        settings_row = connection.execute(sa.select(settings)).one()

    assert onboarding_row.current_step == "EQUIPMENT_RECOMMENDATION"
    assert onboarding_row.answers == {"consent": True}
    assert settings_row.current_step == "MENU"
    assert settings_row.pending_answers == {}


def test_cleanup_aborts_before_drops_for_unmatched_available_code() -> None:
    migration = _load_migration("0018_remove_obsolete_equipment.py")
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    resources = sa.Table(
        "equipment_resources",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
    )
    goals = sa.Table(
        "training_goals",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("equipment_context_revision", sa.Integer(), nullable=False),
    )
    statuses = sa.Table(
        "athlete_goal_equipment_statuses",
        metadata,
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("training_goal_id", sa.Uuid(), nullable=False),
        sa.Column("goal_revision", sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
    )
    sa.Table(
        "athlete_equipment",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("equipment_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(engine)
    athlete_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    resource_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            resources.insert(),
            {"id": resource_id, "code": "future_unmapped_resource"},
        )
        connection.execute(
            goals.insert(),
            {"id": goal_id, "equipment_context_revision": 3},
        )
        connection.execute(
            statuses.insert(),
            {
                "user_id": athlete_id,
                "training_goal_id": goal_id,
                "goal_revision": 3,
                "resource_id": resource_id,
                "status": "AVAILABLE",
            },
        )

        with pytest.raises(RuntimeError, match="future_unmapped_resource"):
            migration._final_backfill(connection)
