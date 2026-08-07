"""Live model and PostgreSQL evaluations for onboarding modifications.

These tests are deliberately opt-in and never substitute a fake model or an
in-memory database. Set ``RUN_LIVE_AGENT_TESTS=1`` and point
``LIVE_AGENT_TEST_DATABASE_URL`` at an already migrated, disposable PostgreSQL
database whose name contains ``test``. Provider settings use ``LIVE_LLM_*`` or
the application's OpenAI-compatible ``LLM_*`` environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.domain.enums import AthleteGender, UserStatus
from app.integrations.llm.live import OpenAICompatibleOnboardingModel
from app.repositories.profiles import ProfileRepository
from app.repositories.users import UserRepository
from app.services.onboarding import OnboardingService
from app.workflows.onboarding_goal.graph import (
    CompiledGoalExtractionGraph,
    LangGraphGoalExtractor,
    build_goal_extraction_graph,
)
from app.workflows.onboarding_goal.nodes import (
    build_onboarding_modification_messages,
)
from app.workflows.onboarding_goal.state import GoalExtractionGraphState

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_AGENT_TESTS") != "1",
        reason="set RUN_LIVE_AGENT_TESTS=1 to call the live model and PostgreSQL",
    ),
]

_INITIAL_GOAL = "10k race"
_INITIAL_TARGET = "Finish comfortably"
_INITIAL_DESCRIPTION = "Initial live-test goal"


@dataclass(frozen=True, slots=True)
class StoredOnboardingData:
    age: int
    weight_kg: float | None
    main_goal: str
    event_date: date | None
    target_outcome: str
    original_description: str


@dataclass(frozen=True, slots=True)
class LiveAgentHarness:
    graph: CompiledGoalExtractionGraph
    onboarding: OnboardingService
    session_factory: async_sessionmaker[AsyncSession]
    user_id: UUID

    async def send(
        self,
        user_text: str,
        *,
        previous: GoalExtractionGraphState | None = None,
    ) -> GoalExtractionGraphState:
        if previous is None:
            messages = build_onboarding_modification_messages(user_text)
            onboarding_updated = False
        else:
            messages = [*previous["messages"], HumanMessage(content=user_text)]
            onboarding_updated = previous.get("onboarding_updated", False)

        raw_state = await self.graph.ainvoke(
            {
                "user_id": self.user_id,
                "action": "MODIFY_ONBOARDING_DATA",
                "user_text": user_text,
                "messages": messages,
                "onboarding_updater": self.onboarding.update_onboarding_data,
                "onboarding_updated": onboarding_updated,
            },
            config={"recursion_limit": 12},
        )
        state = cast(GoalExtractionGraphState, raw_state)
        assert state.get("error_code") is None, state.get("error_code")
        return state

    async def stored_data(self) -> StoredOnboardingData:
        async with self.session_factory() as session:
            repository = ProfileRepository(session)
            profile = await repository.get_athlete_profile(user_id=self.user_id)
            goal = await repository.get_training_goal(user_id=self.user_id)
        assert profile is not None
        assert goal is not None
        return StoredOnboardingData(
            age=profile.age,
            weight_kg=profile.weight_kg,
            main_goal=goal.main_goal,
            event_date=goal.event_date,
            target_outcome=goal.target_outcome,
            original_description=goal.original_description,
        )


@pytest_asyncio.fixture
async def live_agent_harness() -> LiveAgentHarness:
    database_url = _required_test_database_url()
    api_key, base_url, model_name = _live_provider_configuration()
    engine = create_async_engine(database_url, pool_pre_ping=True)
    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    model = OpenAICompatibleOnboardingModel(
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        timeout_seconds=45,
    )
    graph = build_goal_extraction_graph(model=model)
    extractor = LangGraphGoalExtractor(
        graph=graph,
        model=model,
        workflow_name="live_agent_onboarding_test",
        timeout_seconds=60,
    )
    settings = Settings(
        environment="test",
        database_url=database_url,
        llm_mode="live",
        llm_api_key=api_key,
        llm_base_url=base_url,
        llm_model=model_name,
    )
    onboarding = OnboardingService(
        session_factory=factory,
        goal_extractor=extractor,
        settings=settings,
    )
    telegram_user_id = 8_000_000_000 + uuid4().int % 1_000_000_000

    async with factory.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=telegram_user_id,
            telegram_username=None,
            first_name="LiveAgentTest",
            language_code="en",
            timezone="UTC",
        )
        await UserRepository(session).update_status(
            user_id=user.id,
            status=UserStatus.ONBOARDING_COMPLETED,
        )
        profiles = ProfileRepository(session)
        await profiles.upsert_mandatory_athlete_profile(
            user_id=user.id,
            birth_year=1990,
            gender=AthleteGender.OTHER_UNSPECIFIED,
            weight_kg=70.0,
            height_cm=175.0,
        )
        await profiles.upsert_conversational_training_goal(
            user_id=user.id,
            main_goal=_INITIAL_GOAL,
            event_date=None,
            target_outcome=_INITIAL_TARGET,
            secondary_priority=None,
            original_description=_INITIAL_DESCRIPTION,
        )

    harness = LiveAgentHarness(
        graph=graph,
        onboarding=onboarding,
        session_factory=factory,
        user_id=user.id,
    )
    try:
        yield harness
    finally:
        async with factory.begin() as session:
            await UserRepository(session).delete(user_id=user.id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_live_chaotic_mind_mid_flight_correction(
    live_agent_harness: LiveAgentHarness,
) -> None:
    turn_one = await live_agent_harness.send("I want to change my goal")

    assert _tool_payloads(turn_one) == []
    assert turn_one["outcome"] == "no_onboarding_update"
    assert "goal" in _last_ai_text(turn_one).casefold()
    assert "?" in _last_ai_text(turn_one)

    turn_two = await live_agent_harness.send(
        "Actually, forget it, let's update my weight to 82kg first",
        previous=turn_one,
    )

    assert _new_tool_payloads(turn_one, turn_two) == [{"weight_kg": 82.0}]
    after_weight = await live_agent_harness.stored_data()
    assert after_weight.weight_kg == pytest.approx(82.0)
    assert after_weight.main_goal == _INITIAL_GOAL
    assert after_weight.target_outcome == _INITIAL_TARGET

    turn_three = await live_agent_harness.send(
        "Okay, now let's set the goal to an Ironman",
        previous=turn_two,
    )

    assert _new_tool_payloads(turn_two, turn_three) == [{"main_goal": "Ironman"}]
    final_data = await live_agent_harness.stored_data()
    assert final_data.weight_kg == pytest.approx(82.0)
    assert final_data.main_goal == "Ironman"
    assert final_data.target_outcome == _INITIAL_TARGET
    assert turn_three["updated_fields"] == ["main_goal"]


@pytest.mark.asyncio
async def test_live_vague_ambiguity_requires_concrete_goal(
    live_agent_harness: LiveAgentHarness,
) -> None:
    turn_one = await live_agent_harness.send("Change my goal to something fast")

    assert _tool_payloads(turn_one) == []
    clarification = _last_ai_text(turn_one).casefold()
    assert "?" in clarification
    assert "race" in clarification or "distance" in clarification
    unchanged = await live_agent_harness.stored_data()
    assert unchanged.main_goal == _INITIAL_GOAL

    turn_two = await live_agent_harness.send(
        "I mean a 5k race",
        previous=turn_one,
    )

    assert _new_tool_payloads(turn_one, turn_two) == [{"main_goal": "5k race"}]
    updated = await live_agent_harness.stored_data()
    assert updated.main_goal == "5k race"
    assert updated.weight_kg == pytest.approx(70.0)
    assert turn_two["updated_fields"] == ["main_goal"]


@pytest.mark.asyncio
async def test_live_all_in_one_cross_table_update(
    live_agent_harness: LiveAgentHarness,
) -> None:
    expected_event_date = _next_future_date(month=10, day=15)
    turn = await live_agent_harness.send(
        "I am 35 years old, weight 74kg, and my goal is the Barcelona "
        "Marathon on October 15th"
    )

    assert _tool_payloads(turn) == [
        {
            "main_goal": "Barcelona Marathon",
            "age": 35,
            "weight_kg": 74.0,
            "event_date": expected_event_date.isoformat(),
        }
    ]
    stored = await live_agent_harness.stored_data()
    assert stored.age == 35
    assert stored.weight_kg == pytest.approx(74.0)
    assert stored.main_goal == "Barcelona Marathon"
    assert stored.event_date == expected_event_date
    assert stored.target_outcome == _INITIAL_TARGET
    assert stored.original_description == _INITIAL_DESCRIPTION
    assert turn["updated_fields"] == [
        "main_goal",
        "age",
        "weight_kg",
        "event_date",
    ]


def _required_test_database_url() -> str:
    database_url = os.getenv("LIVE_AGENT_TEST_DATABASE_URL")
    if not database_url:
        pytest.fail(
            "LIVE_AGENT_TEST_DATABASE_URL is required when live tests are enabled"
        )
    parsed = make_url(database_url)
    if parsed.drivername != "postgresql+asyncpg":
        pytest.fail("live agent tests require PostgreSQL through asyncpg")
    database_name = parsed.database or ""
    if "test" not in database_name.casefold():
        pytest.fail("live agent database name must contain 'test'")
    return database_url


def _live_provider_configuration() -> tuple[SecretStr, str | None, str]:
    settings = Settings()
    explicit_key = os.getenv("LIVE_LLM_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    if explicit_key:
        api_key = SecretStr(explicit_key)
        base_url = os.getenv("LIVE_LLM_BASE_URL", settings.llm_base_url or "") or None
        model_name = os.getenv("LIVE_LLM_MODEL", settings.llm_model)
    elif openai_key:
        api_key = SecretStr(openai_key)
        base_url = os.getenv("LIVE_LLM_BASE_URL") or None
        model_name = os.getenv(
            "LIVE_LLM_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        )
    elif settings.llm_api_key is not None:
        api_key = settings.llm_api_key
        base_url = settings.llm_base_url or None
        model_name = settings.llm_model
    else:
        pytest.fail(
            "a live OpenAI-compatible credential is required via LIVE_LLM_API_KEY, "
            "OPENAI_API_KEY, or LLM_API_KEY"
        )
    return api_key, base_url, model_name


def _tool_payloads(state: GoalExtractionGraphState) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for message in state["messages"]:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            if call["name"] != "update_onboarding_data":
                continue
            arguments = call["args"]
            assert isinstance(arguments, dict)
            payloads.append(arguments)
    return payloads


def _new_tool_payloads(
    previous: GoalExtractionGraphState,
    current: GoalExtractionGraphState,
) -> list[dict[str, object]]:
    previous_count = len(_tool_payloads(previous))
    return _tool_payloads(current)[previous_count:]


def _last_ai_text(state: GoalExtractionGraphState) -> str:
    message = state["messages"][-1]
    assert isinstance(message, AIMessage)
    content: str | list[str | dict[str, object]] = message.content
    if isinstance(content, str):
        text = content.strip()
    else:
        pieces: list[str] = []
        for block in content:
            if isinstance(block, str):
                pieces.append(block)
            elif isinstance(block.get("text"), str):
                pieces.append(cast(str, block["text"]))
        text = " ".join(pieces).strip()
    assert text
    return text


def _next_future_date(*, month: int, day: int) -> date:
    today = date.today()
    candidate = date(today.year, month, day)
    if candidate <= today:
        candidate = candidate.replace(year=candidate.year + 1)
    return candidate
