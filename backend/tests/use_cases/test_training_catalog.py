"""Focused acceptance tests for the dynamic catalog and capability assessment."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from catalog_seed import seed_training_catalog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import (
    Capability,
    ContextExecutionOption,
    ExecutionOptionCapability,
    GoalTemplate,
    GoalTemplateContext,
    TrainingContext,
)
from app.domain.enums import (
    ContextAssessmentStatus,
    Discipline,
    GoalTemplateKind,
)
from app.repositories.athlete_capabilities import AthleteCapabilityRepository
from app.repositories.training_catalog import TrainingCatalogRepository
from app.repositories.users import UserRepository
from app.schemas.catalog_expansion import (
    ContextCapabilityOutput,
    GoalContextMappingOutput,
    GoalTemplateDraft,
)
from app.services.capabilities import CapabilityAssessmentService
from app.services.training_catalog import (
    CatalogExpansionError,
    TrainingCatalogPublicationService,
)
from app.training_catalog_seed import (
    CAPABILITIES,
    EXECUTION_OPTIONS,
    GOAL_CONTEXTS,
    GOAL_TEMPLATES,
    OPTION_CAPABILITIES,
    TRAINING_CONTEXTS,
    catalog_id,
)
from app.workflows.catalog_expansion.nodes import _CAPABILITIES_SYSTEM, _MAP_SYSTEM


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


def test_catalog_expansion_prompts_explicitly_request_json_mode() -> None:
    assert "JSON object" in _MAP_SYSTEM
    assert "JSON object" in _CAPABILITIES_SYSTEM
    assert "TARGET for direct practice" in _MAP_SYSTEM
    assert "use OTHER\nfor rowing" in _MAP_SYSTEM
    assert "USE_EXISTING codes must occur" in _MAP_SYSTEM
    assert "Option role is PREFERRED or SUBSTITUTE" in _CAPABILITIES_SYSTEM
    assert "methods, workouts, drills" in _CAPABILITIES_SYSTEM
    assert "set of capability codes in capabilities must equal" in (
        _CAPABILITIES_SYSTEM
    )
    assert "an exact match must be USE_EXISTING" in _CAPABILITIES_SYSTEM
    assert len(_MAP_SYSTEM) < 1_500
    assert len(_CAPABILITIES_SYSTEM) < 1_500


@pytest.mark.asyncio
async def test_seed_integrity_covers_triathlon_hyrox_and_obstacle_race(
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
        assert counts == {"HYROX": 2, "OBSTACLE_RACE": 4}


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


@pytest.mark.asyncio
async def test_new_goal_reuses_existing_context_and_is_immediately_reusable(
    catalog_database: async_sessionmaker[AsyncSession],
) -> None:
    draft = GoalTemplateDraft(
        code="STAIR_CLIMB_EVENT",
        kind=GoalTemplateKind.PRIMARY,
        display_name="Stair-climbing event",
        description="General preparation for a stair-climbing event.",
    )
    mapping = GoalContextMappingOutput.model_validate(
        {
            "templates": [
                {
                    "template_code": draft.code,
                    "contexts": [
                        {
                            "decision": "USE_EXISTING",
                            "code": "running_road",
                            "display_name": None,
                            "description": None,
                            "discipline": Discipline.RUNNING,
                            "role": "TARGET",
                            "priority": 10,
                        }
                    ],
                }
            ]
        }
    )
    async with catalog_database.begin() as session:
        first = await TrainingCatalogPublicationService().publish(
            session=session,
            templates=(draft,),
            context_mapping=mapping,
            capability_definition=None,
        )
        second = await TrainingCatalogPublicationService().publish(
            session=session,
            templates=(draft,),
            context_mapping=None,
            capability_definition=None,
        )
        assert first.template_ids[draft.code] == second.template_ids[draft.code]
        count = await session.scalar(
            select(func.count(GoalTemplate.id)).where(GoalTemplate.code == draft.code)
        )
        assert count == 1
        assert await session.scalar(select(func.count(Capability.id))) is not None


@pytest.mark.asyncio
async def test_new_context_and_capability_are_published_in_dependency_order(
    catalog_database: async_sessionmaker[AsyncSession],
) -> None:
    draft = GoalTemplateDraft(
        code="ROWING_EVENT",
        kind=GoalTemplateKind.PRIMARY,
        display_name="Rowing event",
        description="General preparation for a rowing event.",
    )
    mapping = GoalContextMappingOutput.model_validate(
        {
            "templates": [
                {
                    "template_code": draft.code,
                    "contexts": [
                        {
                            "decision": "CREATE",
                            "code": "rowing_general",
                            "display_name": "General rowing",
                            "description": (
                                "General rowing practice on water or indoors."
                            ),
                            "discipline": "OTHER",
                            "role": "TARGET",
                            "priority": 10,
                        }
                    ],
                }
            ]
        }
    )
    definitions = ContextCapabilityOutput.model_validate(
        {
            "capabilities": [
                {
                    "decision": "CREATE",
                    "code": "rowing_machine",
                    "display_name": "Rowing machine",
                    "description": "An indoor rowing machine.",
                    "kind": "EQUIPMENT",
                }
            ],
            "contexts": [
                {
                    "target_context_code": "rowing_general",
                    "options": [
                        {
                            "code": "indoor_rowing",
                            "display_name": "Indoor rowing",
                            "execution_context_code": "rowing_general",
                            "role": "PREFERRED",
                            "priority": 10,
                            "limitations": [],
                            "requirements": [
                                {
                                    "capability_code": "rowing_machine",
                                    "importance": "REQUIRED",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    async with catalog_database.begin() as session:
        result = await TrainingCatalogPublicationService().publish(
            session=session,
            templates=(draft,),
            context_mapping=mapping,
            capability_definition=definitions,
        )
        context = await session.scalar(
            select(TrainingContext).where(TrainingContext.code == "rowing_general")
        )
        capability = await session.scalar(
            select(Capability).where(Capability.code == "rowing_machine")
        )
        option = await session.scalar(
            select(ContextExecutionOption).where(
                ContextExecutionOption.code == "indoor_rowing"
            )
        )
        assert result.created_template_codes == (draft.code,)
        assert context is not None
        assert capability is not None
        assert option is not None
        assert option.target_context_id == context.id
        assert (
            await session.scalar(
                select(func.count(ExecutionOptionCapability.execution_option_id)).where(
                    ExecutionOptionCapability.execution_option_id == option.id
                )
            )
            == 1
        )


def test_catalog_validation_rejects_unknown_capability_references() -> None:
    draft = GoalTemplateDraft(
        code="ROWING_EVENT",
        kind=GoalTemplateKind.PRIMARY,
        display_name="Rowing event",
        description="General preparation for a rowing event.",
    )
    mapping = GoalContextMappingOutput.model_validate(
        {
            "templates": [
                {
                    "template_code": draft.code,
                    "contexts": [
                        {
                            "decision": "CREATE",
                            "code": "rowing_indoor",
                            "display_name": "Indoor rowing",
                            "description": "General indoor rowing preparation.",
                            "discipline": "OTHER",
                            "role": "TARGET",
                            "priority": 10,
                        }
                    ],
                }
            ]
        }
    )
    definitions = ContextCapabilityOutput.model_validate(
        {
            "capabilities": [],
            "contexts": [
                {
                    "target_context_code": "rowing_indoor",
                    "options": [
                        {
                            "code": "rowing_machine_execution",
                            "display_name": "Indoor rowing machine",
                            "execution_context_code": "rowing_indoor",
                            "role": "PREFERRED",
                            "priority": 10,
                            "limitations": [],
                            "requirements": [
                                {
                                    "capability_code": "unknown_rowing_machine",
                                    "importance": "REQUIRED",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    with pytest.raises(CatalogExpansionError, match="unknown_capability_reference"):
        TrainingCatalogPublicationService.validate(
            templates=(draft,),
            context_mapping=mapping,
            capability_definition=definitions,
        )


def test_primary_goal_requires_a_target_context() -> None:
    draft = GoalTemplateDraft(
        code="ROWING_EVENT",
        kind=GoalTemplateKind.PRIMARY,
        display_name="Rowing event",
        description="General preparation for a rowing event.",
    )
    mapping = GoalContextMappingOutput.model_validate(
        {
            "templates": [
                {
                    "template_code": draft.code,
                    "contexts": [
                        {
                            "decision": "USE_EXISTING",
                            "code": "strength_general",
                            "display_name": None,
                            "description": None,
                            "discipline": "STRENGTH",
                            "role": "SUPPORTING",
                            "priority": 10,
                        }
                    ],
                }
            ]
        }
    )

    with pytest.raises(CatalogExpansionError, match="primary_target_context_required"):
        TrainingCatalogPublicationService.validate_context_mapping(
            templates=(draft,),
            context_mapping=mapping,
            active_contexts={"strength_general": Discipline.STRENGTH},
        )
