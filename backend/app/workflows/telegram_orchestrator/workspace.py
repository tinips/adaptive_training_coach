"""One persistent LangGraph workspace for all Telegram text and callbacks."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict, cast
from uuid import UUID

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.tools import InjectedToolArg, tool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, ToolRuntime, tools_condition
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.bot.rendering import TelegramButtonSpec, TelegramResponse
from app.integrations.llm.models import StructuredOnboardingModel
from app.schemas.onboarding_goal import OnboardingUpdateHandler

TelegramEventType = Literal["text", "callback"]
TelegramInputDispatcher = Callable[
    [TelegramEventType, str], Awaitable[TelegramResponse]
]
TelegramPresentationLoader = Callable[[], Awaitable[TelegramResponse]]


class TelegramButtonPayload(TypedDict):
    """Checkpoint-safe presentation metadata for one inline button."""

    text: str
    callback_data: str | None
    url: str | None


class TelegramAgentState(TypedDict, total=False):
    """Only serializable conversation and presentation state is checkpointed."""

    messages: Annotated[list[BaseMessage], add_messages]
    response_text: str | None
    response_button_rows: list[list[TelegramButtonPayload]]
    edit_existing: bool
    clear_agent_thread: bool


@dataclass(frozen=True, slots=True)
class TelegramAgentContext:
    """Per-invocation dependencies; LangGraph never checkpoints these values."""

    user_id: UUID | None
    dispatcher: TelegramInputDispatcher
    onboarding_updater: OnboardingUpdateHandler | None
    presentation_loader: TelegramPresentationLoader | None = None


class DispatchTelegramInputSchema(BaseModel):
    """Exact Telegram event selected by the agent for application dispatch."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    event_type: TelegramEventType = Field(
        description="The exact Telegram event type from the current HumanMessage."
    )
    content: str = Field(
        min_length=1,
        max_length=4096,
        description=(
            "The exact current HumanMessage content, preserved without rewriting."
        ),
    )
    runtime: Annotated[
        ToolRuntime[TelegramAgentContext, TelegramAgentState] | None,
        InjectedToolArg,
    ] = None


class UpdateOnboardingAgentSchema(BaseModel):
    """Sparse correction fields available to the global agent."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    main_goal: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "The concrete primary athletic goal, race, or distance requested by "
            "the athlete, such as 'marathon' or '5k race'."
        ),
    )
    target_outcome: str | None = Field(
        default=None,
        max_length=500,
        description="The athlete's requested performance result or completion aim.",
    )
    event_date: str | None = Field(
        default=None,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
    )
    age: int | None = Field(default=None, ge=16, le=100)
    birth_year: int | None = Field(default=None, ge=1940, le=2008)
    gender: Literal["MALE", "FEMALE", "OTHER_UNSPECIFIED"] | None = None
    weight_kg: float | None = Field(default=None, ge=40, le=200)
    height_cm: int | None = Field(default=None, ge=120, le=230)
    runtime: Annotated[
        ToolRuntime[TelegramAgentContext, TelegramAgentState] | None,
        InjectedToolArg,
    ] = None


@tool("dispatch_telegram_input", args_schema=DispatchTelegramInputSchema)
async def dispatch_telegram_input(
    runtime: ToolRuntime[TelegramAgentContext, TelegramAgentState],
    *,
    event_type: TelegramEventType,
    content: str,
) -> Command[Literal["agent"]]:
    """Dispatch ordinary input; never use for explicit athlete-data changes."""

    response = await runtime.context.dispatcher(event_type, content)
    rows = _serialize_keyboard(response)
    tool_content = json.dumps(
        {
            "response_text": response.text,
            "button_rows": rows,
            "edit_existing": response.edit_existing,
            "clear_agent_thread": response.clear_agent_thread,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=tool_content,
                    name="dispatch_telegram_input",
                    tool_call_id=runtime.tool_call_id or "dispatch-telegram-input",
                )
            ],
            "response_text": response.text,
            "response_button_rows": rows,
            "edit_existing": response.edit_existing,
            "clear_agent_thread": response.clear_agent_thread,
        }
    )


@tool("update_onboarding_data", args_schema=UpdateOnboardingAgentSchema)
async def update_onboarding_data_from_agent(
    runtime: ToolRuntime[TelegramAgentContext, TelegramAgentState],
    **kwargs: object,
) -> Command[Literal["agent"]]:
    """Persist an explicit athlete-data change during or after onboarding."""

    updater = runtime.context.onboarding_updater
    user_id = runtime.context.user_id
    if updater is None or user_id is None:
        raise RuntimeError("onboarding update is unavailable for this thread")
    payload = {
        key: cast(JsonValue, value)
        for key, value in kwargs.items()
        if value is not None
    }
    updated = await updater(user_id=user_id, payload=payload)
    presentation = (
        await runtime.context.presentation_loader()
        if runtime.context.presentation_loader is not None
        else None
    )
    rows = _serialize_keyboard(presentation) if presentation is not None else []
    content = json.dumps(
        {
            "updated_fields": updated.updated_fields,
            "current_prompt": presentation.text if presentation is not None else None,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=content,
                    name="update_onboarding_data",
                    tool_call_id=runtime.tool_call_id or "update-onboarding-data",
                )
            ],
            "response_button_rows": rows,
            "edit_existing": False,
            "clear_agent_thread": False,
        }
    )


def _serialize_keyboard(
    response: TelegramResponse,
) -> list[list[TelegramButtonPayload]]:
    if response.button_rows:
        return [
            [
                {
                    "text": button.text,
                    "callback_data": button.callback_data,
                    "url": button.url,
                }
                for button in row
            ]
            for row in response.button_rows
        ]
    if response.keyboard is None:
        return []
    return [
        [
            {
                "text": button.text,
                "callback_data": (
                    button.callback_data
                    if isinstance(button.callback_data, str)
                    else None
                ),
                "url": button.url if isinstance(button.url, str) else None,
            }
            for button in row
        ]
        for row in response.keyboard.inline_keyboard
    ]


def _system_prompt() -> SystemMessage:
    return SystemMessage(
        content=(
            "You are the global Adaptive Endurance Coach orchestrator. Every current "
            "HumanMessage is a Telegram event and must be handled through exactly "
            "one tool call. Never call both tools for the same HumanMessage. "
            "The latest HumanMessage has absolute priority over earlier questions "
            "and topics in the conversation. Any explicit request to change, "
            "update, correct, replace, or set a supported athlete field MUST call "
            "update_onboarding_data, even while another onboarding question is "
            "active and even after onboarding is complete. Never send such a "
            "request to dispatch_telegram_input. For example, 'change my goal to "
            "a marathon' means update_onboarding_data(main_goal='marathon'), and "
            "'actually set my weight to 82 kg' means "
            "update_onboarding_data(weight_kg=82). "
            "For ordinary answers, commands, and callback values, call "
            "dispatch_telegram_input exactly once, preserving both the event type "
            "and content byte-for-byte. Use update_onboarding_data only when the "
            "athlete explicitly corrects previously supplied onboarding data. "
            "Never reject a callback yourself and never invent callback semantics. "
            "After a tool result, respond naturally and concisely; application "
            "presentation metadata in the tool result remains authoritative. After "
            "an onboarding correction, confirm the saved fields and continue with "
            "the current_prompt from the tool result."
        )
    )


def _enforce_single_tool_call(response: AIMessage) -> AIMessage:
    """Choose one authoritative action when a provider emits parallel calls."""

    calls = response.tool_calls
    if len(calls) <= 1:
        return response
    update_call = next(
        (call for call in calls if call["name"] == "update_onboarding_data"),
        None,
    )
    selected = update_call or calls[0]
    return response.model_copy(update={"tool_calls": [selected]})


CompiledTelegramGraph = CompiledStateGraph[
    TelegramAgentState,
    TelegramAgentContext,
    TelegramAgentState,
    TelegramAgentState,
]


def _build_graph(
    *,
    model: StructuredOnboardingModel,
    checkpointer: BaseCheckpointSaver[str],
) -> CompiledTelegramGraph:
    tools = [dispatch_telegram_input, update_onboarding_data_from_agent]
    tool_model = model.bind_tools(tools)

    async def prepare(_: TelegramAgentState) -> TelegramAgentState:
        return {
            "response_text": None,
            "response_button_rows": [],
            "edit_existing": False,
        }

    async def agent(
        state: TelegramAgentState,
        config: RunnableConfig,
    ) -> TelegramAgentState:
        response = await tool_model.ainvoke(
            [_system_prompt(), *state["messages"]],
            config=config,
        )
        return {"messages": [_enforce_single_tool_call(response)]}

    builder: StateGraph[
        TelegramAgentState,
        TelegramAgentContext,
        TelegramAgentState,
        TelegramAgentState,
    ] = StateGraph(TelegramAgentState, context_schema=TelegramAgentContext)
    builder.add_node("prepare", RunnableLambda(prepare))
    builder.add_node("agent", RunnableLambda(agent))
    builder.add_node("tools", ToolNode(tools, handle_tool_errors=False))
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "agent")
    builder.add_conditional_edges(
        "agent",
        tools_condition,
        {"tools": "tools", "__end__": END},
    )
    builder.add_edge("tools", "agent")
    return builder.compile(
        checkpointer=checkpointer,
        name="telegram_global_orchestrator",
    )


class TelegramAgentWorkspace:
    """Own the compiled graph and its PostgreSQL checkpoint connection."""

    def __init__(
        self,
        *,
        model: StructuredOnboardingModel,
        postgres_dsn: str | None = None,
    ) -> None:
        self._model = model
        self._postgres_dsn = postgres_dsn
        self._graph: CompiledTelegramGraph | None = None
        self._postgres_context: (
            AbstractAsyncContextManager[AsyncPostgresSaver] | None
        ) = None
        self._checkpointer: BaseCheckpointSaver[str] | None = None
        self._start_lock = asyncio.Lock()

    async def start(self) -> None:
        """Open and migrate the native checkpointer once per bot process."""

        async with self._start_lock:
            if self._graph is not None:
                return
            if self._postgres_dsn is None:
                checkpointer: BaseCheckpointSaver[str] = InMemorySaver()
            else:
                postgres_context = AsyncPostgresSaver.from_conn_string(
                    self._postgres_dsn
                )
                postgres = await postgres_context.__aenter__()
                await postgres.setup()
                self._postgres_context = postgres_context
                checkpointer = postgres
            self._checkpointer = checkpointer
            self._graph = _build_graph(model=self._model, checkpointer=checkpointer)

    async def invoke(
        self,
        *,
        thread_id: str,
        message: HumanMessage,
        context: TelegramAgentContext,
    ) -> TelegramResponse:
        """Resume one durable thread and process exactly one Telegram event."""

        await self.start()
        graph = self._graph
        if graph is None:
            raise RuntimeError("telegram agent workspace failed to initialize")
        raw = await graph.ainvoke(
            {"messages": [message]},
            config={"configurable": {"thread_id": thread_id}},
            context=context,
        )
        state = cast(TelegramAgentState, raw)
        response_text = state.get("response_text") or _last_ai_text(state)
        rows = tuple(
            tuple(
                TelegramButtonSpec(
                    text=button["text"],
                    callback_data=button["callback_data"],
                    url=button["url"],
                )
                for button in row
            )
            for row in state.get("response_button_rows", [])
        )
        response = TelegramResponse(
            response_text,
            edit_existing=state.get("edit_existing", False),
            button_rows=rows,
        )
        if state.get("clear_agent_thread", False):
            await self.delete_thread(thread_id)
        return response

    async def delete_thread(self, thread_id: str) -> None:
        await self.start()
        checkpointer = self._checkpointer
        if checkpointer is not None:
            await checkpointer.adelete_thread(thread_id)

    async def aclose(self) -> None:
        context = self._postgres_context
        self._postgres_context = None
        self._graph = None
        self._checkpointer = None
        if context is not None:
            await context.__aexit__(None, None, None)


def _last_ai_text(state: TelegramAgentState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, AIMessage) and isinstance(message.content, str):
            text = message.content.strip()
            if text:
                return text
    return "I could not process that request. Please try again."
