"""Construction and observed invocation of the stateless LangGraph topology."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from time import monotonic
from typing import cast
from uuid import UUID, uuid4

from langchain_core.callbacks import BaseCallbackHandler
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.config import Settings
from app.domain.enums import OnboardingStep
from app.integrations.llm.factory import create_onboarding_text_model
from app.integrations.llm.mock import FakeLLMScenario
from app.integrations.llm.models import StructuredOnboardingModel
from app.observability.callbacks import build_langchain_run_config
from app.observability.noop import NoOpAIWorkflowObserver
from app.observability.protocol import (
    AIWorkflowObserver,
    AIWorkflowRunError,
    AIWorkflowRunMetadata,
    AIWorkflowRunResult,
)
from app.schemas.onboarding import (
    OnboardingTextWorkflowResult,
)
from app.workflows.onboarding_text.nodes import (
    clarification_required,
    confirmation_required,
    fallback_required,
    make_parse_with_model_node,
    make_route_result_node,
    provider_error,
    validate_structured_output,
)
from app.workflows.onboarding_text.routing import select_outcome
from app.workflows.onboarding_text.state import OnboardingTextGraphState

CompiledOnboardingTextGraph = CompiledStateGraph[
    OnboardingTextGraphState,
    None,
    OnboardingTextGraphState,
    OnboardingTextGraphState,
]


def build_onboarding_text_graph(
    *,
    model: StructuredOnboardingModel,
    min_confidence: float,
) -> CompiledOnboardingTextGraph:
    """Compile the one provider-independent topology without a checkpointer."""

    builder: StateGraph[
        OnboardingTextGraphState,
        None,
        OnboardingTextGraphState,
        OnboardingTextGraphState,
    ] = StateGraph(OnboardingTextGraphState)
    builder.add_node("parse_with_model", make_parse_with_model_node(model))
    builder.add_node("validate_structured_output", validate_structured_output)
    builder.add_node(
        "route_result",
        make_route_result_node(min_confidence=min_confidence),
    )
    builder.add_node("confirmation_required", confirmation_required)
    builder.add_node("clarification_required", clarification_required)
    builder.add_node("fallback_required", fallback_required)
    builder.add_node("provider_error", provider_error)

    builder.add_edge(START, "parse_with_model")
    builder.add_edge("parse_with_model", "validate_structured_output")
    builder.add_edge("validate_structured_output", "route_result")
    builder.add_conditional_edges(
        "route_result",
        select_outcome,
        {
            "confirmation_required": "confirmation_required",
            "clarification_required": "clarification_required",
            "fallback_required": "fallback_required",
            "provider_error": "provider_error",
        },
    )
    for terminal in (
        "confirmation_required",
        "clarification_required",
        "fallback_required",
        "provider_error",
    ):
        builder.add_edge(terminal, END)
    return builder.compile(name="onboarding_text")


class LangGraphOnboardingTextParser:
    """Application adapter around one already-compiled graph."""

    def __init__(
        self,
        *,
        graph: CompiledOnboardingTextGraph,
        model: StructuredOnboardingModel,
        workflow_name: str,
        observer: AIWorkflowObserver | None = None,
        callbacks: Sequence[BaseCallbackHandler] = (),
        timeout_seconds: float = 35.0,
    ) -> None:
        self._graph = graph
        self._model = model
        self._workflow_name = workflow_name
        self._observer = observer or NoOpAIWorkflowObserver()
        self._callbacks = tuple(callbacks)
        self._timeout_seconds = timeout_seconds

    @property
    def graph(self) -> CompiledOnboardingTextGraph:
        """Expose the compiled instance for dependency injection and tests."""

        return self._graph

    async def parse(
        self,
        *,
        user_id: UUID,
        step: OnboardingStep,
        user_text: str,
        confirmed_context: dict[str, object],
    ) -> OnboardingTextWorkflowResult:
        """Invoke one stateless graph run and emit only safe observations."""

        started_at = datetime.now(UTC)
        started_clock = monotonic()
        metadata = AIWorkflowRunMetadata(
            workflow_name=self._workflow_name,
            run_id=uuid4(),
            onboarding_step=step,
            provider_mode=self._model.provider_mode,
            model_name=self._model.model_name,
            started_at=started_at,
        )
        await self._observe_started(metadata)
        initial_state: OnboardingTextGraphState = {
            "user_id": user_id,
            "onboarding_step": step,
            "user_text": user_text,
            "confirmed_context": dict(confirmed_context),
        }
        try:
            raw_state = await asyncio.wait_for(
                self._graph.ainvoke(
                    initial_state,
                    config=build_langchain_run_config(
                        metadata,
                        callbacks=self._callbacks,
                    ),
                ),
                timeout=self._timeout_seconds,
            )
            state = cast(OnboardingTextGraphState, raw_state)
            result = _workflow_result_from_state(state)
        except TimeoutError:
            result = OnboardingTextWorkflowResult(
                outcome="provider_error",
                error_code="workflow_timeout",
            )
        except Exception:
            # The graph normally maps provider errors itself. This final guard
            # protects callers from orchestration/runtime failures without
            # exposing exception details.
            result = OnboardingTextWorkflowResult(
                outcome="provider_error",
                error_code="workflow_failure",
            )

        completed_at = datetime.now(UTC)
        latency_ms = max(0, round((monotonic() - started_clock) * 1000))
        if result.outcome == "provider_error":
            await self._observe_failed(
                AIWorkflowRunError(
                    metadata=metadata,
                    failed_at=completed_at,
                    latency_ms=latency_ms,
                    error_code=result.error_code or "provider_failure",
                )
            )
        else:
            await self._observe_completed(
                AIWorkflowRunResult(
                    metadata=metadata,
                    outcome=result.outcome,
                    completed_at=completed_at,
                    latency_ms=latency_ms,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    error_code=result.error_code,
                )
            )
        return result

    async def _observe_started(self, metadata: AIWorkflowRunMetadata) -> None:
        try:
            await self._observer.on_run_started(metadata)
        except Exception:
            return

    async def _observe_completed(self, result: AIWorkflowRunResult) -> None:
        try:
            await self._observer.on_run_completed(result)
        except Exception:
            return

    async def _observe_failed(self, error: AIWorkflowRunError) -> None:
        try:
            await self._observer.on_run_failed(error)
        except Exception:
            return


def create_onboarding_text_parser(
    settings: Settings,
    *,
    observer: AIWorkflowObserver | None = None,
    callbacks: Sequence[BaseCallbackHandler] = (),
    fake_scenario: FakeLLMScenario = FakeLLMScenario.AUTO,
) -> LangGraphOnboardingTextParser:
    """Construct the model once and compile its stateless graph once."""

    model = create_onboarding_text_model(
        settings,
        fake_scenario=fake_scenario,
    )
    graph = build_onboarding_text_graph(
        model=model,
        min_confidence=settings.llm_min_confidence,
    )
    return LangGraphOnboardingTextParser(
        graph=graph,
        model=model,
        workflow_name=settings.ai_workflow_name,
        observer=observer,
        callbacks=callbacks,
    )


def _workflow_result_from_state(
    state: OnboardingTextGraphState,
) -> OnboardingTextWorkflowResult:
    return OnboardingTextWorkflowResult(
        outcome=state.get("outcome", "fallback_required"),
        parse_result=state.get("parse_result"),
        error_code=state.get("error_code"),
        prompt_tokens=state.get("prompt_tokens"),
        completion_tokens=state.get("completion_tokens"),
    )
