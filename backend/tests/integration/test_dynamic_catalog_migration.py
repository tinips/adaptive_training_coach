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


def _catalog_standardization_migration() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0027_standardize_catalog_execution_options.py"
    )
    spec = importlib.util.spec_from_file_location(
        "catalog_option_standardization_migration", path
    )
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


def test_catalog_option_standardization_is_repeatable_and_scope_safe() -> None:
    migration = _catalog_standardization_migration()
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    goals = sa.Table(
        "goal_templates",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
    )
    contexts = sa.Table(
        "training_contexts",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
    )
    links = sa.Table(
        "goal_template_contexts",
        metadata,
        sa.Column("goal_template_id", sa.Uuid(), nullable=False),
        sa.Column("training_context_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
    )
    metadata.create_all(engine)
    hyrox_goal = uuid.uuid4()
    rowing_goal = uuid.uuid4()
    hyrox_row = uuid.uuid4()
    rowing_regatta = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            goals.insert(),
            [
                {"id": hyrox_goal, "code": "HYROX"},
                {"id": rowing_goal, "code": "ROWING_REGATTA"},
            ],
        )
        connection.execute(
            contexts.insert(),
            [
                {"id": hyrox_row, "code": "hyrox_row"},
                {"id": rowing_regatta, "code": "rowing_regatta"},
            ],
        )
        connection.execute(
            links.insert(),
            [
                {
                    "goal_template_id": hyrox_goal,
                    "training_context_id": hyrox_row,
                    "role": "TARGET",
                },
                {
                    "goal_template_id": rowing_goal,
                    "training_context_id": hyrox_row,
                    "role": "SUPPORTING",
                },
                {
                    "goal_template_id": rowing_goal,
                    "training_context_id": rowing_regatta,
                    "role": "TARGET",
                },
            ],
        )
        migration.op = type(
            "MigrationOperations", (), {"get_bind": lambda _self: connection}
        )()
        migration.upgrade()
        migration.upgrade()
        rows = connection.execute(
            sa.select(goals.c.code, contexts.c.code, links.c.role)
            .join(links, links.c.goal_template_id == goals.c.id)
            .join(contexts, links.c.training_context_id == contexts.c.id)
        ).all()

    assert set(rows) == {
        ("HYROX", "hyrox_row", "TARGET"),
        ("ROWING_REGATTA", "rowing_regatta", "TARGET"),
    }
