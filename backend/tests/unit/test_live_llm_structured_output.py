"""The live adapter must tell the model the output shape it wants."""

from __future__ import annotations

import inspect

from app.integrations.llm.live import OpenAICompatibleOnboardingModel


def test_structured_output_sends_the_schema_to_the_model() -> None:
    """json_mode never sends the schema, so the model has to guess it.

    Measured against live DeepSeek: guessing produced 21 validation errors.
    """

    source = inspect.getsource(OpenAICompatibleOnboardingModel.ainvoke_structured)

    assert 'method="function_calling"' in source
    assert 'method="json_mode"' not in source
