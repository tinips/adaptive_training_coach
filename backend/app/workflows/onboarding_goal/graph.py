"""Construction and invocation of the focused stateless goal graph."""

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
from app.integrations.llm.factory import create_goal_extraction_model
from app.integrations.llm.mock import FakeLLMScenario
from app.integrations.llm.models import GoalExtractionOutput, StructuredOnboardingModel
from app.observability.callbacks import build_langchain_run_config
from app.observability.noop import NoOpAIWorkflowObserver
from app.observability.protocol import (
    AIWorkflowObserver,
    AIWorkflowRunError,
    AIWorkflowRunMetadata,
    AIWorkflowRunResult,
)
from app.schemas.onboarding_goal import GoalExtractionWorkflowResult
from app.workflows.onboarding_goal.nodes import make_extract_goal_node
from app.workflows.onboarding_goal.state import GoalExtractionGraphState

CompiledGoalExtractionGraph = CompiledStateGraph[
    GoalExtractionGraphState,
    None,
    GoalExtractionGraphState,
    GoalExtractionGraphState,
]


def build_goal_extraction_graph(
    *,
    model: StructuredOnboardingModel,
) -> CompiledGoalExtractionGraph:
    """Compile one extraction node without a LangGraph checkpointer."""

    builder: StateGraph[
        GoalExtractionGraphState,
        None,
        GoalExtractionGraphState,
        GoalExtractionGraphState,
    ] = StateGraph(GoalExtractionGraphState)
    builder.add_node("extract_goal", make_extract_goal_node(model))
    builder.add_edge(START, "extract_goal")
    builder.add_edge("extract_goal", END)
    return builder.compile(name="onboarding_goal_extraction")


class LangGraphGoalExtractor:
    """Application adapter around the already-compiled goal graph."""

    def __init__(
        self,
        *,
        graph: CompiledGoalExtractionGraph,
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
    def graph(self) -> CompiledGoalExtractionGraph:
        return self._graph

    async def extract(
        self,
        *,
        user_id: UUID,
        user_text: str,
        existing_draft: GoalExtractionOutput | None,
    ) -> GoalExtractionWorkflowResult:
        started_at = datetime.now(UTC)
        started_clock = monotonic()
        metadata = AIWorkflowRunMetadata(
            workflow_name=self._workflow_name,
            run_id=uuid4(),
            onboarding_step=OnboardingStep.GOAL_INTAKE,
            provider_mode=self._model.provider_mode,
            model_name=self._model.model_name,
            started_at=started_at,
        )
        await self._observe_started(metadata)
        initial_state: GoalExtractionGraphState = {
            "user_id": user_id,
            "user_text": user_text,
            "existing_draft": existing_draft,
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
            state = cast(GoalExtractionGraphState, raw_state)
            result = GoalExtractionWorkflowResult(
                outcome=state.get("outcome", "fallback_required"),
                goal_draft=state.get("goal_draft"),
                error_code=state.get("error_code"),
                prompt_tokens=state.get("prompt_tokens"),
                completion_tokens=state.get("completion_tokens"),
            )
        except TimeoutError:
            result = GoalExtractionWorkflowResult(
                outcome="provider_error",
                error_code="workflow_timeout",
            )
        except Exception:
            result = GoalExtractionWorkflowResult(
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
                    outcome=(
                        "confirmation_required"
                        if result.outcome == "extracted"
                        else result.outcome
                    ),
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


def create_goal_extractor(
    settings: Settings,
    *,
    observer: AIWorkflowObserver | None = None,
    callbacks: Sequence[BaseCallbackHandler] = (),
    fake_scenario: FakeLLMScenario = FakeLLMScenario.AUTO,
) -> LangGraphGoalExtractor:
    """Create the existing model adapter and compile the focused graph once."""

    model = create_goal_extraction_model(
        settings,
        fake_scenario=fake_scenario,
    )
    return LangGraphGoalExtractor(
        graph=build_goal_extraction_graph(model=model),
        model=model,
        workflow_name=settings.ai_workflow_name,
        observer=observer,
        callbacks=callbacks,
    )
