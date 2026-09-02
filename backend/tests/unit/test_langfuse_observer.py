"""Offline coverage for the optional privacy-safe Langfuse composition path."""

from __future__ import annotations

from app.config import Settings
from app.integrations.llm.mock import DeterministicFakeOnboardingModel
from app.observability.langfuse import create_ai_workflow_observer
from app.observability.noop import NoOpAIWorkflowObserver
from app.services.onboarding.availability import AvailabilityExtractionService


def test_langfuse_defaults_to_noop_without_configuration() -> None:
    observer = create_ai_workflow_observer(
        Settings(database_url="sqlite+aiosqlite:///:memory:")
    )

    assert isinstance(observer, NoOpAIWorkflowObserver)


def test_langfuse_enabled_without_keys_stays_noop() -> None:
    observer = create_ai_workflow_observer(
        Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            langfuse_enabled=True,
        )
    )

    assert isinstance(observer, NoOpAIWorkflowObserver)


class _SpyObserver:
    def __init__(self) -> None:
        self.started: list[object] = []
        self.completed: list[object] = []
        self.failed: list[object] = []

    async def on_run_started(self, metadata: object) -> None:
        self.started.append(metadata)

    async def on_run_completed(self, result: object) -> None:
        self.completed.append(result)

    async def on_run_failed(self, error: object) -> None:
        self.failed.append(error)


async def test_availability_observer_receives_sanitized_success_metadata() -> None:
    observer = _SpyObserver()
    service = AvailabilityExtractionService(
        DeterministicFakeOnboardingModel(), observer=observer
    )

    await service.extract("Tuesday evening, one hour", goal_disciplines=("running",))

    assert len(observer.started) == 1
    assert len(observer.completed) == 1
    assert observer.failed == []
    assert not hasattr(observer.started[0], "text")
