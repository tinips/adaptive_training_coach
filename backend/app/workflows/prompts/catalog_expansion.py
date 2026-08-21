"""Versioned static prompt templates for catalog-expansion workflows.

Increment a contract version whenever its static wording changes, and update the
prompt-regression tests in the same change. Dynamic catalog snapshots and goal
data are injected by the workflow and never stored in these templates.
"""

from __future__ import annotations

from typing import Final

CONTEXT_EXPANSION_CONTRACT_VERSION: Final = "2"
"""Version of the static new-goal context-expansion contract."""

NEW_GOAL_CONTEXT_EXPANSION: Final = (
    "You are defining the complete training-context structure for a NEW "
    "training goal. Return one JSON object matching the schema; no prose. "
    "You must determine all contexts materially required to train for this "
    "goal. Prefer an existing canonical context when it semantically matches "
    "the requirement; reuse it with decision USE_EXISTING and null "
    "display_name/description. Only propose CREATE when no supplied canonical "
    "context appropriately represents the requirement, and provide a stable "
    "lowercase code, display name, and description. Do not omit essential "
    "training contexts. Do not invent database IDs or relationships. "
    "Reuse a context only for the same sport/movement and environment. A "
    "PRIMARY goal needs a TARGET context for direct practice of its defining "
    "modality; conditioning and cross-training are SUPPORTING, not "
    "substitutes. If no direct context exists, CREATE a general one. Reuse a "
    "general active context for supporting conditioning instead of creating a "
    "sport-named duplicate; do not CREATE nonessential supporting contexts. "
    "For an event with named stations, segments, or challenges, represent each "
    "materially distinct challenge as its own TARGET context unless an active "
    "context is an exact match for that movement and environment. A generic "
    "functional-fitness or strength context may support conditioning, but it "
    "must not replace distinct sled, ergometer, carry, obstacle, or other "
    "event-specific challenge contexts. "
    "SUPPORTING templates may return only SUPPORTING contexts. Discipline is "
    "one of RUNNING, CYCLING, HIKING, SWIMMING, STRENGTH, OTHER; use OTHER for "
    "rowing or another unmatched modality. USE_EXISTING codes must occur in "
    "active_training_contexts and use null display_name/description; otherwise "
    "CREATE. Generated text is concise, general English without personal data, "
    "dates, performance targets, health data, URLs, brands, local events, "
    "plans, or purchase advice."
)

CAPABILITY_EXPANSION_CONTRACT_VERSION: Final = "3"
"""Version of the static goal-context capability-expansion contract."""

GOAL_CONTEXT_CAPABILITY_EXPANSION: Final = (
    "Define complete requirements for each goal-context pair. Return one JSON object; "
    "no prose. Use goal and context together. Return the complete capability set, "
    "not only missing capabilities. "
    "The contexts array must contain exactly one definition for every "
    "new_training_contexts code, with no other target_context_code; do not create "
    "or rename contexts. Use only supplied catalog data. "
    "Each option needs decision USE_EXISTING or CREATE. USE_EXISTING must copy "
    "an exact code from active_execution_options and preserve its target, "
    "execution context, role, priority, limitations, and requirements. CREATE "
    "only when no exact canonical option exists; provide display_name. The "
    "execution_context_code must be a supplied or newly proposed context code, "
    "never a capability code. Contexts are created only by the mapping stage. "
    "Return 1-4 options per context, including one PREFERRED; each needs a "
    "REQUIRED capability. Option role is PREFERRED or SUBSTITUTE; importance is "
    "REQUIRED, RECOMMENDED, or OPTIONAL. Capabilities are only EQUIPMENT, "
    "ACCESS, or FACILITY—not methods, workouts, drills, services, technique, "
    "plans, goals, or generic concepts. Use exact code match with "
    "active_capabilities; reuse those codes "
    "with USE_EXISTING and null display_name/description; CREATE absent codes "
    "with definitions. No duplicates. The set of capability codes in capabilities "
    "must equal the set referenced by all requirements. Use concise general English "
    "without personal data, URLs, brands, or advice."
)
