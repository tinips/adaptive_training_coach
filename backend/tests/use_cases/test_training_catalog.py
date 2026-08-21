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
from app.workflows.prompts.catalog_expansion import (
    CAPABILITY_EXPANSION_CONTRACT_VERSION,
    CONTEXT_EXPANSION_CONTRACT_VERSION,
    GOAL_CONTEXT_CAPABILITY_EXPANSION,
    NEW_GOAL_CONTEXT_EXPANSION,
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


def test_catalog_expansion_prompts_explicitly_request_json_mode() -> None:
    assert "JSON object" in NEW_GOAL_CONTEXT_EXPANSION
    assert "JSON object" in GOAL_CONTEXT_CAPABILITY_EXPANSION
    assert CONTEXT_EXPANSION_CONTRACT_VERSION == "2"
    assert CAPABILITY_EXPANSION_CONTRACT_VERSION == "3"
    assert "complete training-context structure" in NEW_GOAL_CONTEXT_EXPANSION
    assert "Do not omit essential training contexts" in NEW_GOAL_CONTEXT_EXPANSION
    assert "Do not invent database IDs or relationships" in NEW_GOAL_CONTEXT_EXPANSION
    assert "TARGET context for direct practice" in NEW_GOAL_CONTEXT_EXPANSION
    assert "distinct challenge as its own TARGET context" in NEW_GOAL_CONTEXT_EXPANSION
    assert "use OTHER for" in NEW_GOAL_CONTEXT_EXPANSION
    assert "USE_EXISTING codes must occur" in NEW_GOAL_CONTEXT_EXPANSION
    assert "goal-context pair" in GOAL_CONTEXT_CAPABILITY_EXPANSION
    assert "complete capability set, not only missing" in (
        GOAL_CONTEXT_CAPABILITY_EXPANSION
    )
    assert "Option role is PREFERRED or SUBSTITUTE" in GOAL_CONTEXT_CAPABILITY_EXPANSION
    assert "methods, workouts, drills" in GOAL_CONTEXT_CAPABILITY_EXPANSION
    assert "set of capability codes in capabilities must equal" in (
        GOAL_CONTEXT_CAPABILITY_EXPANSION
    )
    assert "exact code match with active_capabilities" in (
        GOAL_CONTEXT_CAPABILITY_EXPANSION
    )
    assert len(NEW_GOAL_CONTEXT_EXPANSION) < 2_000
    assert len(GOAL_CONTEXT_CAPABILITY_EXPANSION) < 1_500


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
        definitions = ContextCapabilityOutput.model_validate(
            {
                "capabilities": [
                    {
                        "decision": "USE_EXISTING",
                        "code": "running_shoes",
                        "display_name": None,
                        "description": None,
                        "kind": "EQUIPMENT",
                    }
                ],
                "contexts": [
                    {
                        "target_context_code": "running_road",
                        "options": [
                            {
                                "decision": "CREATE",
                                "code": "stair_running",
                                "display_name": "Stair-event running",
                                "execution_context_code": "running_road",
                                "role": "PREFERRED",
                                "priority": 10,
                                "limitations": [],
                                "requirements": [
                                    {
                                        "capability_code": "running_shoes",
                                        "importance": "REQUIRED",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )
        first = await TrainingCatalogPublicationService().publish(
            session=session,
            templates=(draft,),
            context_mapping=mapping,
            capability_definition=definitions,
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
                            "decision": "CREATE",
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
                            "decision": "CREATE",
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


def _marathon_draft() -> GoalTemplateDraft:
    return GoalTemplateDraft(
        code="MARATHON",
        kind=GoalTemplateKind.PRIMARY,
        display_name="Marathon",
        description="Road or general marathon event.",
    )


def _running_road_proposal() -> dict[str, object]:
    return {
        "decision": "USE_EXISTING",
        "code": "running_road",
        "display_name": None,
        "description": None,
        "discipline": "RUNNING",
        "role": "TARGET",
        "priority": 10,
    }


def _running_option_definition(
    *,
    decision: str = "USE_EXISTING",
    execution_context_code: str = "running_road",
    requirement_code: str = "running_shoes",
) -> ContextCapabilityOutput:
    return ContextCapabilityOutput.model_validate(
        {
            "capabilities": [
                {
                    "decision": "USE_EXISTING",
                    "code": requirement_code,
                    "display_name": None,
                    "description": None,
                    "kind": "EQUIPMENT",
                }
            ],
            "contexts": [
                {
                    "target_context_code": "running_road",
                    "options": [
                        {
                            "decision": decision,
                            "code": "outdoor_road",
                            "display_name": (
                                None
                                if decision == "USE_EXISTING"
                                else "Outdoor road running"
                            ),
                            "execution_context_code": execution_context_code,
                            "role": "PREFERRED",
                            "priority": 10,
                            "limitations": [],
                            "requirements": [
                                {
                                    "capability_code": requirement_code,
                                    "importance": "REQUIRED",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )


def _running_mapping(template_code: str) -> GoalContextMappingOutput:
    return GoalContextMappingOutput.model_validate(
        {
            "templates": [
                {
                    "template_code": template_code,
                    "contexts": [_running_road_proposal()],
                }
            ]
        }
    )


def _running_draft(code: str) -> GoalTemplateDraft:
    return GoalTemplateDraft(
        code=code,
        kind=GoalTemplateKind.PRIMARY,
        display_name="Road race",
        description="General preparation for a road race.",
    )


@pytest.mark.asyncio
async def test_existing_execution_option_is_reused_without_new_links(
    catalog_database: async_sessionmaker[AsyncSession],
) -> None:
    draft = _running_draft("ROAD_RACE_OPTION_REUSE")
    async with catalog_database.begin() as session:
        await TrainingCatalogPublicationService().publish(
            session=session,
            templates=(draft,),
            context_mapping=_running_mapping(draft.code),
            capability_definition=_running_option_definition(),
        )
        count = await session.scalar(
            select(func.count(ContextExecutionOption.id)).where(
                ContextExecutionOption.code == "outdoor_road"
            )
        )
        assert count == 1


@pytest.mark.asyncio
async def test_existing_execution_option_reuse_requires_exact_definition(
    catalog_database: async_sessionmaker[AsyncSession],
) -> None:
    draft = _running_draft("ROAD_RACE_OPTION_MISMATCH")
    async with catalog_database.begin() as session:
        with pytest.raises(
            CatalogExpansionError, match="execution_option_definition_mismatch"
        ):
            await TrainingCatalogPublicationService().publish(
                session=session,
                templates=(draft,),
                context_mapping=_running_mapping(draft.code),
                capability_definition=_running_option_definition(
                    execution_context_code="running_treadmill"
                ),
            )


@pytest.mark.asyncio
async def test_create_cannot_collide_with_existing_execution_option(
    catalog_database: async_sessionmaker[AsyncSession],
) -> None:
    draft = _running_draft("ROAD_RACE_OPTION_COLLISION")
    async with catalog_database.begin() as session:
        with pytest.raises(
            CatalogExpansionError, match="execution_option_code_collision"
        ):
            await TrainingCatalogPublicationService().publish(
                session=session,
                templates=(draft,),
                context_mapping=_running_mapping(draft.code),
                capability_definition=_running_option_definition(decision="CREATE"),
            )


@pytest.mark.asyncio
async def test_execution_context_code_cannot_be_a_capability(
    catalog_database: async_sessionmaker[AsyncSession],
) -> None:
    draft = _running_draft("ROAD_RACE_OPTION_CAPABILITY_CONTEXT")
    async with catalog_database.begin() as session:
        with pytest.raises(CatalogExpansionError, match="unknown_execution_context"):
            await TrainingCatalogPublicationService().publish(
                session=session,
                templates=(draft,),
                context_mapping=_running_mapping(draft.code),
                capability_definition=_running_option_definition(
                    execution_context_code="running_shoes"
                ),
            )


def _rowing_definitions(
    *,
    target_code: str = "rowing_general",
    include_running: bool = False,
    rowing_option_decision: str = "CREATE",
) -> ContextCapabilityOutput:
    capabilities: list[dict[str, object]] = [
        {
            "decision": "CREATE",
            "code": "rowing_machine",
            "display_name": "Rowing machine",
            "description": "An indoor rowing machine.",
            "kind": "EQUIPMENT",
        }
    ]
    contexts: list[dict[str, object]] = [
        {
            "target_context_code": target_code,
            "options": [
                {
                    "decision": rowing_option_decision,
                    "code": "indoor_rowing",
                    "display_name": "Indoor rowing",
                    "execution_context_code": target_code,
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
    ]
    if include_running:
        capabilities.append(
            {
                "decision": "USE_EXISTING",
                "code": "running_shoes",
                "display_name": None,
                "description": None,
                "kind": "EQUIPMENT",
            }
        )
        contexts.append(
            {
                "target_context_code": "running_road",
                "options": [
                    {
                        "decision": "USE_EXISTING",
                        "code": "outdoor_road",
                        "display_name": "Outdoor road running",
                        "execution_context_code": "running_road",
                        "role": "PREFERRED",
                        "priority": 10,
                        "limitations": [],
                        "requirements": [
                            {
                                "capability_code": "running_shoes",
                                "importance": "REQUIRED",
                            }
                        ],
                    }
                ],
            }
        )
    return ContextCapabilityOutput.model_validate(
        {
            "capabilities": capabilities,
            "contexts": contexts,
        }
    )


@pytest.mark.asyncio
async def test_publish_attaches_new_context_to_existing_template_idempotently(
    catalog_database: async_sessionmaker[AsyncSession],
) -> None:
    draft = _marathon_draft()
    mapping = GoalContextMappingOutput.model_validate(
        {
            "templates": [
                {
                    "template_code": draft.code,
                    "contexts": [
                        _running_road_proposal(),
                        {
                            "decision": "CREATE",
                            "code": "rowing_general",
                            "display_name": "General rowing",
                            "description": "General rowing practice.",
                            "discipline": "OTHER",
                            "role": "TARGET",
                            "priority": 20,
                        },
                    ],
                }
            ]
        }
    )
    definitions = _rowing_definitions(include_running=True)

    async with catalog_database.begin() as session:
        first = await TrainingCatalogPublicationService().publish(
            session=session,
            templates=(draft,),
            context_mapping=mapping,
            capability_definition=definitions,
        )
        second = await TrainingCatalogPublicationService().publish(
            session=session,
            templates=(draft,),
            context_mapping=mapping,
            capability_definition=_rowing_definitions(
                include_running=True,
                rowing_option_decision="USE_EXISTING",
            ),
        )
        assert first.template_ids[draft.code] == second.template_ids[draft.code]
        marathon = await session.scalar(
            select(GoalTemplate).where(GoalTemplate.code == draft.code)
        )
        rowing = await session.scalar(
            select(TrainingContext).where(TrainingContext.code == "rowing_general")
        )
        assert marathon is not None
        assert rowing is not None
        assert (
            await session.scalar(
                select(func.count(GoalTemplate.id)).where(
                    GoalTemplate.code == draft.code
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(TrainingContext.id)).where(
                    TrainingContext.code == "rowing_general"
                )
            )
            == 1
        )
        link_count = await session.scalar(
            select(func.count(GoalTemplateContext.training_context_id)).where(
                GoalTemplateContext.goal_template_id == marathon.id,
                GoalTemplateContext.training_context_id == rowing.id,
            )
        )
        assert link_count == 1
        option = await session.scalar(
            select(ContextExecutionOption).where(
                ContextExecutionOption.target_context_id == rowing.id
            )
        )
        assert option is not None
        assert (
            await session.scalar(
                select(func.count(ExecutionOptionCapability.execution_option_id)).where(
                    ExecutionOptionCapability.execution_option_id == option.id
                )
            )
            == 1
        )
