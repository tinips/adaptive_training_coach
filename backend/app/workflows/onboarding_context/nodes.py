"""LangGraph node for deterministic literal context validation."""

from __future__ import annotations

from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda

from app.integrations.llm.models import StructuredOnboardingModel
from app.workflows.onboarding_context.prompts import is_supported_free_text_step
from app.workflows.onboarding_context.state import FreeTextValidationGraphState

FreeTextValidationNode = Runnable[
    FreeTextValidationGraphState, FreeTextValidationGraphState
]


def make_free_text_validation_node(
    model: StructuredOnboardingModel,
) -> FreeTextValidationNode:
    """Accept any non-empty answer without asking the model to judge it."""

    del model

    async def validate(
        state: FreeTextValidationGraphState,
        config: RunnableConfig,
    ) -> FreeTextValidationGraphState:
        del config
        if not is_supported_free_text_step(state["step"]):
            return {
                "outcome": "provider_error",
                "error_code": "unsupported_context_step",
            }
        if not state["user_text"].strip():
            return {"outcome": "retry_required", "error_code": "empty_text"}
        return {"outcome": "accepted", "error_code": None}

    return RunnableLambda(validate, name="validate_onboarding_context_text")
