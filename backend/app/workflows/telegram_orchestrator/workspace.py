"""One persistent LangGraph workspace for all Telegram text and callbacks."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict, cast
from uuid import UUID

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
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
from langgraph.runtime import Runtime
from langgraph.types import Command
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.bot import messages as bot_messages
from app.bot.rendering import TelegramButtonSpec, TelegramResponse
from app.integrations.llm.models import StructuredOnboardingModel
from app.schemas.onboarding_goal import OnboardingUpdateHandler
from app.workflows.prompts.onboarding import explicit_onboarding_change_tool_policy

TelegramEventType = Literal["text", "callback"]
TelegramInputDispatcher = Callable[
    [TelegramEventType, str], Awaitable[TelegramResponse]
]
TelegramPresentationLoader = Callable[[], Awaitable[TelegramResponse]]

_CHECKPOINT_POOL_MIN_SIZE = 1
_CHECKPOINT_POOL_MAX_SIZE = 10
_LLM_CONTEXT_MESSAGE_LIMIT = 3
_STRICT_CALLBACK_PATTERN = re.compile(r"[A-Za-z0-9:_-]{1,128}")
_STRICT_NUMBER_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_FAST_COMMANDS = frozenset(
    {
        "/start",
        "/help",
        "/profile",
        "/add_workout",
        "/cancel",
        "/delete_me",
    }
)
_GENDER_CHOICES = frozenset({"MALE", "FEMALE"})
_NUMERIC_PROMPT_HINTS = (
    "what year were you born",
    "birth year",
    "how old are you",
    "your age",
    "current weight",
    "weight in kilograms",
    "new weight",
    "height in centimeters",
    "your height",
    "new height",
)
_GENDER_PROMPT_HINTS = ("which is your sex",)
_MANDATORY_NUMERIC_PROMPTS = frozenset(
    {
        bot_messages.PROFILE_BIRTH_YEAR_INTAKE.casefold(),
        bot_messages.PROFILE_WEIGHT_INTAKE.casefold(),
        bot_messages.PROFILE_HEIGHT_INTAKE.casefold(),
    }
)


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
    fast_track: bool


@dataclass(frozen=True, slots=True)
class TelegramAgentContext:
    """Per-invocation dependencies; LangGraph never checkpoints these values."""

    user_id: UUID | None
    dispatcher: TelegramInputDispatcher
    onboarding_updater: OnboardingUpdateHandler | None
    presentation_loader: TelegramPresentationLoader | None = None
    onboarding_active: bool = False


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
    gender: Literal["MALE", "FEMALE"] | None = None
    weight_kg: float | None = Field(default=None, ge=40, le=200)
    height_cm: int | None = Field(default=None, ge=120, le=230)
    availability_text: str | None = Field(
        default=None,
        min_length=1,
        max_length=4096,
        description=(
            "The athlete's training availability exactly as they stated it, "
            "including days and available time. Preserve the supplied wording "
            "without summarising, translating, or normalising it."
        ),
    )
    health_limitations_text: str | None = Field(
        default=None,
        min_length=1,
        max_length=4096,
        description=(
            "The athlete's injuries or training limitations exactly as stated. "
            "Use NONE_REPORTED only when the athlete explicitly states that "
            "there are none; otherwise preserve their supplied wording without "
            "rewriting it. This is sensitive text and must never be repeated in "
            "the assistant response."
        ),
    )
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
    confirmation = bot_messages.onboarding_fields_updated(
        tuple(updated.updated_fields),
    )
    # The presentation is authoritative after a mutation.  In particular, a
    # changed goal immediately reopens equipment review; do not depend on a
    # subsequent model turn to repeat that prompt or preserve health privacy.
    response_text = (
        f"{confirmation}\n\n{presentation.text}"
        if presentation is not None
        else confirmation
    )
    content = json.dumps(
        {
            # Values are intentionally omitted from graph state and the next
            # provider turn.  They can contain raw health or availability text;
            # only the persisted service owns those values.
            "updated_fields": list(updated.updated_fields),
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
            "response_text": response_text,
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
    data_corrections_policy = explicit_onboarding_change_tool_policy(
        "telegram_orchestrator",
        tool_name="update_onboarding_data",
        supported_fields=(
            "goal, target outcome, event date, age, birth year, category, "
            "weight, height, availability, and training limitations"
        ),
    )
    return SystemMessage(
        content=(
            "You are the Adaptive Endurance Coach orchestrator. Your sole job is "
            "to route the latest Telegram event into EXACTLY ONE tool call based "
            "on user intent.\n\n"
            "CRITICAL RULES:\n"
            f"{data_corrections_policy}"
            "2. RAW CONTEXT UPDATES: Availability and training limitations can "
            "each be updated independently. For one of those "
            "fields, send only the relevant value from the latest user message "
            "to 'update_onboarding_data' byte-for-byte: never summarise, "
            "translate, infer, or alter it. Use "
            "health_limitations_text='NONE_REPORTED' only when "
            "the athlete explicitly says they have no injuries or training "
            "limitations. Do not update any other field unless the athlete "
            "explicitly requested it.\n"
            "3. SENSITIVE HEALTH CONTENT: Never quote, restate, summarise, or "
            "otherwise expose health_limitations_text in a response. After it is "
            "saved, refer only to 'training limitations'.\n"
            "4. ORDINARY INPUTS: For normal answers, buttons clicks, or commands, "
            "call 'dispatch_telegram_input' preserving the raw content "
            "byte-for-byte.\n"
            "5. NO DUPLICATES: Never call both tools for a single message.\n\n"
            "After a tool executes, confirm the changes briefly and naturally, "
            "without exposing health text, then prompt the user using the "
            "'current_prompt' provided in the tool's result."
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


def _provider_message_context(
    messages: list[BaseMessage],
    *,
    limit: int = _LLM_CONTEXT_MESSAGE_LIMIT,
) -> list[BaseMessage]:
    """Return a bounded provider window without orphaning active tool results."""

    if limit < 1:
        return []
    if messages and isinstance(messages[-1], ToolMessage):
        tool_request_index: int | None = None
        for index in range(len(messages) - 2, -1, -1):
            candidate = messages[index]
            if isinstance(candidate, AIMessage) and candidate.tool_calls:
                tool_request_index = index
                break
        if tool_request_index is not None:
            active_exchange = messages[tool_request_index:]
            remaining = max(0, limit - len(active_exchange))
            preceding = [
                message
                for message in messages[:tool_request_index]
                if not isinstance(message, ToolMessage)
                and not (isinstance(message, AIMessage) and bool(message.tool_calls))
            ]
            prior_context = preceding[-remaining:] if remaining else []
            return [*prior_context, *active_exchange]
    plain_messages = [
        message
        for message in messages
        if not isinstance(message, ToolMessage)
        and not (isinstance(message, AIMessage) and bool(message.tool_calls))
    ]
    return plain_messages[-limit:]


def _remove_persisted_update_exchange(
    state: TelegramAgentState,
) -> TelegramAgentState:
    """Remove raw update input and arguments before the durable checkpoint exit.

    The global workspace needs raw text for its first routing turn, but a
    successful ownership-scoped update has no need to retain that message or its
    AI tool-call arguments in chat history. This matters especially for health
    limitations, whose literal value belongs only in the profile field.
    """

    messages = state.get("messages", [])
    latest_tool_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if isinstance(messages[index], ToolMessage)
            and messages[index].name == "update_onboarding_data"
        ),
        None,
    )
    if latest_tool_index is None:
        return {}

    latest_tool = messages[latest_tool_index]
    if not isinstance(latest_tool, ToolMessage):
        return {}
    remove_ids = [latest_tool.id]
    tool_call_id = latest_tool.tool_call_id
    tool_call_index: int | None = None
    for index in range(latest_tool_index - 1, -1, -1):
        candidate = messages[index]
        if not isinstance(candidate, AIMessage):
            continue
        if any(
            call.get("id") == tool_call_id
            and call.get("name") == "update_onboarding_data"
            for call in candidate.tool_calls
        ):
            remove_ids.append(candidate.id)
            tool_call_index = index
            break
    if tool_call_index is not None:
        for index in range(tool_call_index - 1, -1, -1):
            candidate = messages[index]
            if isinstance(candidate, HumanMessage):
                remove_ids.append(candidate.id)
                break
    return {
        "messages": [
            RemoveMessage(id=message_id)
            for message_id in remove_ids
            if isinstance(message_id, str) and message_id
        ]
    }


def _dispatch_tool_call(
    *,
    event_type: TelegramEventType,
    content: str,
    call_id: str,
) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "dispatch_telegram_input",
                "args": {"event_type": event_type, "content": content},
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def _update_tool_call(
    *,
    payload: dict[str, JsonValue],
    call_id: str,
) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "update_onboarding_data",
                "args": payload,
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def _previous_presentation(messages: list[BaseMessage]) -> str:
    """Return the last bot presentation without exposing it outside the graph."""

    for message in reversed(messages[:-1]):
        if isinstance(message, ToolMessage) and isinstance(message.content, str):
            try:
                payload = json.loads(message.content)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                response_text = payload.get("response_text")
                if isinstance(response_text, str) and response_text.strip():
                    return response_text
        if isinstance(message, AIMessage) and isinstance(message.content, str):
            content = message.content.strip()
            if content:
                return content
    return ""


def _numeric_update_payload(
    *,
    previous: str,
    content: str,
) -> dict[str, JsonValue] | None:
    """Extract one validated field from a deterministic clarification answer."""

    prompt = previous.casefold()
    value = float(content)
    if "weight" in prompt and 40 <= value <= 200:
        return {"weight_kg": value}
    if "height" in prompt and value.is_integer() and 120 <= value <= 230:
        return {"height_cm": int(value)}
    if (
        ("birth year" in prompt or "year were you born" in prompt)
        and value.is_integer()
        and 1940 <= value <= 2008
    ):
        return {"birth_year": int(value)}
    if (
        ("your age" in prompt or "how old are you" in prompt)
        and value.is_integer()
        and 16 <= value <= 100
    ):
        return {"age": int(value)}
    return None


def _fast_track_call(
    state: TelegramAgentState,
    *,
    onboarding_active: bool,
) -> AIMessage | None:
    """Build a deterministic dispatch call for strict, context-safe inputs."""

    messages = state.get("messages", [])
    if not messages or not isinstance(messages[-1], HumanMessage):
        return None
    current = messages[-1]
    if not isinstance(current.content, str):
        return None
    raw_content = current.content
    content = raw_content.strip()
    raw_event_type = current.additional_kwargs.get("telegram_event_type", "text")
    event_type: TelegramEventType = (
        "callback" if raw_event_type == "callback" else "text"
    )
    call_id = f"fast-dispatch-{len(messages)}"
    normalized = content.upper().replace(" ", "_")

    if onboarding_active:
        return _dispatch_tool_call(
            event_type=event_type,
            content=raw_content,
            call_id=call_id,
        )

    if event_type == "callback" and normalized in _GENDER_CHOICES:
        return _dispatch_tool_call(
            event_type="callback",
            content=f"ob:v1:profile:gender:{normalized}",
            call_id=call_id,
        )
    if (
        event_type == "callback"
        and len(content.encode("utf-8")) <= 64
        and _STRICT_CALLBACK_PATTERN.fullmatch(content) is not None
    ):
        return _dispatch_tool_call(
            event_type="callback",
            content=content,
            call_id=call_id,
        )
    if event_type == "text" and content.casefold() in _FAST_COMMANDS:
        return _dispatch_tool_call(
            event_type="text",
            content=content,
            call_id=call_id,
        )

    previous_presentation = _previous_presentation(messages)
    previous = previous_presentation.casefold()
    if normalized in _GENDER_CHOICES and any(
        hint in previous for hint in _GENDER_PROMPT_HINTS
    ):
        return _dispatch_tool_call(
            event_type="callback",
            content=f"ob:v1:profile:gender:{normalized}",
            call_id=call_id,
        )
    if _STRICT_NUMBER_PATTERN.fullmatch(content) is not None and any(
        hint in previous for hint in _NUMERIC_PROMPT_HINTS
    ):
        if previous.strip() not in _MANDATORY_NUMERIC_PROMPTS:
            payload = _numeric_update_payload(
                previous=previous_presentation,
                content=content,
            )
            if payload is not None:
                return _update_tool_call(
                    payload=payload,
                    call_id=f"fast-update-{len(messages)}",
                )
        return _dispatch_tool_call(
            event_type="text",
            content=content,
            call_id=call_id,
        )
    return None


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
            "fast_track": False,
        }

    async def fast_router(
        state: TelegramAgentState,
        runtime: Runtime[TelegramAgentContext],
    ) -> TelegramAgentState:
        call = _fast_track_call(
            state,
            onboarding_active=runtime.context.onboarding_active,
        )
        if call is None:
            return {"fast_track": False}
        return {"messages": [call], "fast_track": True}

    def route_after_fast_router(
        state: TelegramAgentState,
    ) -> Literal["tools", "agent"]:
        return "tools" if state.get("fast_track", False) else "agent"

    async def agent(
        state: TelegramAgentState,
        config: RunnableConfig,
    ) -> TelegramAgentState:
        response = await tool_model.ainvoke(
            [_system_prompt(), *_provider_message_context(state["messages"])],
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
    builder.add_node("fast_router", fast_router)
    builder.add_node("agent", RunnableLambda(agent))
    builder.add_node("tools", ToolNode(tools, handle_tool_errors=False))
    builder.add_node(
        "remove_persisted_update_exchange",
        RunnableLambda(_remove_persisted_update_exchange),
    )
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "fast_router")
    builder.add_conditional_edges(
        "fast_router",
        route_after_fast_router,
        {"tools": "tools", "agent": "agent"},
    )
    builder.add_conditional_edges(
        "agent",
        tools_condition,
        {"tools": "tools", "__end__": END},
    )
    # Both tools construct the complete deterministic Telegram response.  Do
    # not make a second provider call after a write: the prior tool-call message
    # may contain raw context values, including sensitive limitations.
    builder.add_edge("tools", "remove_persisted_update_exchange")
    builder.add_edge("remove_persisted_update_exchange", END)
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
        self._postgres_pool: (
            AsyncConnectionPool[AsyncConnection[dict[str, Any]]] | None
        ) = None
        self._checkpointer: BaseCheckpointSaver[str] | None = None
        self._start_lock = asyncio.Lock()

    async def start(self) -> None:
        """Open and migrate the native checkpointer once per bot process."""

        if self._graph is not None:
            return
        await self._start_once()

    async def _start_once(self) -> None:
        """Serialize the sole initialization without taxing steady-state calls."""

        async with self._start_lock:
            if self._graph is not None:
                return
            if self._postgres_dsn is None:
                checkpointer: BaseCheckpointSaver[str] = InMemorySaver()
            else:
                postgres_pool: AsyncConnectionPool[AsyncConnection[dict[str, Any]]] = (
                    AsyncConnectionPool(
                        conninfo=self._postgres_dsn,
                        kwargs={
                            "autocommit": True,
                            "prepare_threshold": 0,
                            "row_factory": dict_row,
                        },
                        min_size=_CHECKPOINT_POOL_MIN_SIZE,
                        max_size=_CHECKPOINT_POOL_MAX_SIZE,
                        open=False,
                        name="telegram-agent-checkpoints",
                    )
                )
                try:
                    await postgres_pool.open(wait=True)
                    postgres = AsyncPostgresSaver(postgres_pool)
                    await postgres.setup()
                except Exception:
                    await postgres_pool.close()
                    raise
                self._postgres_pool = postgres_pool
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
            durability="exit",
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
        postgres_pool = self._postgres_pool
        self._postgres_pool = None
        self._graph = None
        self._checkpointer = None
        if postgres_pool is not None:
            await postgres_pool.close()


def _last_ai_text(state: TelegramAgentState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, AIMessage) and isinstance(message.content, str):
            text = message.content.strip()
            if text:
                return text
    return "I could not process that request. Please try again."
