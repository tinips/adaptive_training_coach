"""Safety checks for migration 0036."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0036_prune_non_endurance_goal_templates.py"
    )
    spec = importlib.util.spec_from_file_location("prune_goal_templates", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tables(metadata: sa.MetaData) -> tuple[sa.Table, sa.Table, sa.Table]:
    goals = sa.Table(
        "goal_templates",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
    )
    athlete_goals = sa.Table(
        "training_goals",
        metadata,
        sa.Column("goal_template_id", sa.Uuid()),
        sa.Column("supporting_goal_template_id", sa.Uuid()),
    )
    contexts = sa.Table(
        "goal_template_contexts",
        metadata,
        sa.Column("goal_template_id", sa.Uuid(), nullable=False),
    )
    return goals, athlete_goals, contexts


def test_prunes_only_unreferenced_obsolete_goals() -> None:
    migration = _migration()
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    goals, _, contexts = _tables(metadata)
    metadata.create_all(engine)
    obsolete_id, running_id = uuid.uuid4(), uuid.uuid4()

    with engine.begin() as connection:
        connection.execute(
            goals.insert(),
            [
                {
                    "id": obsolete_id,
                    "code": "HYROX",
                    "source": "SEEDED",
                    "definition_version": 1,
                },
                {
                    "id": running_id,
                    "code": "RUNNING_5K",
                    "source": "SEEDED",
                    "definition_version": 1,
                },
            ],
        )
        connection.execute(contexts.insert(), {"goal_template_id": obsolete_id})
        migration.op = type(
            "MigrationOperations", (), {"get_bind": lambda _: connection}
        )()
        migration.upgrade()
        remaining = set(connection.execute(sa.select(goals.c.code)).scalars())
        remaining_contexts = set(
            connection.execute(sa.select(contexts.c.goal_template_id)).scalars()
        )

    assert remaining == {"RUNNING_5K"}
    assert remaining_contexts == set()


def test_refuses_to_delete_a_goal_referenced_by_an_athlete() -> None:
    migration = _migration()
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    goals, athlete_goals, _ = _tables(metadata)
    metadata.create_all(engine)
    hyrox_id = uuid.uuid4()

    with engine.begin() as connection:
        connection.execute(
            goals.insert(),
            {
                "id": hyrox_id,
                "code": "HYROX",
                "source": "SEEDED",
                "definition_version": 1,
            },
        )
        connection.execute(athlete_goals.insert(), {"goal_template_id": hyrox_id})
        migration.op = type(
            "MigrationOperations", (), {"get_bind": lambda _: connection}
        )()
        with pytest.raises(RuntimeError, match="referenced by athletes: HYROX"):
            migration.upgrade()
