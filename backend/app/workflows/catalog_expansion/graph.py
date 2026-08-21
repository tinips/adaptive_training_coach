"""Compiled, checkpoint-free dynamic catalog expansion workflow."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from time import monotonic
from typing import Literal, cast
from uuid import UUID, uuid4

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.config import Settings
from app.domain.enums import OnboardingStep
from app.integrations.llm.factory import create_goal_extraction_model
from app.integrations.llm.models import GoalTemplateSummary, StructuredOnboardingModel
from app.observability.callbacks import build_langchain_run_config
from app.observability.noop import NoOpAIWorkflowObserver
from app.observability.protocol import (
    AIWorkflowObserver,
    AIWorkflowRunError,
    AIWorkflowRunMetadata,
    AIWorkflowRunResult,
)
from app.schemas.catalog_expansion import (
    CapabilitySummary,
    CatalogExpansionWorkflowResult,
    ExecutionOptionSummary,
    GoalContextProposal,
    GoalTemplateDraft,
    TrainingContextSummary,
)
from app.workflows.catalog_expansion.nodes import make_expand_catalog_node
from app.workflows.catalog_expansion.state import CatalogExpansionState

CompiledCatalogExpansionGraph = CompiledStateGraph[
    CatalogExpansionState,
    None,
    CatalogExpansionState,
    CatalogExpansionState,
]


def build_catalog_expansion_graph(
    *, model: StructuredOnboardingModel
) -> CompiledCatalogExpansionGraph:
    builder: StateGraph[
        CatalogExpansionState,
        None,
        CatalogExpansionState,
        CatalogExpansionState,
    ] = StateGraph(CatalogExpansionState)
    builder.add_node(
        "expand_catalog",
        RunnableLambda(make_expand_catalog_node(model), name="expand_catalog"),
    )
    builder.add_edge(START, "expand_catalog")
    builder.add_edge("expand_catalog", END)
    return builder.compile(name="dynamic_training_catalog_expansion")


class LangGraphCatalogExpansionWorkflow:
    def __init__(
        self,
        *,
        graph: CompiledCatalogExpansionGraph,
        model: StructuredOnboardingModel,
        observer: AIWorkflowObserver | None = None,
        callbacks: Sequence[BaseCallbackHandler] = (),
        timeout_seconds: float = 35.0,
    ) -> None:
        self._graph = graph
        self._model = model
        self._observer = observer or NoOpAIWorkflowObserver()
        self._callbacks = tuple(callbacks)
        self._timeout_seconds = timeout_seconds

    async def map_goal_contexts(
        self,
        *,
        user_id: UUID,
        templates: tuple[GoalTemplateDraft, ...],
        active_goals: tuple[GoalTemplateSummary, ...],
        active_contexts: tuple[TrainingContextSummary, ...],
    ) -> CatalogExpansionWorkflowResult:
        # Existing goal templates do not affect context selection. Keep the
        # protocol argument for compatibility with callers, but avoid sending
        # that redundant catalog snapshot to the model.
        del active_goals
        request = {
            "new_templates": [item.model_dump(mode="json") for item in templates],
            "active_training_contexts": [
                item.model_dump(mode="json") for item in active_contexts
            ],
        }
        return await self._invoke(
            user_id=user_id,
            action="MAP_CONTEXTS",
            request=request,
        )

    async def define_context_capabilities(
        self,
        *,
        user_id: UUID,
        goals: tuple[GoalTemplateDraft, ...],
        new_contexts: tuple[GoalContextProposal, ...],
        active_contexts: tuple[TrainingContextSummary, ...],
        active_capabilities: tuple[CapabilitySummary, ...],
        active_execution_options: tuple[ExecutionOptionSummary, ...],
    ) -> CatalogExpansionWorkflowResult:
        # The compatibility parameter name is ``new_contexts``; the payload
        # deliberately contains every context required by the new goal so the
        # model cannot silently fall back to a context's generic definition.
        request = {
            "goals": [item.model_dump(mode="json") for item in goals],
            "new_training_contexts": [
                item.model_dump(mode="json") for item in new_contexts
            ],
            "active_training_contexts": [
                item.model_dump(mode="json") for item in active_contexts
            ],
            "active_capabilities": [
                item.model_dump(mode="json") for item in active_capabilities
            ],
            "active_execution_options": [
                item.model_dump(mode="json") for item in active_execution_options
            ],
        }
        return await self._invoke(
            user_id=user_id,
            action="DEFINE_CAPABILITIES",
            request=request,
        )

    async def _invoke(
        self,
        *,
        user_id: UUID,
        action: str,
        request: Mapping[str, object],
    ) -> CatalogExpansionWorkflowResult:
        run_id = uuid4()
        started_at = datetime.now(UTC)
        started_clock = monotonic()
        metadata = AIWorkflowRunMetadata(
            workflow_name=f"catalog_expansion_{action.casefold()}",
            run_id=run_id,
            onboarding_step=OnboardingStep.GOAL_CONFIRMED,
            provider_mode=self._model.provider_mode,
            model_name=self._model.model_name,
            started_at=started_at,
        )
        await self._observe_started(metadata)
        initial: CatalogExpansionState = {
            "action": cast(
                Literal["MAP_CONTEXTS", "DEFINE_CAPABILITIES"],
                action,
            ),
            "request_json": json.dumps(request, separators=(",", ":")),
        }
        try:
            raw = await asyncio.wait_for(
                self._graph.ainvoke(
                    initial,
                    config=build_langchain_run_config(
                        metadata,
                        callbacks=self._callbacks,
                    ),
                ),
                timeout=self._timeout_seconds,
            )
            state = cast(CatalogExpansionState, raw)
            result = CatalogExpansionWorkflowResult(
                outcome=state.get("outcome", "fallback_required"),
                context_mapping=state.get("context_mapping"),
                capability_definition=state.get("capability_definition"),
                error_code=state.get("error_code"),
                prompt_tokens=state.get("prompt_tokens"),
                completion_tokens=state.get("completion_tokens"),
            )
        except TimeoutError:
            result = CatalogExpansionWorkflowResult(
                outcome="provider_error",
                error_code="catalog_expansion_timeout",
            )
        completed_at = datetime.now(UTC)
        latency_ms = max(0, round((monotonic() - started_clock) * 1000))
        if result.outcome == "provider_error":
            await self._observe_failed(
                AIWorkflowRunError(
                    metadata=metadata,
                    failed_at=completed_at,
                    latency_ms=latency_ms,
                    error_code=result.error_code or "catalog_expansion_failure",
                )
            )
        else:
            await self._observe_completed(
                AIWorkflowRunResult(
                    metadata=metadata,
                    outcome=(
                        "confirmation_required"
                        if result.outcome == "succeeded"
                        else "fallback_required"
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
            pass

    async def _observe_completed(self, result: AIWorkflowRunResult) -> None:
        try:
            await self._observer.on_run_completed(result)
        except Exception:
            pass

    async def _observe_failed(self, error: AIWorkflowRunError) -> None:
        try:
            await self._observer.on_run_failed(error)
        except Exception:
            pass


def create_catalog_expansion_workflow(
    settings: Settings,
    *,
    observer: AIWorkflowObserver | None = None,
    callbacks: Sequence[BaseCallbackHandler] = (),
) -> LangGraphCatalogExpansionWorkflow:
    model = create_goal_extraction_model(settings, timeout_seconds=60.0)
    return LangGraphCatalogExpansionWorkflow(
        graph=build_catalog_expansion_graph(model=model),
        model=model,
        observer=observer,
        callbacks=callbacks,
        timeout_seconds=70.0,
    )
