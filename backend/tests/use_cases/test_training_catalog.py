"""Focused acceptance tests for the read-only catalog and capability assessment.

The dynamic catalog expansion and publication subsystem was deleted: every
goal template and training context in every environment was `SEEDED`, and
the model never generated one. What remains here are the read paths the
planner and the new goal menu depend on.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from catalog_seed import seed_training_catalog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import GoalTemplate, GoalTemplateContext, TrainingContext
from app.domain.enums import ContextAssessmentStatus
from app.repositories.athlete_capabilities import AthleteCapabilityRepository
from app.repositories.training_catalog import TrainingCatalogRepository
from app.repositories.users import UserRepository
from app.services.capabilities import CapabilityAssessmentService
from app.training_catalog_seed import (
    CAPABILITIES,
    EXECUTION_OPTIONS,
    GOAL_CONTEXTS,
    GOAL_TEMPLATES,
    OPTION_CAPABILITIES,
    TRAINING_CONTEXTS,
    catalog_id,
)


@pytest_asyncio.fixture
async def catalog_database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory.begin() as session:
        await seed_training_catalog(session)
    yield factory
    await engine.dispose()


async def _athlete(session: AsyncSession, telegram_id: int) -> uuid.UUID:
    user, _ = await UserRepository(session).get_or_create(
        telegram_user_id=telegram_id,
        telegram_username=None,
        first_name=None,
        language_code="en",
    )
    return user.id


@pytest.mark.asyncio
async def test_seed_integrity_covers_triathlon_complete_hyrox_and_obstacle_race(
    catalog_database: async_sessionmaker[AsyncSession],
) -> None:
    goal_codes = {row[0] for row in GOAL_TEMPLATES}
    context_codes = {row[0] for row in TRAINING_CONTEXTS}
    capability_codes = {row[0] for row in CAPABILITIES}
    option_keys = {(row[0], row[1]) for row in EXECUTION_OPTIONS}
    required_option_keys = {
        (target, option)
        for target, option, _, importance in OPTION_CAPABILITIES
        if importance == "REQUIRED"
    }
    assert all(
        goal in goal_codes and context in context_codes
        for goal, context, _, _ in GOAL_CONTEXTS
    )
    assert all(
        target in context_codes and execution in context_codes
        for target, _, _, execution, _, _, _ in EXECUTION_OPTIONS
    )
    assert all(
        (target, option) in option_keys and capability in capability_codes
        for target, option, capability, _ in OPTION_CAPABILITIES
    )
    assert option_keys <= required_option_keys

    async with catalog_database() as session:
        goals = {
            item.code: item
            for item in await TrainingCatalogRepository(session).active_goal_templates()
        }
        assert {
            "TRIATHLON_HALF_DISTANCE",
            "HYROX",
            "OBSTACLE_RACE",
            "MUSCLE_RETENTION",
        }.issubset(goals)
        rows = await session.execute(
            select(
                GoalTemplate.code, func.count(GoalTemplateContext.training_context_id)
            )
            .join(
                GoalTemplateContext,
                GoalTemplateContext.goal_template_id == GoalTemplate.id,
            )
            .where(GoalTemplate.code.in_(("HYROX", "OBSTACLE_RACE")))
            .group_by(GoalTemplate.code)
        )
        counts = {code: count for code, count in rows}
        assert counts == {"HYROX": 8, "OBSTACLE_RACE": 4}
        hyrox_contexts = {
            context.code
            for context in await session.scalars(
                select(TrainingContext)
                .join(
                    GoalTemplateContext,
                    GoalTemplateContext.training_context_id == TrainingContext.id,
                )
                .join(
                    GoalTemplate,
                    GoalTemplate.id == GoalTemplateContext.goal_template_id,
                )
                .where(GoalTemplate.code == "HYROX")
            )
        }
        assert "functional_fitness" not in hyrox_contexts
        assert {
            "running_road",
            "hyrox_ski_erg",
            "hyrox_sled_push_pull",
            "hyrox_burpee_broad_jump",
            "hyrox_row",
            "hyrox_farmer_carry",
            "hyrox_sandbag_lunge",
            "hyrox_wall_balls",
        } == hyrox_contexts


@pytest.mark.asyncio
async def test_cycling_assessment_uses_mtb_then_stationary_as_substitutions(
    catalog_database: async_sessionmaker[AsyncSession],
) -> None:
    async with catalog_database.begin() as session:
        athlete_id = await _athlete(session, 9101)
        repository = AthleteCapabilityRepository(session)
        service = CapabilityAssessmentService()
        catalog = TrainingCatalogRepository(session)
        review = await service.review(
            catalog=catalog,
            athlete_capabilities=repository,
            athlete_id=athlete_id,
            goal_template_id=catalog_id("goal", "ROAD_CYCLING_EVENT"),
            supporting_goal_template_id=None,
        )
        assert review is not None
        by_code = {item.code: item.id for item in review.options}
        assessment = await service.save_and_assess(
            catalog=catalog,
            athlete_capabilities=repository,
            athlete_id=athlete_id,
            goal_template_id=catalog_id("goal", "ROAD_CYCLING_EVENT"),
            supporting_goal_template_id=None,
            review=review,
            selected_ids={by_code["mountain_bike"], by_code["helmet"]},
        )
        cycling = assessment.contexts[0]
        assert cycling.status is ContextAssessmentStatus.FEASIBLE_WITH_SUBSTITUTION
        assert cycling.default_execution == "cycling_mountain"

        assessment = await service.save_and_assess(
            catalog=catalog,
            athlete_capabilities=repository,
            athlete_id=athlete_id,
            goal_template_id=catalog_id("goal", "ROAD_CYCLING_EVENT"),
            supporting_goal_template_id=None,
            review=review,
            selected_ids={by_code["stationary_bike"]},
        )
        assert assessment.contexts[0].status is (
            ContextAssessmentStatus.FEASIBLE_WITH_SUBSTITUTION
        )
        assert assessment.contexts[0].default_execution == "cycling_stationary"


@pytest.mark.asyncio
async def test_capabilities_are_isolated_and_unrelated_answers_are_preserved(
    catalog_database: async_sessionmaker[AsyncSession],
) -> None:
    async with catalog_database.begin() as session:
        first = await _athlete(session, 9102)
        second = await _athlete(session, 9103)
        capabilities = {
            item.code: item.id
            for item in await TrainingCatalogRepository(session).active_capabilities()
        }
        first_repo = AthleteCapabilityRepository(session)
        await first_repo.replace_reviewed(
            athlete_id=first,
            reviewed_ids={capabilities["stationary_bike"], capabilities["gym_access"]},
            available_ids={capabilities["stationary_bike"], capabilities["gym_access"]},
        )
        await first_repo.replace_reviewed(
            athlete_id=first,
            reviewed_ids={capabilities["running_shoes"]},
            available_ids=set(),
        )
        assert {item.code for item in await first_repo.available(athlete_id=first)} == {
            "gym_access",
            "stationary_bike",
        }
        assert await first_repo.available(athlete_id=second) == ()
        states = await first_repo.states(athlete_id=first)
        assert capabilities["treadmill_access"] not in states
        assert capabilities["pool_access"] not in states
        assert capabilities["free_weights"] not in states
