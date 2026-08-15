"""Transient state for one catalog-expansion model call."""

from __future__ import annotations

from typing import Literal, TypedDict

from app.schemas.catalog_expansion import (
    ContextCapabilityOutput,
    GoalContextMappingOutput,
)


class CatalogExpansionState(TypedDict, total=False):
    action: Literal["MAP_CONTEXTS", "DEFINE_CAPABILITIES"]
    request_json: str
    outcome: Literal["succeeded", "fallback_required", "provider_error"]
    context_mapping: GoalContextMappingOutput
    capability_definition: ContextCapabilityOutput
    error_code: str
    prompt_tokens: int | None
    completion_tokens: int | None
