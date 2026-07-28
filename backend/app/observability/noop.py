"""No-op observer used until an external monitoring provider is introduced."""

from __future__ import annotations

from app.observability.protocol import (
    AIWorkflowRunError,
    AIWorkflowRunMetadata,
    AIWorkflowRunResult,
)


class NoOpAIWorkflowObserver:
    """Accept workflow lifecycle events without I/O or retained state."""

    async def on_run_started(self, metadata: AIWorkflowRunMetadata) -> None:
        del metadata

    async def on_run_completed(self, result: AIWorkflowRunResult) -> None:
        del result

    async def on_run_failed(self, error: AIWorkflowRunError) -> None:
        del error
