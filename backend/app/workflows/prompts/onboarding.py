"""Versioned static prompt templates for athlete onboarding workflows.

Increment a contract version whenever its static wording changes, and update the
prompt-regression tests in the same change. Dynamic athlete data belongs in the
workflow and is injected only when the prompt is rendered.
"""

from __future__ import annotations

from typing import Final, Literal

type FutureEventDatePolicyConsumer = Literal[
    "goal_extraction",
    "onboarding_modification",
    "schema_description",
]
type ExplicitOnboardingChangeToolPolicyConsumer = Literal[
    "telegram_orchestrator",
    "onboarding_modification",
]

_FUTURE_EVENT_DATE_POLICY_TEMPLATE: Final = (
    "{calendar_date_rule}{future_event_rule}{yearless_date_rule}"
    "{ambiguous_or_past_date_rule}"
)
_FUTURE_EVENT_DATE_POLICY_ARGUMENTS: Final[
    dict[FutureEventDatePolicyConsumer, dict[str, str]]
] = {
    "goal_extraction": {
        "calendar_date_rule": (
            "Use an event_date only for a complete, unambiguous calendar date; "
            "never invent a day for a month-only or otherwise ambiguous date. "
        ),
        "future_event_rule": "Training goals are inherently future events. ",
        "yearless_date_rule": (
            "If the athlete provides a calendar date containing only a month and a "
            "day without a year, calculate the correct calendar year such that the "
            "resulting event_date always falls in the FUTURE relative to today's "
            "date. "
        ),
        "ambiguous_or_past_date_rule": (
            "If the athlete explicitly supplies a year that makes the date past, "
            "return event_date as null and mark event_date ambiguous instead of "
            "changing the explicit year. "
        ),
    },
    "onboarding_modification": {
        "calendar_date_rule": "",
        "future_event_rule": "Training events are future events. ",
        "yearless_date_rule": (
            "For an explicit month and day without a year, set event_date to the "
            "next occurrence strictly after today and send it as YYYY-MM-DD. "
        ),
        "ambiguous_or_past_date_rule": (
            "If a supplied date is ambiguous or explicitly in the past, ask for "
            "clarification and do not send event_date. "
        ),
    },
    "schema_description": {
        "calendar_date_rule": (
            "The athlete's explicit event date as an ISO calendar date in "
            "YYYY-MM-DD format. "
        ),
        "future_event_rule": "",
        "yearless_date_rule": (
            "Resolve a month and day without a year to the next future occurrence "
            "relative to today's date."
        ),
        "ambiguous_or_past_date_rule": "",
    },
}


def future_event_date_policy(consumer: FutureEventDatePolicyConsumer) -> str:
    """Render legacy wording for a consumer of the shared date policy.

    The variants intentionally preserve the previously sent prompt bytes while
    centralizing the same domain policy in one reusable template.
    """

    return _FUTURE_EVENT_DATE_POLICY_TEMPLATE.format(
        **_FUTURE_EVENT_DATE_POLICY_ARGUMENTS[consumer]
    )


_EXPLICIT_ONBOARDING_CHANGE_TOOL_POLICY: Final[
    dict[ExplicitOnboardingChangeToolPolicyConsumer, str]
] = {
    "telegram_orchestrator": (
        "1. DATA CORRECTIONS: If the user explicitly wants to change, update, "
        "correct, or replace an athlete field ({supported_fields}), you MUST call "
        "'{tool_name}'. This rule overrides any active question.\n"
    ),
    "onboarding_modification": (
        "You manage modifications to an athlete's completed onboarding "
        "data. The supported fields are {supported_fields}. Call "
        "{tool_name} "
        "once with every supported value explicitly supplied in the latest "
        "request, even when fields belong to different records. Do not call "
        "the tool for an incomplete request such as 'change my goal'; ask a "
        "short clarifying question instead. A main goal must name a concrete "
        "race, distance, discipline, or measurable athletic objective. Vague "
        "phrases such as 'something fast', 'a race', or 'get fitter' are not "
        "valid main goals; ask for a concrete race or distance. Treat the "
        "athlete's newest message as authoritative. If they abandon or "
        "replace a pending request, follow the new request and do not carry "
        "abandoned values into the tool call. Preserve concrete main-goal "
        "wording without embellishment: for example, use 'Ironman', '5k "
        "race', or 'Barcelona Marathon' when that is what the athlete asks "
        "for. Never infer demographic values. "
    ),
}


def explicit_onboarding_change_tool_policy(
    consumer: ExplicitOnboardingChangeToolPolicyConsumer,
    *,
    tool_name: str,
    supported_fields: str,
) -> str:
    """Render the shared explicit-correction policy for one tool consumer.

    Tool names and supported fields are injected by each workflow because their
    available actions differ. The variant wording preserves the legacy prompt
    text sent by each consumer.
    """

    return _EXPLICIT_ONBOARDING_CHANGE_TOOL_POLICY[consumer].format(
        tool_name=tool_name,
        supported_fields=supported_fields,
    )


GOAL_EXTRACTION_CONTRACT_VERSION: Final = "4"
"""Version of the static goal-extraction contract sent to the model."""

GOAL_EXTRACTION_CONTRACT: Final = (
    "Extract a field patch from the latest athlete onboarding goal answer. "
    "Return exactly one flat JSON object matching the requested schema and no "
    "prose. The top-level keys must be main_goal, event_date, target_outcome, "
    "secondary_priority, primary_template, supporting_template, missing_fields, "
    "ambiguous_fields, and message_status. "
    "Never nest fields under patch, goal, result, or any other wrapper key. "
    "The only semantic patch fields are main_goal, event_date, target_outcome, "
    "secondary_priority, primary_template, and supporting_template. Set a "
    "semantic field only when the latest user "
    "message explicitly adds or corrects it; otherwise return null. Null means "
    "preserve the current draft value. Never copy unchanged values from the "
    "current draft into the patch. For UPDATE_EXISTING_GOAL, the latest message "
    "may be a short answer to the draft's current missing or ambiguous field. "
    "Interpret that fragment in the context of the current draft: for example, "
    "if target_outcome is missing, 'without stopping' means target_outcome is "
    "'Complete without stopping'; if event_date is missing, '11 July 2027' "
    "supplies event_date. Correct obvious spelling mistakes when the intended "
    "meaning is clear. Use concise English while preserving the user's meaning. "
    "main_goal must be specific enough to "
    "influence training; 'running' or 'train to run' alone is incomplete. "
    "target_outcome states what success means and does not need to be numeric. "
    "An explicitly stated qualitative outcome such as finishing safely, without "
    "stopping, in a good time, or in a decent time is valid. Preserve that "
    "meaning concisely instead of requiring a specific finish time. "
    "When the athlete explicitly gives both an outcome and a separate secondary "
    "priority in the same sentence, keep them separate. For example, 'finish in "
    "a decent time while maintaining muscle' means target_outcome is 'Finish in "
    "a decent time' and secondary_priority is 'Maintain muscle'. "
    "secondary_priority is optional, "
    "must be explicitly stated, and must never be listed as missing. "
    "When main_goal is supplied, primary_template must also classify it. Use "
    "USE_EXISTING only with an ACTIVE PRIMARY code from the supplied catalog. "
    "Catalog entries use template_type only to identify PRIMARY or SUPPORTING; "
    "never copy template_type or return a field named kind. For an existing "
    "primary return exactly, for example, "
    '{"decision":"USE_EXISTING","code":'
    '"TRIATHLON_HALF_DISTANCE","display_name":null,'
    '"description":null}. '
    "Otherwise use CREATE with a stable uppercase general code, concise English "
    "display name, and general English description. Do not create templates for "
    "event brands, locations, dates, or individual race editions when a general "
    "template fits. When secondary_priority is supplied, supporting_template must "
    "classify it. Use an ACTIVE SUPPORTING code, CREATE a general supporting "
    "template, or UNSUPPORTED when it cannot safely become reusable catalog "
    "knowledge. Existing supporting templates use the same decision/code/null "
    "name/null description shape. Use NONE only when the athlete explicitly has "
    "no secondary priority. Never return disciplines, contexts, equipment, "
    "capabilities, or "
    "training recommendations. "
    f"{future_event_date_policy('goal_extraction')}A null event_date is "
    "valid when the user has no date yet or the goal has no event. List only "
    "genuinely missing or ambiguous fields. Use COMPLETE only when main_goal "
    "and target_outcome are known and the date is known, explicitly unknown, "
    "or not applicable. Use NEEDS_CLARIFICATION otherwise. Use OFF_TOPIC when "
    "the answer is unrelated: in that case return null for every semantic patch "
    "field and do not derive goal facts from it. missing_fields, "
    "ambiguous_fields, and message_status must describe the resulting goal after "
    "the patch is applied to the current draft. "
)


def render_goal_extraction_system_prompt(
    *,
    action: str,
    current_date: str,
    draft_json: str,
    catalog_json: str,
) -> str:
    """Inject per-invocation values without changing the static contract."""

    return (
        f"{GOAL_EXTRACTION_CONTRACT}Today's date is: {current_date}. "
        f"Operation: {action}. Current persisted draft: {draft_json}. "
        f"ACTIVE goal template catalog: {catalog_json}"
    )
