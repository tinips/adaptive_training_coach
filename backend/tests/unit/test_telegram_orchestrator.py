"""Regression coverage for the universal Telegram LangGraph workspace."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

from app.bot import keyboards, messages
from app.bot.rendering import TelegramResponse
from app.integrations.llm.mock import DeterministicFakeOnboardingModel
from app.schemas.onboarding_goal import UpdatedOnboardingData
from app.workflows.telegram_orchestrator.workspace import (
    TelegramAgentContext,
    TelegramAgentWorkspace,
    _build_graph,
    _provider_message_context,
)


class ParallelCorrectionModel(DeterministicFakeOnboardingModel):
    """Reproduce providers that request correction and dispatch together."""

    def bind_tools(self, tools):  # type: ignore[no-untyped-def]
        del tools

        async def respond(messages):  # type: ignore[no-untyped-def]
            if isinstance(messages[-1], ToolMessage):
                return AIMessage(content="Your birth year has been updated.")
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "update_onboarding_data",
                        "args": {"birth_year": 2003},
                        "id": "parallel-update",
                        "type": "tool_call",
                    },
                    {
                        "name": "dispatch_telegram_input",
                        "args": {
                            "event_type": "text",
                            "content": "sorr my year birth is 2003",
                        },
                        "id": "parallel-dispatch",
                        "type": "tool_call",
                    },
                ],
            )

        return RunnableLambda(respond)


class ForbiddenInvocationModel(DeterministicFakeOnboardingModel):
    """Fail immediately if a deterministic input reaches the provider node."""

    def __init__(self) -> None:
        super().__init__()
        self.invocations = 0

    def bind_tools(self, tools):  # type: ignore[no-untyped-def]
        del tools

        async def respond(messages):  # type: ignore[no-untyped-def]
            del messages
            self.invocations += 1
            raise AssertionError("the LLM node must not run on the fast track")

        return RunnableLambda(respond)


class HeightClarificationModel(DeterministicFakeOnboardingModel):
    """Ask once for height and reject a second provider invocation."""

    def __init__(self) -> None:
        super().__init__()
        self.invocations = 0

    def bind_tools(self, tools):  # type: ignore[no-untyped-def]
        del tools

        async def respond(messages):  # type: ignore[no-untyped-def]
            del messages
            self.invocations += 1
            if self.invocations == 1:
                return AIMessage(
                    content=(
                        "I'd be happy to update your height. What is your height "
                        "in centimeters?"
                    )
                )
            raise AssertionError("the numeric clarification must bypass the LLM")

        return RunnableLambda(respond)


def test_provider_context_keeps_only_three_plain_messages() -> None:
    history = [
        HumanMessage(content="old question"),
        AIMessage(content="old answer"),
        HumanMessage(content="recent question"),
        AIMessage(content="recent answer"),
        HumanMessage(content="latest request"),
    ]

    context = _provider_message_context(history)

    assert [message.content for message in context] == [
        "recent question",
        "recent answer",
        "latest request",
    ]


def test_provider_context_preserves_active_tool_request_and_result() -> None:
    current_request = HumanMessage(content="change my height to 170")
    tool_request = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "update_onboarding_data",
                "args": {"height_cm": 170},
                "id": "height-update",
                "type": "tool_call",
            }
        ],
    )
    tool_result = ToolMessage(
        content='{"updated_fields":{"height_cm":170}}',
        name="update_onboarding_data",
        tool_call_id="height-update",
    )
    history = [
        HumanMessage(content="irrelevant old request"),
        AIMessage(content="irrelevant old response"),
        current_request,
        tool_request,
        tool_result,
    ]

    context = _provider_message_context(history)

    assert context == [current_request, tool_request, tool_result]


@pytest.mark.asyncio
async def test_workspace_dispatches_text_and_callback_and_returns_button_metadata() -> (
    None
):
    events: list[tuple[str, str]] = []

    async def dispatch(event_type: str, content: str) -> TelegramResponse:
        events.append((event_type, content))
        return TelegramResponse(
            f"handled:{content}",
            keyboards.profile_gender_keyboard(),
            edit_existing=event_type == "callback",
        )

    workspace = TelegramAgentWorkspace(model=DeterministicFakeOnboardingModel())
    context = TelegramAgentContext(
        user_id=None,
        dispatcher=dispatch,  # type: ignore[arg-type]
        onboarding_updater=None,
    )
    try:
        first = await workspace.invoke(
            thread_id="telegram:1",
            message=HumanMessage(
                content="73",
                additional_kwargs={"telegram_event_type": "text"},
            ),
            context=context,
        )
        second = await workspace.invoke(
            thread_id="telegram:1",
            message=HumanMessage(
                content="ob:v1:profile:gender:FEMALE",
                additional_kwargs={"telegram_event_type": "callback"},
            ),
            context=context,
        )
    finally:
        await workspace.aclose()

    assert events == [
        ("text", "73"),
        ("callback", "ob:v1:profile:gender:FEMALE"),
    ]
    assert first.text == "handled:73"
    assert first.keyboard is None
    assert first.button_rows[0][0].callback_data == "ob:v1:profile:gender:MALE"
    assert second.edit_existing is True


@pytest.mark.asyncio
async def test_workspace_uses_update_tool_for_explicit_birth_year_correction() -> None:
    user_id = uuid4()
    dispatcher = AsyncMock(return_value=TelegramResponse("not used"))
    updater = AsyncMock(
        return_value=UpdatedOnboardingData(updated_fields={"birth_year": 2004})
    )
    workspace = TelegramAgentWorkspace(model=DeterministicFakeOnboardingModel())
    try:
        response = await workspace.invoke(
            thread_id="telegram:2",
            message=HumanMessage(
                content="change my birth year to 2004",
                additional_kwargs={"telegram_event_type": "text"},
            ),
            context=TelegramAgentContext(
                user_id=user_id,
                dispatcher=dispatcher,
                onboarding_updater=updater,
            ),
        )
    finally:
        await workspace.aclose()

    updater.assert_awaited_once_with(
        user_id=user_id,
        payload={"birth_year": 2004},
    )
    dispatcher.assert_not_awaited()
    assert "updated" in response.text.casefold()


@pytest.mark.asyncio
async def test_workspace_prefers_correction_when_model_requests_two_tools() -> None:
    user_id = uuid4()
    dispatcher = AsyncMock(return_value=TelegramResponse("not used"))
    updater = AsyncMock(
        return_value=UpdatedOnboardingData(updated_fields={"birth_year": 2003})
    )
    workspace = TelegramAgentWorkspace(model=ParallelCorrectionModel())
    try:
        response = await workspace.invoke(
            thread_id="telegram:parallel-correction",
            message=HumanMessage(
                content="sorr my year birth is 2003",
                additional_kwargs={"telegram_event_type": "text"},
            ),
            context=TelegramAgentContext(
                user_id=user_id,
                dispatcher=dispatcher,
                onboarding_updater=updater,
            ),
        )
    finally:
        await workspace.aclose()

    updater.assert_awaited_once_with(
        user_id=user_id,
        payload={"birth_year": 2003},
    )
    dispatcher.assert_not_awaited()
    assert "updated" in response.text.casefold()


@pytest.mark.asyncio
async def test_numeric_profile_answer_bypasses_the_model() -> None:
    events: list[tuple[str, str]] = []
    model = ForbiddenInvocationModel()

    async def dispatch(event_type: str, content: str) -> TelegramResponse:
        events.append((event_type, content))
        if content == "/start":
            return TelegramResponse(messages.PROFILE_WEIGHT_INTAKE)
        return TelegramResponse(messages.PROFILE_HEIGHT_INTAKE)

    workspace = TelegramAgentWorkspace(model=model)
    context = TelegramAgentContext(
        user_id=None,
        dispatcher=dispatch,  # type: ignore[arg-type]
        onboarding_updater=None,
    )
    try:
        await workspace.invoke(
            thread_id="telegram:fast-number",
            message=HumanMessage(
                content="/start",
                additional_kwargs={"telegram_event_type": "text"},
            ),
            context=context,
        )
        response = await workspace.invoke(
            thread_id="telegram:fast-number",
            message=HumanMessage(
                content="73.5",
                additional_kwargs={"telegram_event_type": "text"},
            ),
            context=context,
        )
    finally:
        await workspace.aclose()

    assert events == [("text", "/start"), ("text", "73.5")]
    assert response.text == messages.PROFILE_HEIGHT_INTAKE
    assert model.invocations == 0


@pytest.mark.asyncio
async def test_numeric_height_clarification_updates_the_typed_field() -> None:
    user_id = uuid4()
    model = HeightClarificationModel()
    dispatcher = AsyncMock(return_value=TelegramResponse("not used"))
    updater = AsyncMock(
        return_value=UpdatedOnboardingData(updated_fields={"height_cm": 170})
    )
    workspace = TelegramAgentWorkspace(model=model)
    context = TelegramAgentContext(
        user_id=user_id,
        dispatcher=dispatcher,
        onboarding_updater=updater,
    )
    try:
        clarification = await workspace.invoke(
            thread_id="telegram:height-clarification",
            message=HumanMessage(
                content="change my height",
                additional_kwargs={"telegram_event_type": "text"},
            ),
            context=context,
        )
        response = await workspace.invoke(
            thread_id="telegram:height-clarification",
            message=HumanMessage(
                content="170",
                additional_kwargs={"telegram_event_type": "text"},
            ),
            context=context,
        )
    finally:
        await workspace.aclose()

    assert "height in centimeters" in clarification.text
    updater.assert_awaited_once_with(
        user_id=user_id,
        payload={"height_cm": 170},
    )
    dispatcher.assert_not_awaited()
    assert response.text == "Your height has been updated."
    assert model.invocations == 1


@pytest.mark.asyncio
async def test_strict_callback_bypasses_the_model() -> None:
    model = ForbiddenInvocationModel()
    dispatcher = AsyncMock(
        return_value=TelegramResponse(messages.PROFILE_WEIGHT_INTAKE)
    )
    workspace = TelegramAgentWorkspace(model=model)
    try:
        response = await workspace.invoke(
            thread_id="telegram:fast-callback",
            message=HumanMessage(
                content="FEMALE",
                additional_kwargs={"telegram_event_type": "callback"},
            ),
            context=TelegramAgentContext(
                user_id=None,
                dispatcher=dispatcher,
                onboarding_updater=None,
            ),
        )
    finally:
        await workspace.aclose()

    dispatcher.assert_awaited_once_with(
        "callback",
        "ob:v1:profile:gender:FEMALE",
    )
    assert response.text == messages.PROFILE_WEIGHT_INTAKE
    assert model.invocations == 0


@pytest.mark.asyncio
async def test_workspace_compiles_the_graph_once() -> None:
    model = ForbiddenInvocationModel()
    dispatcher = AsyncMock(return_value=TelegramResponse("ok"))
    workspace = TelegramAgentWorkspace(model=model)
    context = TelegramAgentContext(
        user_id=None,
        dispatcher=dispatcher,
        onboarding_updater=None,
    )
    with patch(
        "app.workflows.telegram_orchestrator.workspace._build_graph",
        wraps=_build_graph,
    ) as compile_graph:
        try:
            await workspace.start()
            for command in ("/start", "/help"):
                await workspace.invoke(
                    thread_id="telegram:singleton",
                    message=HumanMessage(
                        content=command,
                        additional_kwargs={"telegram_event_type": "text"},
                    ),
                    context=context,
                )
        finally:
            await workspace.aclose()

    assert compile_graph.call_count == 1
    assert model.invocations == 0
