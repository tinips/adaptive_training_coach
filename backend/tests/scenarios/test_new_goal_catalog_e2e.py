"""End-to-end evidence for a new goal's catalog and equipment definition."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from catalog_seed import seed_training_catalog
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.db.base import Base
from app.db.models import (
    Capability,
    ContextExecutionOption,
    ExecutionOptionCapability,
    GoalTemplate,
    GoalTemplateContext,
    TrainingContext,
)
from app.domain.enums import OnboardingStep
from app.integrations.llm.mock import DeterministicFakeOnboardingModel
from app.integrations.llm.models import (
    GoalExtractionPatch,
    StructuredModelResponse,
    StructuredOutputSchema,
)
from app.repositories.profiles import ProfileRepository
from app.schemas.catalog_expansion import (
    ContextCapabilityOutput,
    GoalContextMappingOutput,
)
from app.schemas.common import TelegramIdentity
from app.services.onboarding import OnboardingService
from app.workflows.catalog_expansion import (
    LangGraphCatalogExpansionWorkflow,
)
from app.workflows.catalog_expansion.graph import build_catalog_expansion_graph
from app.workflows.onboarding_goal import (
    LangGraphGoalExtractor,
    build_goal_extraction_graph,
)

_HYROX_CHALLENGES = (
    (
        "hyrox_ski_erg",
        "SkiErg challenge",
        "HYROX SkiErg station.",
        "OTHER",
        "ski_ergometer",
        "SkiErg equipment",
        "EQUIPMENT",
    ),
    (
        "hyrox_sled_push_pull",
        "Sled push and pull",
        "HYROX sled push and sled pull station.",
        "STRENGTH",
        "sled_push_pull_equipment",
        "Sled push and pull equipment",
        "EQUIPMENT",
    ),
    (
        "hyrox_burpee_broad_jump",
        "Burpee broad jump challenge",
        "HYROX burpee broad-jump station.",
        "OTHER",
        "burpee_broad_jump_space",
        "Burpee broad-jump space",
        "FACILITY",
    ),
    (
        "hyrox_row",
        "Rowing challenge",
        "HYROX rowing station.",
        "OTHER",
        "rowing_ergometer",
        "Rowing ergometer",
        "EQUIPMENT",
    ),
    (
        "hyrox_farmer_carry",
        "Farmer carry challenge",
        "HYROX farmer-carry station.",
        "STRENGTH",
        "farmer_carry_weights",
        "Farmer-carry weights",
        "EQUIPMENT",
    ),
    (
        "hyrox_sandbag_lunge",
        "Sandbag lunge challenge",
        "HYROX sandbag-lunge station.",
        "STRENGTH",
        "sandbag",
        "Sandbag",
        "EQUIPMENT",
    ),
    (
        "hyrox_wall_balls",
        "Wall-ball challenge",
        "HYROX wall-ball station.",
        "STRENGTH",
        "wall_ball",
        "Wall ball",
        "EQUIPMENT",
    ),
)


class SemanticHYROXModel(DeterministicFakeOnboardingModel):
    """A structured provider double that records the real graph boundaries."""

    def __init__(self) -> None:
        super().__init__(model_name="semantic-hyrox-test-model")
        self.requests: dict[str, list[dict[str, object]]] = {
            "goal": [],
            "map": [],
            "capabilities": [],
        }

    async def ainvoke_structured(
        self,
        *,
        step: OnboardingStep,
        schema: StructuredOutputSchema,
        messages: list[BaseMessage],
        config: RunnableConfig,
    ) -> StructuredModelResponse:
        del step, config
        if schema is GoalExtractionPatch:
            self.requests["goal"].append({"user_text": _last_human_text(messages)})
            output: object = {
                "main_goal": "Prepare for HYROX",
                "event_date": None,
                "target_outcome": "Finish the event comfortably",
                "secondary_priority": None,
                "primary_template": {
                    "decision": "CREATE",
                    "code": "CUSTOM_HYROX",
                    "display_name": "HYROX",
                    "description": (
                        "HYROX hybrid race combining repeated running with "
                        "functional stations."
                    ),
                },
                "supporting_template": {
                    "decision": "NONE",
                    "code": None,
                    "display_name": None,
                    "description": None,
                },
                "missing_fields": [],
                "ambiguous_fields": [],
                "message_status": "COMPLETE",
            }
        elif schema is GoalContextMappingOutput:
            request = json.loads(_last_human_text(messages))
            self.requests["map"].append(request)
            contexts = [
                {
                    "decision": "USE_EXISTING",
                    "code": "running_road",
                    "display_name": None,
                    "description": None,
                    "discipline": "RUNNING",
                    "role": "TARGET",
                    "priority": 10,
                }
            ]
            contexts.extend(
                {
                    "decision": "USE_EXISTING",
                    "code": code,
                    "display_name": None,
                    "description": None,
                    "discipline": discipline,
                    "role": "TARGET",
                    "priority": index * 10 + 20,
                }
                for index, (
                    code,
                    display_name,
                    description,
                    discipline,
                    _,
                    _,
                    _,
                ) in enumerate(_HYROX_CHALLENGES)
            )
            output = {
                "templates": [
                    {
                        "template_code": "CUSTOM_HYROX",
                        "contexts": contexts,
                    }
                ]
            }
        elif schema is ContextCapabilityOutput:
            request = json.loads(_last_human_text(messages))
            self.requests["capabilities"].append(request)
            capabilities: list[dict[str, object]] = [
                {
                    "decision": "USE_EXISTING",
                    "code": "running_shoes",
                    "display_name": None,
                    "description": None,
                    "kind": "EQUIPMENT",
                },
                {
                    "decision": "USE_EXISTING",
                    "code": "gym_access",
                    "display_name": None,
                    "description": None,
                    "kind": "FACILITY",
                },
            ]
            capabilities.extend(
                {
                    "decision": "USE_EXISTING",
                    "code": capability_code,
                    "display_name": None,
                    "description": None,
                    "kind": capability_kind,
                }
                for (
                    _,
                    display_name,
                    _,
                    _,
                    capability_code,
                    capability_name,
                    capability_kind,
                ) in _HYROX_CHALLENGES
            )
            definitions: list[dict[str, object]] = [
                {
                    "target_context_code": "running_road",
                    "options": [
                        {
                            "decision": "CREATE",
                            "code": "hyrox_running",
                            "display_name": "HYROX running",
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
            ]
            definitions.extend(
                {
                    "target_context_code": code,
                    "options": [
                        {
                            "decision": "USE_EXISTING",
                            "code": f"{code}_execution",
                            "display_name": None,
                            "execution_context_code": code,
                            "role": "PREFERRED",
                            "priority": 10,
                            "limitations": [],
                            "requirements": [
                                {
                                    "capability_code": "gym_access",
                                    "importance": "REQUIRED",
                                },
                                {
                                    "capability_code": capability_code,
                                    "importance": "REQUIRED",
                                },
                            ],
                        }
                    ],
                }
                for (
                    code,
                    display_name,
                    _,
                    _,
                    capability_code,
                    _,
                    _,
                ) in _HYROX_CHALLENGES
            )
            output = {"capabilities": capabilities, "contexts": definitions}
        else:
            raise AssertionError(f"unexpected structured schema: {schema}")
        return StructuredModelResponse(
            output=output,
            prompt_tokens=11,
            completion_tokens=19,
        )


def _last_human_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            return message.content
    raise AssertionError("structured request did not contain a human payload")


def _identity(telegram_id: int) -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=telegram_id,
        telegram_username="hyrox_e2e",
        first_name="HYROX E2E",
        language_code="en",
    )


@pytest_asyncio.fixture
async def database() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory.begin() as session:
        await seed_training_catalog(session)
    yield engine, factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_new_hyrox_goal_uses_semantic_catalog_through_equipment_review(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    model = SemanticHYROXModel()
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        llm_mode="mock",
    )
    goal_extractor = LangGraphGoalExtractor(
        graph=build_goal_extraction_graph(model=model),
        model=model,
        workflow_name="test_goal_extraction",
    )
    expansion = LangGraphCatalogExpansionWorkflow(
        graph=build_catalog_expansion_graph(model=model),
        model=model,
    )
    onboarding = OnboardingService(
        session_factory=factory,
        goal_extractor=goal_extractor,
        settings=settings,
        catalog_expansion_workflow=expansion,
    )
    athlete = _identity(9928)

    await onboarding.start(athlete)
    await onboarding.confirm_consent(athlete)
    await onboarding.start_profile(athlete)
    await onboarding.handle_text(athlete, "1990")
    await onboarding.choose_gender(athlete, "FEMALE")
    await onboarding.handle_text(athlete, "70")
    await onboarding.handle_text(athlete, "170")
    draft = await onboarding.handle_text(athlete, "I want to prepare for HYROX.")
    assert draft.answers["goal_draft"]["primary_template"]["decision"] == "CREATE"

    confirmed = await onboarding.confirm_goal(athlete)
    assert confirmed.kind == "availability_intake"
    equipment = await onboarding.handle_text(athlete, "Weekday mornings.")
    assert equipment.capability_review is not None
    reviewed_codes = {item.code for item in equipment.capability_review.options}
    challenge_capability_codes = {item[4] for item in _HYROX_CHALLENGES}
    assert {
        "running_shoes",
        "gym_access",
    } | challenge_capability_codes <= reviewed_codes

    assert model.requests["map"][0]["new_templates"] == [
        {
            "code": "CUSTOM_HYROX",
            "kind": "PRIMARY",
            "display_name": "HYROX",
            "description": (
                "HYROX hybrid race combining repeated running with functional stations."
            ),
        }
    ]
    capability_request = model.requests["capabilities"][0]
    assert capability_request["goals"][0]["code"] == "CUSTOM_HYROX"
    assert {item["code"] for item in capability_request["new_training_contexts"]} == {
        "running_road",
        *{item[0] for item in _HYROX_CHALLENGES},
    }

    async with factory() as session:
        goal = await ProfileRepository(session).get_training_goal(
            user_id=confirmed.user_id
        )
        template = await session.scalar(
            select(GoalTemplate).where(GoalTemplate.code == "CUSTOM_HYROX")
        )
        assert goal is not None and template is not None
        assert goal.goal_template_id == template.id

        links = await session.execute(
            select(GoalTemplateContext, TrainingContext)
            .join(
                TrainingContext,
                TrainingContext.id == GoalTemplateContext.training_context_id,
            )
            .where(GoalTemplateContext.goal_template_id == template.id)
        )
        context_rows = {context.code: relation for relation, context in links.all()}
        challenge_context_codes = {item[0] for item in _HYROX_CHALLENGES}
        assert set(context_rows) == {"running_road"} | challenge_context_codes

        options = list(
            await session.scalars(
                select(ContextExecutionOption).where(
                    ContextExecutionOption.target_context_id.in_(
                        [
                            relation.training_context_id
                            for relation in context_rows.values()
                        ]
                    )
                )
            )
        )
        assert {option.code for option in options} >= {
            "hyrox_running",
            *(f"{code}_execution" for code in challenge_context_codes),
        }

        requirements = await session.execute(
            select(ExecutionOptionCapability, Capability)
            .join(Capability, Capability.id == ExecutionOptionCapability.capability_id)
            .where(
                ExecutionOptionCapability.execution_option_id.in_(
                    [option.id for option in options]
                )
            )
        )
        requirement_codes = {capability.code for _, capability in requirements.all()}
        assert {"running_shoes", "gym_access"} | challenge_capability_codes <= (
            requirement_codes
        )
