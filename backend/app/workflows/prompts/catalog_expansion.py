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

CAPABILITY_EXPANSION_CONTRACT_VERSION: Final = "2"
"""Version of the static goal-context capability-expansion contract."""

GOAL_CONTEXT_CAPABILITY_EXPANSION: Final = (
    "Define capability/equipment requirements for a goal-context pair. Return "
    "one schema-matching JSON object; no prose. "
    "Consider goal and context together; identify "
    "capabilities/equipment required for it. Prefer "
    "supplied canonical capabilities; reuse with USE_EXISTING and null "
    "display_name/description. "
    "Only propose CREATE when no suitable canonical capability exists. Return "
    "the complete capability set, not only missing capabilities. The contexts "
    "array MUST contain exactly one definition for every code in "
    "new_training_contexts, and no other target_context_code; copy those codes "
    "exactly. Do not create "
    "or rename contexts; use only the supplied "
    "catalog snapshot. Define 1-4 execution options per context. Include a "
    "PREFERRED option; every option needs a REQUIRED capability. Option role "
    "is PREFERRED or SUBSTITUTE, priority is an integer, and requirement "
    "importance is REQUIRED, RECOMMENDED, or OPTIONAL. Capabilities are only "
    "physical EQUIPMENT, location/resource ACCESS, or FACILITY—not methods, "
    "workouts, drills, services, technique, plans, goals, or generic concepts. "
    "Return the smallest referenced set, no duplicates. The set of capability "
    "codes in capabilities must equal the set referenced by all requirements. "
    "An exact code match with active_capabilities must be USE_EXISTING with "
    "null display_name/description; an absent code must be CREATE with "
    "definitions. Use short limitations only. Generated "
    "text is concise, general English without personal data, URLs, brands, or "
    "purchase advice."
)
