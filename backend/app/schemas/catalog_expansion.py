"""Strict LLM and application boundaries for dynamic catalog expansion."""

from __future__ import annotations

from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import (
    CapabilityImportance,
    CapabilityKind,
    Discipline,
    ExecutionOptionRole,
    GoalContextRole,
    GoalTemplateKind,
)
from app.integrations.llm.models import GoalTemplateSummary

CatalogDecision = Literal["USE_EXISTING", "CREATE"]
CatalogExpansionOutcome = Literal["succeeded", "fallback_required", "provider_error"]


class GoalTemplateDraft(BaseModel):
    """Confirmed template candidate passed to catalog expansion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    kind: GoalTemplateKind
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)


class TrainingContextSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    discipline: Discipline


class CapabilitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    kind: CapabilityKind


class GoalContextProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: CatalogDecision
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    display_name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    discipline: Discipline
    role: GoalContextRole
    priority: int = Field(ge=0, le=1000)

    @model_validator(mode="after")
    def require_created_definition(self) -> GoalContextProposal:
        if self.decision == "CREATE" and (
            not self.display_name or not self.description
        ):
            raise ValueError("created contexts require a name and description")
        return self


class GoalContextSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    contexts: list[GoalContextProposal] = Field(min_length=1, max_length=12)


class GoalContextMappingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    templates: list[GoalContextSet] = Field(min_length=1, max_length=2)


class CapabilityProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: CatalogDecision
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    display_name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    kind: CapabilityKind

    @model_validator(mode="after")
    def require_created_definition(self) -> CapabilityProposal:
        if self.decision == "CREATE" and (
            not self.display_name or not self.description
        ):
            raise ValueError("created capabilities require a name and description")
        return self


class CapabilityRequirementProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    importance: CapabilityImportance


class ExecutionOptionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    display_name: str = Field(min_length=1, max_length=120)
    execution_context_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    role: ExecutionOptionRole
    priority: int = Field(ge=0, le=1000)
    limitations: list[str] = Field(default_factory=list, max_length=3)
    requirements: list[CapabilityRequirementProposal] = Field(
        min_length=1,
        max_length=8,
    )


class ContextExecutionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_context_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    options: list[ExecutionOptionProposal] = Field(min_length=1, max_length=4)


class ContextCapabilityOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capabilities: list[CapabilityProposal] = Field(default_factory=list, max_length=50)
    contexts: list[ContextExecutionDefinition] = Field(min_length=1, max_length=24)


class CatalogExpansionWorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: CatalogExpansionOutcome
    context_mapping: GoalContextMappingOutput | None = None
    capability_definition: ContextCapabilityOutput | None = None
    error_code: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class CatalogExpansionWorkflow(Protocol):
    async def map_goal_contexts(
        self,
        *,
        user_id: UUID,
        templates: tuple[GoalTemplateDraft, ...],
        active_goals: tuple[GoalTemplateSummary, ...],
        active_contexts: tuple[TrainingContextSummary, ...],
    ) -> CatalogExpansionWorkflowResult:
        """Map confirmed new templates to existing or proposed contexts."""

    async def define_context_capabilities(
        self,
        *,
        user_id: UUID,
        goals: tuple[GoalTemplateDraft, ...],
        new_contexts: tuple[GoalContextProposal, ...],
        active_contexts: tuple[TrainingContextSummary, ...],
        active_capabilities: tuple[CapabilitySummary, ...],
    ) -> CatalogExpansionWorkflowResult:
        """Define the complete capability set for the goal-context pairs.

        ``new_contexts`` is retained as the protocol name for compatibility;
        callers supply every context required by the newly created goal,
        including contexts reused from the canonical catalog. The model must
        reason about the goal and each context together, reusing canonical
        capabilities wherever they represent the requirement.
        """
