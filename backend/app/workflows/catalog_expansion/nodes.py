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
from app.workflows.prompts.catalog_expansion import (
    GOAL_CONTEXT_CAPABILITY_EXPANSION,
    NEW_GOAL_CONTEXT_EXPANSION,
)


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
        system = (
            NEW_GOAL_CONTEXT_EXPANSION
            if action == "MAP_CONTEXTS"
            else GOAL_CONTEXT_CAPABILITY_EXPANSION
        )
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
