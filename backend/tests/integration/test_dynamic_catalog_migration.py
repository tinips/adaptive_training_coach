"""Representative data-safety checks for migration 0022."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0022_dynamic_training_catalog.py"
    )
    spec = importlib.util.spec_from_file_location("dynamic_catalog_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backfill_classifies_goals_merges_duplicates_and_preserves_ambiguity() -> None:
    migration = _migration()
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    goals = sa.Table(
        "training_goals",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("main_goal", sa.String(), nullable=False),
        sa.Column("original_description", sa.String(), nullable=False),
        sa.Column("secondary_priority", sa.String()),
        sa.Column("goal_template_id", sa.Uuid()),
        sa.Column("supporting_goal_template_id", sa.Uuid()),
    )
    catalog = sa.Table(
        "equipment_catalog",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("equipment", sa.String(), nullable=False),
    )
    athlete_equipment = sa.Table(
        "athlete_equipment",
        metadata,
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("equipment_id", sa.Uuid(), nullable=False),
    )
    capabilities = sa.Table(
        "athlete_capabilities",
        metadata,
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("capability_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("athlete_id", "capability_id"),
    )
    metadata.create_all(engine)
    athlete_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    gym_one, gym_two, bike = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            goals.insert(),
            {
                "id": goal_id,
                "main_goal": "Finish an Ironman 70.3",
                "original_description": "Finish safely without losing muscle",
                "secondary_priority": "Maintain muscle",
            },
        )
        connection.execute(
            catalog.insert(),
            [
                {"id": gym_one, "equipment": "gym_access"},
                {"id": gym_two, "equipment": "gym_access"},
                {"id": bike, "equipment": "bike"},
            ],
        )
        connection.execute(
            athlete_equipment.insert(),
            [
                {"athlete_id": athlete_id, "equipment_id": gym_one},
                {"athlete_id": athlete_id, "equipment_id": gym_two},
                {"athlete_id": athlete_id, "equipment_id": bike},
            ],
        )
        migration._backfill(connection)
        goal = connection.execute(sa.select(goals)).one()
        rows = connection.execute(
            sa.select(capabilities.c.capability_id, capabilities.c.status)
        ).all()

    assert goal.goal_template_id == migration.catalog_id(
        "goal", "TRIATHLON_HALF_DISTANCE"
    )
    assert goal.supporting_goal_template_id == migration.catalog_id(
        "goal", "MUSCLE_RETENTION"
    )
    assert set(rows) == {
        (migration.catalog_id("capability", "gym_access"), "AVAILABLE"),
        (
            migration.catalog_id("capability", "bike_access_unspecified"),
            "AVAILABLE",
        ),
    }


def test_migration_is_irreversible_and_drops_both_legacy_tables() -> None:
    migration = _migration()
    source = Path(migration.__file__).read_text(encoding="utf-8")
    assert 'op.drop_table("athlete_equipment")' in source
    assert 'op.drop_table("equipment_catalog")' in source
    try:
        migration.downgrade()
    except RuntimeError as exc:
        assert "restore the required pre-migration backup" in str(exc)
    else:
        raise AssertionError("downgrade must abort explicitly")
