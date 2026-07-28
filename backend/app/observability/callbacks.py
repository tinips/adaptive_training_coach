"""Central construction of safe LangChain invocation configuration."""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig

from app.observability.protocol import AIWorkflowRunMetadata


def build_langchain_run_config(
    metadata: AIWorkflowRunMetadata,
    *,
    callbacks: Sequence[BaseCallbackHandler] = (),
) -> RunnableConfig:
    """Build the single callback/tracing attachment point for AI workflows."""

    return RunnableConfig(
        run_id=metadata.run_id,
        run_name=metadata.workflow_name,
        callbacks=list(callbacks),
        tags=["onboarding", metadata.onboarding_step.value.lower()],
        metadata={
            "workflow_name": metadata.workflow_name,
            "onboarding_step": metadata.onboarding_step.value,
            "provider_mode": metadata.provider_mode,
            "model_name": metadata.model_name,
        },
    )
