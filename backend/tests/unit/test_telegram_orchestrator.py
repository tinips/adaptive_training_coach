"""Regression coverage for the universal Telegram LangGraph workspace."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

from app.bot import keyboards
from app.bot.rendering import TelegramResponse
from app.integrations.llm.mock import DeterministicFakeOnboardingModel
from app.schemas.onboarding_goal import UpdatedOnboardingData
from app.workflows.telegram_orchestrator.workspace import (
    TelegramAgentContext,
    TelegramAgentWorkspace,
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
