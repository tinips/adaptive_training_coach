"""Compiled stateless workflow for literal onboarding context intake."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
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
    FreeTextValidationWorkflowResult,
)
from app.workflows.onboarding_context.nodes import make_free_text_validation_node
from app.workflows.onboarding_context.state import FreeTextValidationGraphState

CompiledFreeTextValidationGraph = CompiledStateGraph[
    FreeTextValidationGraphState,
    None,
    FreeTextValidationGraphState,
    FreeTextValidationGraphState,
]


def build_free_text_validation_graph(
    *, model: StructuredOnboardingModel
) -> CompiledFreeTextValidationGraph:
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


class LangGraphContextOnboardingWorkflow(ContextOnboardingWorkflow):
    def __init__(
        self,
        *,
        free_text_validation_graph: CompiledFreeTextValidationGraph,
        model: StructuredOnboardingModel,
        workflow_name: str,
        observer: AIWorkflowObserver | None = None,
        callbacks: Sequence[BaseCallbackHandler] = (),
        timeout_seconds: float = 35.0,
    ) -> None:
        self._free_text_validation_graph = free_text_validation_graph
        self._model = model
        self._workflow_name = workflow_name
        self._observer = observer or NoOpAIWorkflowObserver()
        self._callbacks = tuple(callbacks)
        self._timeout_seconds = timeout_seconds

    @property
    def free_text_validation_graph(self) -> CompiledFreeTextValidationGraph:
        return self._free_text_validation_graph

    async def validate_free_text(
        self, *, step: OnboardingStep, user_text: str
    ) -> FreeTextValidationWorkflowResult:
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
        try:
            await self._observer.on_run_started(metadata)
        except Exception:
            pass
        try:
            raw = await asyncio.wait_for(
                self._free_text_validation_graph.ainvoke(
                    {"step": step, "user_text": user_text},
                    config=build_langchain_run_config(
                        metadata, callbacks=self._callbacks
                    ),
                ),
                timeout=self._timeout_seconds,
            )
            result = _result_from_state(cast(FreeTextValidationGraphState, raw))
        except TimeoutError:
            result = FreeTextValidationWorkflowResult(
                outcome="provider_error", error_code="workflow_timeout"
            )
        except Exception:
            result = FreeTextValidationWorkflowResult(
                outcome="provider_error", error_code="workflow_failure"
            )
        completed_at = datetime.now(UTC)
        latency_ms = max(0, round((monotonic() - started_clock) * 1000))
        try:
            if result.outcome == "provider_error":
                await self._observer.on_run_failed(
                    AIWorkflowRunError(
                        metadata=metadata,
                        failed_at=completed_at,
                        latency_ms=latency_ms,
                        error_code=result.error_code or "provider_failure",
                    )
                )
            else:
                await self._observer.on_run_completed(
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
        except Exception:
            pass
        return result

    async def validate_text(
        self, *, step: OnboardingStep, user_text: str
    ) -> FreeTextValidationWorkflowResult:
        return await self.validate_free_text(step=step, user_text=user_text)


def _result_from_state(
    state: FreeTextValidationGraphState,
) -> FreeTextValidationWorkflowResult:
    outcome = state.get("outcome")
    if outcome not in {"accepted", "retry_required", "provider_error"}:
        return FreeTextValidationWorkflowResult(
            outcome="provider_error", error_code="invalid_workflow_result"
        )
    return FreeTextValidationWorkflowResult(
        outcome=outcome,
        error_code=state.get("error_code"),
        prompt_tokens=state.get("prompt_tokens"),
        completion_tokens=state.get("completion_tokens"),
    )


def create_context_onboarding_workflow(
    settings: Settings,
    *,
    observer: AIWorkflowObserver | None = None,
    callbacks: Sequence[BaseCallbackHandler] = (),
    fake_scenario: FakeLLMScenario = FakeLLMScenario.AUTO,
) -> LangGraphContextOnboardingWorkflow:
    model = create_goal_extraction_model(settings, fake_scenario=fake_scenario)
    return LangGraphContextOnboardingWorkflow(
        free_text_validation_graph=build_free_text_validation_graph(model=model),
        model=model,
        workflow_name=f"{settings.ai_workflow_name}_context"[:100],
        observer=observer,
        callbacks=callbacks,
    )
