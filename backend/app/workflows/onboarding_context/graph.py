"""Compiled, stateless LangGraph workflow for onboarding context intake."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, date, datetime
from time import monotonic
from typing import cast
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.config import Settings
from app.domain.enums import OnboardingStep
from app.integrations.llm.factory import create_goal_extraction_model
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
from app.schemas.onboarding_context import (
    ContextOnboardingWorkflow,
    EquipmentRecommendationGoalContext,
    EquipmentRecommendationWorkflowResult,
    FreeTextValidationWorkflowResult,
)
from app.workflows.onboarding_context.nodes import (
    make_equipment_recommendation_node,
    make_free_text_validation_node,
)
from app.workflows.onboarding_context.state import (
    EquipmentRecommendationGraphState,
    FreeTextValidationGraphState,
)

CompiledFreeTextValidationGraph = CompiledStateGraph[
    FreeTextValidationGraphState,
    None,
    FreeTextValidationGraphState,
    FreeTextValidationGraphState,
]
CompiledEquipmentRecommendationGraph = CompiledStateGraph[
    EquipmentRecommendationGraphState,
    None,
    EquipmentRecommendationGraphState,
    EquipmentRecommendationGraphState,
]


def build_free_text_validation_graph(
    *,
    model: StructuredOnboardingModel,
) -> CompiledFreeTextValidationGraph:
    """Compile one stateless raw-text validation graph without checkpoints."""

    builder: StateGraph[
        FreeTextValidationGraphState,
        None,
        FreeTextValidationGraphState,
        FreeTextValidationGraphState,
    ] = StateGraph(FreeTextValidationGraphState)
    builder.add_node("validate_text", make_free_text_validation_node(model))
    builder.add_edge(START, "validate_text")
    builder.add_edge("validate_text", END)
    return builder.compile(name="onboarding_context_validation")


def build_equipment_recommendation_graph(
    *,
    model: StructuredOnboardingModel,
) -> CompiledEquipmentRecommendationGraph:
    """Compile one stateless goal-to-equipment graph without checkpoints."""

    builder: StateGraph[
        EquipmentRecommendationGraphState,
        None,
        EquipmentRecommendationGraphState,
        EquipmentRecommendationGraphState,
    ] = StateGraph(EquipmentRecommendationGraphState)
    builder.add_node("recommend_equipment", make_equipment_recommendation_node(model))
    builder.add_edge(START, "recommend_equipment")
    builder.add_edge("recommend_equipment", END)
    return builder.compile(name="onboarding_equipment_recommendation")


class LangGraphContextOnboardingWorkflow(ContextOnboardingWorkflow):
    """Application adapter around compiled non-persistent context graphs."""

    def __init__(
        self,
        *,
        free_text_validation_graph: CompiledFreeTextValidationGraph,
        equipment_recommendation_graph: CompiledEquipmentRecommendationGraph,
        model: StructuredOnboardingModel,
        workflow_name: str,
        observer: AIWorkflowObserver | None = None,
        callbacks: Sequence[BaseCallbackHandler] = (),
        timeout_seconds: float = 35.0,
    ) -> None:
        self._free_text_validation_graph = free_text_validation_graph
        self._equipment_recommendation_graph = equipment_recommendation_graph
        self._model = model
        self._workflow_name = workflow_name
        self._observer = observer or NoOpAIWorkflowObserver()
        self._callbacks = tuple(callbacks)
        self._timeout_seconds = timeout_seconds

    @property
    def free_text_validation_graph(self) -> CompiledFreeTextValidationGraph:
        """Expose the compiled validation graph for focused wiring tests."""

        return self._free_text_validation_graph

    @property
    def equipment_recommendation_graph(self) -> CompiledEquipmentRecommendationGraph:
        """Expose the compiled recommendation graph for focused wiring tests."""

        return self._equipment_recommendation_graph

    async def validate_free_text(
        self,
        *,
        step: OnboardingStep,
        user_text: str,
        goal_context: EquipmentRecommendationGoalContext | None = None,
    ) -> FreeTextValidationWorkflowResult:
        """Accept any non-empty raw answer for the active intake step."""

        started_at = datetime.now(UTC)
        started_clock = monotonic()
        metadata = self._build_metadata(step=step, started_at=started_at)
        await self._observe_started(metadata)
        try:
            raw_state = await asyncio.wait_for(
                self._free_text_validation_graph.ainvoke(
                    {
                        "step": step,
                        "user_text": user_text,
                        "goal_context": goal_context,
                    },
                    config=build_langchain_run_config(
                        metadata,
                        callbacks=self._callbacks,
                    ),
                ),
                timeout=self._timeout_seconds,
            )
            state = cast(FreeTextValidationGraphState, raw_state)
            result = _free_text_result_from_state(state)
        except TimeoutError:
            result = FreeTextValidationWorkflowResult(
                outcome="provider_error",
                error_code="workflow_timeout",
            )
        except Exception:
            result = FreeTextValidationWorkflowResult(
                outcome="provider_error",
                error_code="workflow_failure",
            )
        await self._observe_free_text_result(
            metadata=metadata,
            result=result,
            started_clock=started_clock,
        )
        return result

    async def validate_text(
        self,
        *,
        step: OnboardingStep,
        user_text: str,
        goal_context: EquipmentRecommendationGoalContext | None = None,
    ) -> FreeTextValidationWorkflowResult:
        """Alias retained for concise application-service call sites."""

        return await self.validate_free_text(
            step=step,
            user_text=user_text,
            goal_context=goal_context,
        )

    async def recommend_equipment(
        self,
        *,
        main_goal: str | None,
        target_outcome: str | None,
        event_date: date | None,
        secondary_priority: str | None,
    ) -> EquipmentRecommendationWorkflowResult:
        """Generate a short equipment recommendation from canonical goal fields."""

        started_at = datetime.now(UTC)
        started_clock = monotonic()
        metadata = self._build_metadata(
            step=_equipment_recommendation_step(),
            started_at=started_at,
        )
        await self._observe_started(metadata)
        goal_context = EquipmentRecommendationGoalContext(
            main_goal=main_goal,
            target_outcome=target_outcome,
            event_date=event_date,
            secondary_priority=secondary_priority,
        )
        try:
            raw_state = await asyncio.wait_for(
                self._equipment_recommendation_graph.ainvoke(
                    {"goal_context": goal_context},
                    config=build_langchain_run_config(
                        metadata,
                        callbacks=self._callbacks,
                    ),
                ),
                timeout=self._timeout_seconds,
            )
            state = cast(EquipmentRecommendationGraphState, raw_state)
            result = _equipment_recommendation_result_from_state(state)
        except TimeoutError:
            result = EquipmentRecommendationWorkflowResult(
                outcome="provider_error",
                error_code="workflow_timeout",
            )
        except Exception:
            result = EquipmentRecommendationWorkflowResult(
                outcome="provider_error",
                error_code="workflow_failure",
            )
        await self._observe_equipment_result(
            metadata=metadata,
            result=result,
            started_clock=started_clock,
        )
        return result

    def _build_metadata(
        self,
        *,
        step: OnboardingStep,
        started_at: datetime,
    ) -> AIWorkflowRunMetadata:
        return AIWorkflowRunMetadata(
            workflow_name=self._workflow_name,
            run_id=uuid4(),
            onboarding_step=step,
            provider_mode=self._model.provider_mode,
            model_name=self._model.model_name,
            started_at=started_at,
        )

    async def _observe_started(self, metadata: AIWorkflowRunMetadata) -> None:
        try:
            await self._observer.on_run_started(metadata)
        except Exception:
            return

    async def _observe_free_text_result(
        self,
        *,
        metadata: AIWorkflowRunMetadata,
        result: FreeTextValidationWorkflowResult,
        started_clock: float,
    ) -> None:
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
            return
        await self._observe_completed(
            AIWorkflowRunResult(
                metadata=metadata,
                outcome=(
                    "confirmation_required"
                    if result.outcome == "accepted"
                    else "fallback_required"
                ),
                completed_at=completed_at,
                latency_ms=latency_ms,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                error_code=result.error_code,
            )
        )

    async def _observe_equipment_result(
        self,
        *,
        metadata: AIWorkflowRunMetadata,
        result: EquipmentRecommendationWorkflowResult,
        started_clock: float,
    ) -> None:
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
            return
        await self._observe_completed(
            AIWorkflowRunResult(
                metadata=metadata,
                outcome=(
                    "confirmation_required"
                    if result.outcome == "recommended"
                    else "fallback_required"
                ),
                completed_at=completed_at,
                latency_ms=latency_ms,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                error_code=result.error_code,
            )
        )

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


def _free_text_result_from_state(
    state: FreeTextValidationGraphState,
) -> FreeTextValidationWorkflowResult:
    outcome = state.get("outcome")
    if outcome not in {"accepted", "retry_required", "provider_error"}:
        return FreeTextValidationWorkflowResult(
            outcome="provider_error",
            error_code="invalid_workflow_result",
        )
    return FreeTextValidationWorkflowResult(
        outcome=outcome,
        error_code=state.get("error_code"),
        prompt_tokens=state.get("prompt_tokens"),
        completion_tokens=state.get("completion_tokens"),
    )


def _equipment_recommendation_result_from_state(
    state: EquipmentRecommendationGraphState,
) -> EquipmentRecommendationWorkflowResult:
    outcome = state.get("outcome")
    if outcome not in {"recommended", "retry_required", "provider_error"}:
        return EquipmentRecommendationWorkflowResult(
            outcome="provider_error",
            error_code="invalid_workflow_result",
        )
    recommendation = state.get("recommendation")
    if outcome == "recommended" and not recommendation:
        return EquipmentRecommendationWorkflowResult(
            outcome="retry_required",
            error_code="malformed_structured_output",
            prompt_tokens=state.get("prompt_tokens"),
            completion_tokens=state.get("completion_tokens"),
        )
    return EquipmentRecommendationWorkflowResult(
        outcome=outcome,
        recommendation=recommendation,
        error_code=state.get("error_code"),
        prompt_tokens=state.get("prompt_tokens"),
        completion_tokens=state.get("completion_tokens"),
    )


def _equipment_recommendation_step() -> OnboardingStep:
    """Return the explicit persisted recommendation checkpoint."""

    return OnboardingStep.EQUIPMENT_RECOMMENDATION


def create_context_onboarding_workflow(
    settings: Settings,
    *,
    observer: AIWorkflowObserver | None = None,
    callbacks: Sequence[BaseCallbackHandler] = (),
    fake_scenario: FakeLLMScenario = FakeLLMScenario.AUTO,
) -> LangGraphContextOnboardingWorkflow:
    """Create the shared model adapter and compile both context graphs once."""

    model = create_goal_extraction_model(settings, fake_scenario=fake_scenario)
    workflow_name = f"{settings.ai_workflow_name}_context"[:100]
    return LangGraphContextOnboardingWorkflow(
        free_text_validation_graph=build_free_text_validation_graph(model=model),
        equipment_recommendation_graph=build_equipment_recommendation_graph(
            model=model,
        ),
        model=model,
        workflow_name=workflow_name,
        observer=observer,
        callbacks=callbacks,
    )
