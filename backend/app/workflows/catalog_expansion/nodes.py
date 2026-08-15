"""Strict structured-output node for catalog expansion."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError

from app.domain.enums import OnboardingStep
from app.integrations.llm.models import StructuredOnboardingModel
from app.schemas.catalog_expansion import (
    ContextCapabilityOutput,
    GoalContextMappingOutput,
)
from app.workflows.catalog_expansion.state import CatalogExpansionState

_MAP_SYSTEM = """Maintain a reusable global training catalog. Return one JSON object
matching the schema; no prose. Map every new template exactly once.

Return only the smallest useful context set. Reuse a context only for the same
sport/movement and environment. A PRIMARY needs a
TARGET for direct practice of its defining modality. Conditioning and cross-training
are SUPPORTING, not substitutes for that TARGET. If no direct context exists, CREATE
a general one (for example, rowing needs a rowing context; strength_general may only
support it). Reuse a general active context for supporting conditioning instead of
creating a sport-named duplicate; do not CREATE nonessential supporting contexts.
SUPPORTING templates may return only SUPPORTING contexts.

Discipline is one of RUNNING, CYCLING, HIKING, SWIMMING, STRENGTH, OTHER; use OTHER
for rowing or another unmatched modality. Do not define capabilities, execution
options, workouts, or plans. USE_EXISTING codes must occur in
active_training_contexts and use null display_name/description; otherwise CREATE.
Generated text is concise, general English without personal data, dates, performance
targets, health data, URLs, brands, local events, plans, or purchase advice."""

_CAPABILITIES_SYSTEM = """Maintain reusable execution knowledge for each supplied new
context. Return one JSON object matching the schema; no prose. Do not create or rename
contexts.

Define 1-4 execution options per context. Include a PREFERRED option; every option
needs a REQUIRED capability. Option role is PREFERRED or SUBSTITUTE, priority is an
integer, and requirement importance is REQUIRED, RECOMMENDED, or OPTIONAL.

Capabilities are only physical EQUIPMENT, location/resource ACCESS, or FACILITY—not
methods, workouts, drills, services, coaches, content, technique, plans, goals, or
generic concepts. Return the smallest referenced set with no duplicates or unused
items. The set of capability codes in capabilities must equal the set referenced by
all requirements. Before assigning a decision, compare each code with
active_capabilities: an exact match must be USE_EXISTING with null
display_name/description; an absent code must be CREATE with definitions. Use no more
than three short limitations per option. Final check: never reference a capability
only inside requirements. For example, a requirement for gym_access requires this
capabilities item: {"decision":"USE_EXISTING","code":"gym_access",
"display_name":null,"description":null,"kind":"FACILITY"}. Generated text is
concise, general English without personal data, dates, performance targets, health
data, URLs, brands, plans, or purchase advice."""


def make_expand_catalog_node(
    model: StructuredOnboardingModel,
) -> Callable[
    [CatalogExpansionState, RunnableConfig],
    Awaitable[CatalogExpansionState],
]:
    async def expand(
        state: CatalogExpansionState,
        config: RunnableConfig,
    ) -> CatalogExpansionState:
        action = state["action"]
        schema = (
            GoalContextMappingOutput
            if action == "MAP_CONTEXTS"
            else ContextCapabilityOutput
        )
        system = _MAP_SYSTEM if action == "MAP_CONTEXTS" else _CAPABILITIES_SYSTEM
        schema_json = json.dumps(
            schema.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            response = await model.ainvoke_structured(
                step=OnboardingStep.GOAL_CONFIRMED,
                schema=schema,
                messages=[
                    SystemMessage(
                        content=f"{system}\nThe exact JSON Schema is: {schema_json}"
                    ),
                    HumanMessage(content=state["request_json"]),
                ],
                config=config,
            )
            output = schema.model_validate(response.output)
        except ValidationError:
            return {
                "outcome": "fallback_required",
                "error_code": "malformed_catalog_expansion",
            }
        except TimeoutError:
            raise
        except Exception:
            return {
                "outcome": "provider_error",
                "error_code": "catalog_expansion_provider_error",
            }
        result: CatalogExpansionState = {
            "outcome": "succeeded",
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
        }
        if isinstance(output, GoalContextMappingOutput):
            result["context_mapping"] = output
        else:
            result["capability_definition"] = output
        return result

    return expand
