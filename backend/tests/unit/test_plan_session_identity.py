"""Stable, code-generated planned-session identity.

See docs/decisions/locked.md, "First-week evaluator": "every planned session
receives a code-generated UUID. Array ordinal is never permanent identity. A
plan revision preserves a session UUID only when it represents the same
intended session; materially replaced sessions receive new UUIDs..."

docs/briefs/backlog/first-week-evaluator.md, "Stable session reference": "Add
a code-generated UUID after model generation and before persistence. Never
use array ordinal as permanent identity and never ask the LLM to invent an
ID."
"""

from __future__ import annotations

import uuid

from app.schemas.weekly_plans import (
    FirstWeekEnduranceSession,
    FirstWeekEnduranceSessionPrescription,
    FirstWeekPlan,
    FirstWeekStrengthSession,
    FirstWeekStrengthSessionPrescription,
    PlanSession,
    PlanSessionPrescription,
)


def _pace_intensity() -> dict[str, object]:
    return {
        "metric": "PACE_SECONDS_PER_KM",
        "target_range": [300.0, 330.0],
        "rpe_range": [3, 4],
        "guidance": "Steady, controlled effort.",
    }


def _endurance_session_payload() -> dict[str, object]:
    return {
        "discipline": "RUNNING",
        "purpose": "Build a consistent aerobic habit.",
        "intensity": _pace_intensity(),
        "objective": "Cover the distance at the prescribed pace.",
        "targets": {"distance_range_meters": (8000.0, 9000.0)},
        "execution": "Keep the effort controlled throughout.",
    }


def _strength_session_payload() -> dict[str, object]:
    return {
        "discipline": "STRENGTH",
        "purpose": "Build supportive full-body strength.",
        "intensity": {
            "metric": "RPE",
            "target_range": [2.0, 3.0],
            "rpe_range": [2, 3],
            "guidance": "Easy, controlled effort.",
        },
        "objective": "Move well under light, controlled load.",
        "targets": {"duration_minutes": 30},
        "execution": "Full-body circuit, controlled tempo.",
    }


def _first_week_plan_payload(sessions: list[dict[str, object]]) -> dict[str, object]:
    counts: dict[str, int] = {}
    minutes: dict[str, int] = {}
    for raw in sessions:
        discipline = raw["discipline"]
        counts[discipline] = counts.get(discipline, 0) + 1
        targets = raw.get("targets", {})
        duration = (
            targets.get("duration_minutes") if isinstance(targets, dict) else None
        )
        minutes[discipline] = minutes.get(discipline, 0) + (duration or 0)
    return {
        "plan_kind": "FIRST_WEEK_MENU",
        "week_start": "2026-09-14",
        "sessions": sessions,
        "guardrails": ["Stop if you feel sharp pain."],
        "logging_instructions": ["Log every session you complete."],
        "sessions_per_discipline": counts,
        "total_minutes_per_discipline": minutes,
    }


class TestSessionIdentityIsCodeGenerated:
    def test_plan_session_gets_a_code_generated_uuid_by_default(self) -> None:
        session = PlanSession(**_endurance_session_payload())
        assert isinstance(session.id, uuid.UUID)

    def test_first_week_endurance_session_gets_a_code_generated_uuid(self) -> None:
        session = FirstWeekEnduranceSession(**_endurance_session_payload())
        assert isinstance(session.id, uuid.UUID)

    def test_first_week_strength_session_gets_a_code_generated_uuid(self) -> None:
        session = FirstWeekStrengthSession(**_strength_session_payload())
        assert isinstance(session.id, uuid.UUID)

    def test_two_sessions_built_without_explicit_ids_are_distinct(self) -> None:
        first = PlanSession(**_endurance_session_payload())
        second = PlanSession(**_endurance_session_payload())
        assert first.id != second.id

    def test_prescription_schema_has_no_id_field_the_model_could_fill(self) -> None:
        # The LLM authors PlanSessionPrescription/FirstWeekSessionPrescription;
        # neither carries an "id" field, so the model literally cannot
        # invent one. Code assigns identity only on the persisted PlanSession
        # side, never by trusting model output.
        assert "id" not in PlanSessionPrescription.model_fields
        assert "id" not in FirstWeekEnduranceSessionPrescription.model_fields
        assert "id" not in FirstWeekStrengthSessionPrescription.model_fields

    def test_prescription_built_from_model_dump_of_a_session_does_not_carry_id(
        self,
    ) -> None:
        # Even a full dict dump of a persisted session cannot be fed back
        # into the model-facing prescription type as an id: extra="forbid"
        # means such a stray key raises, but the practical path is: code
        # always constructs a PlanSession from a *_require_and_derive_*-safe
        # prescription dict without "id", so this documents the boundary
        # directly on the type surface instead.
        assert PlanSessionPrescription.model_fields.keys() == {
            "discipline",
            "purpose",
            "intensity",
            "objective",
            "targets",
            "execution",
        }


class TestSessionIdentityRoundTrip:
    def test_explicit_id_is_preserved_through_json_round_trip(self) -> None:
        original_id = uuid.uuid4()
        session = PlanSession(id=original_id, **_endurance_session_payload())
        dumped = session.model_dump_json()
        restored = PlanSession.model_validate_json(dumped)
        assert restored.id == original_id

    def test_plan_level_round_trip_preserves_every_session_id(self) -> None:
        session = FirstWeekEnduranceSession(**_endurance_session_payload())
        plan = FirstWeekPlan.model_validate(
            _first_week_plan_payload([session.model_dump(mode="python")])
        )
        dumped = plan.model_dump(mode="json")
        restored = FirstWeekPlan.model_validate(dumped)
        assert restored.sessions[0].id == plan.sessions[0].id
        assert restored.sessions[0].id == session.id


def _legacy_v4_session_payload_without_id() -> dict[str, object]:
    """A schema-v4 stored session dict: fully derived, no "id" key at all."""

    session = FirstWeekEnduranceSession(**_endurance_session_payload())
    dumped = session.model_dump(mode="json")
    del dumped["id"]
    return dumped


class TestLegacySchemaV4Compatibility:
    def test_a_legacy_v4_payload_with_no_session_id_still_loads(self) -> None:
        # v4 rows predate this identity feature and never held an "id" key.
        # They must still load for display, so the field defaults rather
        # than rejecting the payload.
        session_payload = _legacy_v4_session_payload_without_id()
        assert "id" not in session_payload
        plan = FirstWeekPlan.model_validate(_first_week_plan_payload([session_payload]))
        assert isinstance(plan.sessions[0].id, uuid.UUID)

    def test_ids_synthesized_for_a_legacy_payload_are_not_stable_across_loads(
        self,
    ) -> None:
        # Documents the known limitation: a legacy (pre-identity) plan has no
        # durable session identity, so it must not be treated as linkable.
        # Loading the same raw payload twice produces two different ids.
        payload = _first_week_plan_payload([_legacy_v4_session_payload_without_id()])
        first_load = FirstWeekPlan.model_validate(payload)
        second_load = FirstWeekPlan.model_validate(payload)
        assert first_load.sessions[0].id != second_load.sessions[0].id


class TestSessionUuidUniquenessWithinAPlan:
    def test_a_plan_with_multiple_sessions_gets_distinct_ids_per_session(
        self,
    ) -> None:
        endurance = FirstWeekEnduranceSession(**_endurance_session_payload())
        strength = FirstWeekStrengthSession(**_strength_session_payload())
        plan = FirstWeekPlan.model_validate(
            _first_week_plan_payload(
                [
                    endurance.model_dump(mode="python"),
                    strength.model_dump(mode="python"),
                ]
            )
        )
        ids = [session.id for session in plan.sessions]
        assert len(ids) == len(set(ids))
