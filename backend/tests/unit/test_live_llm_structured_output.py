"""The live adapter must tell the model the output shape it wants."""

from __future__ import annotations

import inspect
import json

from langchain_core.messages import AIMessage

from app.integrations.llm.live import (
    OpenAICompatibleOnboardingModel,
    _recover_structured_json,
)


def test_structured_output_sends_the_schema_to_the_model() -> None:
    """json_mode never sends the schema, so the model has to guess it.

    Measured against live DeepSeek: guessing produced 21 validation errors.
    """

    source = inspect.getsource(OpenAICompatibleOnboardingModel.ainvoke_structured)

    assert 'method="function_calling"' in source
    assert 'method="json_mode"' not in source


def test_structured_output_recovers_normalized_tool_call_arguments() -> None:
    payload = {"sessions": [{"day": "monday"}]}
    raw = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "FirstWeekPlanPrescription",
                "args": payload,
                "id": "call_1",
                "type": "tool_call",
            }
        ],
    )

    recovered, malformed = _recover_structured_json(parsed=None, raw=raw)

    assert recovered == payload
    assert malformed is False


def test_structured_output_recovers_openai_compatible_tool_arguments() -> None:
    payload = {"sessions": [{"day": "monday"}]}
    raw = AIMessage(
        content="",
        additional_kwargs={
            "tool_calls": [
                {
                    "function": {
                        "name": "FirstWeekPlanPrescription",
                        "arguments": json.dumps(payload),
                    }
                }
            ]
        },
    )

    recovered, malformed = _recover_structured_json(parsed=None, raw=raw)

    assert recovered == payload
    assert malformed is False
