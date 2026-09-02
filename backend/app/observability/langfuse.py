"""Privacy-safe, opt-in Langfuse observer.

This module never accepts prompt text, user input, images, or credentials other
than the SDK keys required to send the already-sanitized protocol records.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.observability.noop import NoOpAIWorkflowObserver
from app.observability.protocol import (
    AIWorkflowObserver,
    AIWorkflowRunError,
    AIWorkflowRunMetadata,
    AIWorkflowRunResult,
)


class LangfuseAIWorkflowObserver:
    """Send only protocol-safe workflow lifecycle fields to Langfuse."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._active: dict[str, Any] = {}

    async def on_run_started(self, metadata: AIWorkflowRunMetadata) -> None:
        manager = self._client.start_as_current_span(
            name=metadata.workflow_name,
            trace_context={"trace_id": str(metadata.run_id)},
            metadata=self._metadata(metadata),
        )
        span = manager.__enter__()
        self._active[str(metadata.run_id)] = (manager, span)

    async def on_run_completed(self, result: AIWorkflowRunResult) -> None:
        manager, span = self._active.pop(str(result.metadata.run_id), (None, None))
        if span is None:
            return
        usage_details = {
            key: value
            for key, value in {
                "input": result.prompt_tokens,
                "output": result.completion_tokens,
            }.items()
            if value is not None
        }
        span.update(
            metadata={
                **self._metadata(result.metadata),
                "outcome": result.outcome,
                "latency_ms": result.latency_ms,
            },
            usage_details=usage_details or None,
        )
        manager.__exit__(None, None, None)

    async def on_run_failed(self, error: AIWorkflowRunError) -> None:
        manager, span = self._active.pop(str(error.metadata.run_id), (None, None))
        if span is None:
            return
        span.update(
            level="ERROR",
            metadata={
                **self._metadata(error.metadata),
                "error_code": error.error_code,
                "latency_ms": error.latency_ms,
            },
        )
        manager.__exit__(None, None, None)

    @staticmethod
    def _metadata(metadata: AIWorkflowRunMetadata) -> dict[str, object]:
        return {
            "run_id": str(metadata.run_id),
            "onboarding_step": metadata.onboarding_step.value,
            "provider_mode": metadata.provider_mode,
            "model_name": metadata.model_name,
            "started_at": metadata.started_at.isoformat(),
        }


def create_ai_workflow_observer(settings: Settings) -> AIWorkflowObserver:
    """Create Langfuse lazily; disabled/misconfigured deployments stay no-op."""

    secret = settings.langfuse_secret_key
    if (
        not settings.langfuse_enabled
        or not settings.langfuse_public_key
        or secret is None
        or not secret.get_secret_value()
    ):
        return NoOpAIWorkflowObserver()
    try:
        from langfuse import Langfuse

        return LangfuseAIWorkflowObserver(
            Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=secret.get_secret_value(),
                host=settings.langfuse_host,
            )
        )
    except Exception:
        # Observability must never prevent the coach from starting.
        return NoOpAIWorkflowObserver()
