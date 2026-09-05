# The Three Planner Layers — Architecture

Status: layer 3 (Weekly Planner) is `BUILT` for both first-week and ongoing modes. Layers 1 and 2 (General Planner, Stage Planner) are `PROPOSED` — locked in principle (`docs/decisions/locked.md`), not yet implemented. This document exists to separate the three layers clearly before implementation starts, per the roadmap.

---

## Layer 1 — General Planner (`PROPOSED`)

Runs once per goal, or after a major re-plan (e.g. an injury, a goal change, a large availability shift).

- Divides the goal timeline into phases: Base → Build → Peak → Taper.
- Works across running, cycling, swimming, and triathlon goals — the phase model itself is discipline-agnostic (locked: "Phase model is discipline-agnostic; only session content is sport-specific," `docs/decisions/locked.md`).
- **Deterministic calculation:** phase dates and structural rules — cutting a goal timeline into phases by calendar time, not by CTL or any measured load, is locked as calendar math, i.e. pure code, no LLM call.
- **LLM judgment:** phase *objective* wording only — e.g. turning "Base phase, weeks 1-6" into a short athlete-facing description of what this phase is for. The LLM does not decide phase boundaries, durations, or which phases exist.
- Phase objectives may be templated initially — a v1 does not require an LLM call at all if a fixed set of phase-objective templates (one per phase type) covers the need. Whether templates are sufficient for v1, or an LLM call is justified from the start, is implementation judgment, not something this document decides — but the default assumption is templates first, LLM only if templates prove insufficient in practice.
- **Open, not decided here:** phase proportions per event type (marathon vs. sprint-distance vs. long-course) — `docs/decisions/open.md` already lists this; start with sensible defaults, tune later, per that entry.

## Layer 2 — Stage Planner (`PROPOSED`)

Runs at the beginning of each phase (once the General Planner has produced phase boundaries).

- Creates a lightweight week-by-week skeleton for that one phase only.
- Stores, per week: weekly focus, progression intent, and recovery/deload intent. Locked: "Stage skeleton commits load + focus + deload flag per week — NOT full workouts" (`docs/decisions/locked.md`).
- Does not generate complete workouts — that remains entirely Layer 3's job. The Stage Planner's output is guidance metadata for a week, not sessions.
- Its skeleton is guidance, not a hard commitment: it can be adjusted after a meaningful disruption (e.g. a missed week, an injury, a large adherence miss) rather than being treated as immutable once written.
- **Deterministic calculation:** progression rules within a phase (e.g. how load nominally increases week to week toward a peak, then deloads) are locked as deterministic progression rules — code, not LLM judgment.
- **LLM judgment:** none specified as required at this layer in the locked decisions; if any wording/description task exists here it would be the same kind of templated-objective work as Layer 1, not a planning decision.
- **Open, not decided here:** none specific to this layer beyond what's already listed for Layer 1 (phase proportions feed into how the stage skeleton is shaped).

## Layer 3 — Weekly Planner (`BUILT`, both modes)

Runs every week. This is the only layer with a live implementation today, in two modes:

- **FIRST_WEEK mode** (`BUILT`) — the probe/baseline week described fully in `docs/design/first-week-planner.md`. Receives a scoped, goal-excluded `prompt_context`; produces a menu, not scheduled sessions.
- **ONGOING mode** (`BUILT`) — generates dated sessions from current constraints. Today it does not yet receive a current phase or objective from a General/Stage Planner, because those layers don't exist yet — this is exactly roadmap step 9 ("wire the ongoing weekly planner to current phase and feedback").

Per the target design (once Layers 1-2 exist), the Weekly Planner receives:

- Current phase (from the General Planner).
- Current stage-week intent: focus, progression, deload flag (from the Stage Planner).
- Athlete constraints: availability, equipment, health limitations (already gathered today, per `_prepare()` in `docs/design/first-week-planner.md` Step 1).
- Fitness state (from `docs/design/fitness-state.md` — not yet built).
- Previous evaluation / `last_week_feedback` (from `docs/design/first-week-evaluator.md` — not yet built).

- **Deterministic calculation:** validation of availability fit, metric plausibility (zone conflicts), schema compliance, and safety rules (no-HR-prescription, strength-duration-only) — all `BUILT` today via `validation.py`, and locked project-wide as a shared harness both planner modes use (`docs/CLAUDE.md`: "Shared guards ... live in a COMMON harness both planners use — never re-implemented per planner").
- **LLM judgment:** concrete session design — which specific sessions, what intensity character, what wording — within the deterministic guardrails. This is the layer's actual job, per the project's core principle: "the LLM's job is narrow: session DESIGN and language/interpretation" (`docs/CLAUDE.md`).
- **Open, not decided here:** how exactly `last_week_feedback` and current phase get woven into the FIRST_WEEK-style scoped `prompt_context` for ongoing weeks — this depends on the field list for `last_week_feedback`, itself an open decision (`docs/decisions/open.md`).

---

## Summary table

| | Layer 1: General | Layer 2: Stage | Layer 3: Weekly |
|---|---|---|---|
| Status | `PROPOSED` | `PROPOSED` | `BUILT` |
| Runs | Once per goal / re-plan | Once per phase | Every week |
| Deterministic part | Phase dates (calendar math) | Progression rules within a phase | Validation, schema, safety guards |
| LLM part | Phase objective wording (maybe templated, not required) | None specified | Concrete session design |
| Output | Dated phases | Weekly load/focus/deload skeleton | Actual sessions |
| Depends on | Goal, event date, disciplines | General Planner's phases | Phase + stage intent + fitness state + last_week_feedback (target); today: constraints only |

A guarantee enforced in one planner layer must be enforced in all layers that share the same concern — this is already how Layer 3's shared validation harness works across FIRST_WEEK/ONGOING modes, and the same principle should extend to Layers 1-2 once built, per `docs/CLAUDE.md`'s core architecture principle.
