"""Execute the semantic catalog dataset through the real onboarding flow."""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from catalog_seed import seed_training_catalog
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from semantic_catalog_dataset import SEMANTIC_GOAL_CASES, SemanticGoalCase
from sqlalchemy import delete, select
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
from app.domain.enums import (
    CatalogItemSource,
    CatalogItemStatus,
    Discipline,
    OnboardingStep,
)
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
from app.workflows.catalog_expansion import LangGraphCatalogExpansionWorkflow
from app.workflows.catalog_expansion.graph import build_catalog_expansion_graph
from app.workflows.onboarding_goal import (
    LangGraphGoalExtractor,
    build_goal_extraction_graph,
)


class SemanticDatasetModel(DeterministicFakeOnboardingModel):
    """Structured semantic fixture that records every real graph boundary."""

    def __init__(self, case: SemanticGoalCase) -> None:
        super().__init__(model_name="semantic-catalog-dataset-model")
        self.case = case
        self.requests: dict[str, list[dict[str, object]]] = {
            "goal": [],
            "map": [],
            "capabilities": [],
        }
        self.responses: dict[str, list[dict[str, object]]] = {
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
            output: object = self._goal_output()
            self.responses["goal"].append(output)
        elif schema is GoalContextMappingOutput:
            request = json.loads(_last_human_text(messages))
            self.requests["map"].append(request)
            output = {
                "templates": [
                    {
                        "template_code": self.case.goal_code,
                        "contexts": [
                            {
                                "decision": item.decision,
                                "code": item.code,
                                "display_name": (
                                    None
                                    if item.decision == "USE_EXISTING"
                                    else item.code.replace("_", " ").title()
                                ),
                                "description": (
                                    None
                                    if item.decision == "USE_EXISTING"
                                    else (
                                        "Training the "
                                        f"{item.code.replace('_', ' ')} modality."
                                    )
                                ),
                                "discipline": item.discipline,
                                "role": item.role,
                                "priority": index * 10 + 10,
                            }
                            for index, item in enumerate(self.case.contexts)
                        ],
                    }
                ]
            }
            self.responses["map"].append(output)
        elif schema is ContextCapabilityOutput:
            request = json.loads(_last_human_text(messages))
            self.requests["capabilities"].append(request)
            output = self._capability_output()
            self.responses["capabilities"].append(output)
        else:
            raise AssertionError(f"unexpected structured schema: {schema}")
        return StructuredModelResponse(
            output=output,
            prompt_tokens=11,
            completion_tokens=19,
        )

    def _goal_output(self) -> dict[str, object]:
        if self.case.goal_existing:
            decision = "USE_EXISTING"
            display_name = None
            description = None
        else:
            decision = "CREATE"
            display_name = self.case.goal_code.replace("_", " ").title()
            description = f"Preparation for {display_name.casefold()}."
        supporting: dict[str, object]
        if self.case.supporting_goal_code is None:
            supporting = {
                "decision": "NONE",
                "code": None,
                "display_name": None,
                "description": None,
            }
        else:
            supporting = {
                "decision": "USE_EXISTING",
                "code": self.case.supporting_goal_code,
                "display_name": None,
                "description": None,
            }
        return {
            "main_goal": self.case.user_text,
            "event_date": None,
            "target_outcome": "Complete the goal comfortably",
            "secondary_priority": (
                "Train strength" if self.case.supporting_goal_code is not None else None
            ),
            "primary_template": {
                "decision": decision,
                "code": self.case.goal_code,
                "display_name": display_name,
                "description": description,
            },
            "supporting_template": supporting,
            "missing_fields": [],
            "ambiguous_fields": [],
            "message_status": "COMPLETE",
        }

    def _capability_output(self) -> dict[str, object]:
        capabilities = [
            {
                "decision": item.decision,
                "code": item.code,
                "display_name": (
                    None
                    if item.decision == "USE_EXISTING"
                    else item.code.replace("_", " ").title()
                ),
                "description": (
                    None
                    if item.decision == "USE_EXISTING"
                    else f"Equipment or access for {item.code.replace('_', ' ')}."
                ),
                "kind": item.kind,
            }
            for item in self.case.capabilities
        ]
        contexts = [
            {
                "target_context_code": context.code,
                "options": [
                    {
                        "code": f"semantic_{context.code}",
                        "display_name": (
                            f"{context.code.replace('_', ' ').title()} execution"
                        ),
                        "execution_context_code": context.code,
                        "role": "PREFERRED",
                        "priority": 10,
                        "limitations": [],
                        "requirements": [
                            {
                                "capability_code": code,
                                "importance": "REQUIRED",
                            }
                            for code in context.capability_codes
                        ],
                    }
                ],
            }
            for context in self.case.contexts
        ]
        return {"capabilities": capabilities, "contexts": contexts}


def _last_human_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            return message.content
    raise AssertionError("structured request did not contain a human payload")


def _identity(telegram_id: int) -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=telegram_id,
        telegram_username="semantic_dataset",
        first_name="Semantic Dataset",
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


async def _prepare_case(
    factory: async_sessionmaker[AsyncSession], case: SemanticGoalCase
) -> None:
    async with factory.begin() as session:
        if not case.goal_existing:
            goal = await session.scalar(
                select(GoalTemplate).where(GoalTemplate.code == case.goal_code)
            )
            if goal is not None:
                await session.execute(
                    delete(GoalTemplateContext).where(
                        GoalTemplateContext.goal_template_id == goal.id
                    )
                )
                await session.delete(goal)
        for code in case.preload_contexts:
            if (
                await session.scalar(
                    select(TrainingContext).where(TrainingContext.code == code)
                )
                is None
            ):
                session.add(
                    TrainingContext(
                        id=uuid.uuid4(),
                        code=code,
                        discipline=Discipline.OTHER,
                        display_name=code.replace("_", " ").title(),
                        description="Canonical rowing training context.",
                        source=CatalogItemSource.SEEDED,
                        status=CatalogItemStatus.ACTIVE,
                        definition_version=1,
                    )
                )


async def _start_goal(onboarding: OnboardingService, athlete: TelegramIdentity) -> None:
    await onboarding.start(athlete)
    await onboarding.confirm_consent(athlete)
    await onboarding.start_profile(athlete)
    await onboarding.handle_text(athlete, "1990")
    await onboarding.choose_gender(athlete, "FEMALE")
    await onboarding.handle_text(athlete, "70")
    await onboarding.handle_text(athlete, "170")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", SEMANTIC_GOAL_CASES, ids=[item.case_id for item in SEMANTIC_GOAL_CASES]
)
async def test_semantic_goal_dataset_through_real_catalog_flow(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    case: SemanticGoalCase,
) -> None:
    _, factory = database
    await _prepare_case(factory, case)
    model = SemanticDatasetModel(case)
    extractor = LangGraphGoalExtractor(
        graph=build_goal_extraction_graph(model=model),
        model=model,
        workflow_name="semantic_dataset_goal_extraction",
    )
    expansion = LangGraphCatalogExpansionWorkflow(
        graph=build_catalog_expansion_graph(model=model),
        model=model,
    )
    onboarding = OnboardingService(
        session_factory=factory,
        goal_extractor=extractor,
        settings=Settings(
            environment="test",
            database_url="sqlite+aiosqlite:///:memory:",
            llm_mode="mock",
        ),
        catalog_expansion_workflow=expansion,
    )
    athlete = _identity(7000 + SEMANTIC_GOAL_CASES.index(case))

    await _start_goal(onboarding, athlete)
    draft = await onboarding.handle_text(athlete, case.user_text)
    goal_draft = draft.answers.get("goal_draft")
    assert isinstance(goal_draft, dict), (
        draft.kind,
        draft.answers,
        model.requests,
    )
    assert goal_draft["primary_template"]["code"] == case.goal_code, (
        draft.kind,
        draft.answers,
        model.requests,
    )
    confirmed = await onboarding.confirm_goal(athlete)
    assert confirmed.kind == "availability_intake", (
        confirmed.kind,
        confirmed.error_code,
        confirmed.answers,
        model.requests,
    )

    assert len(model.requests["goal"]) == 1
    assert len(model.requests["map"]) == (0 if case.goal_existing else 1)
    assert len(model.requests["capabilities"]) == (0 if case.goal_existing else 1)
    if not case.goal_existing:
        mapping_response = model.responses["map"][0]
        mapped_contexts = mapping_response["templates"][0]["contexts"]
        assert {item["code"] for item in mapped_contexts} == {
            item.code for item in case.contexts
        }
        request_contexts = model.requests["capabilities"][0]["new_training_contexts"]
        assert isinstance(request_contexts, list)
        assert {item["code"] for item in request_contexts} == {
            item.code for item in case.contexts
        }

    equipment = await onboarding.handle_text(athlete, "Weekday mornings.")
    assert equipment.capability_review is not None
    reviewed_codes = {item.code for item in equipment.capability_review.options}
    assert (
        set(case.reused_capabilities) | set(case.created_capabilities) <= reviewed_codes
    )

    async with factory() as session:
        goal = await ProfileRepository(session).get_training_goal(
            user_id=confirmed.user_id
        )
        assert goal is not None
        template = await session.scalar(
            select(GoalTemplate).where(GoalTemplate.code == case.goal_code)
        )
        assert template is not None
        assert goal.goal_template_id == template.id
        links_result = await session.execute(
            select(GoalTemplateContext, TrainingContext)
            .join(
                TrainingContext,
                TrainingContext.id == GoalTemplateContext.training_context_id,
            )
            .where(GoalTemplateContext.goal_template_id == template.id)
        )
        links = list(links_result.all())
        persisted_context_codes = {context.code for _, context in links}
        if not case.goal_existing:
            assert persisted_context_codes == {item.code for item in case.contexts}
            assert {
                item.code for item in case.contexts if item.decision == "CREATE"
            } <= {
                item.code
                for item in await session.scalars(
                    select(TrainingContext).where(
                        TrainingContext.code.in_(case.created_contexts)
                    )
                )
            }
        persisted_options = list(
            await session.scalars(
                select(ContextExecutionOption).where(
                    ContextExecutionOption.target_context_id.in_(
                        [context.id for _, context in links]
                    )
                )
            )
        )
        requirement_rows = await session.execute(
            select(ExecutionOptionCapability, Capability)
            .join(Capability, Capability.id == ExecutionOptionCapability.capability_id)
            .where(
                ExecutionOptionCapability.execution_option_id.in_(
                    [item.id for item in persisted_options]
                )
            )
        )
        persisted_capabilities = {
            capability.code for _, capability in requirement_rows.all()
        }
        if not case.goal_existing:
            assert set(case.reused_capabilities) | set(case.created_capabilities) <= (
                persisted_capabilities
            )
