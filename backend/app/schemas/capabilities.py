"""Typed boundaries for catalog review and deterministic execution assessment."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.enums import (
    CapabilityImportance,
    CapabilityKind,
    ContextAssessmentStatus,
    ExecutionOptionRole,
    GoalContextRole,
)


class CapabilityOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    code: str
    display_name: str
    kind: CapabilityKind
    importance: CapabilityImportance
    execution_roles: tuple[ExecutionOptionRole, ...]
    target_context_codes: tuple[str, ...]
    selected: bool = False


class CapabilityReviewContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    display_name: str
    role: GoalContextRole


class CapabilityReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contexts: tuple[CapabilityReviewContext, ...]
    options: tuple[CapabilityOption, ...]


class AvailableExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    display_name: str
    role: ExecutionOptionRole
    limitations: tuple[str, ...] = ()


class ContextExecutionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_context: str
    target_display_name: str
    status: ContextAssessmentStatus
    default_execution: str | None = None
    available_executions: tuple[AvailableExecution, ...] = ()
    missing_required: tuple[str, ...] = ()
    missing_recommended: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class GoalExecutionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contexts: tuple[ContextExecutionAssessment, ...]


class CapabilityAccessItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    display_name: str
    kind: CapabilityKind
