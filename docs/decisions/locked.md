# Locked Decisions

Final decisions — do not re-litigate. Each is settled. Status tags below (`BUILT`/`DESIGNED`/`PROPOSED`) indicate whether the decision is already true in running code, or is a target design not yet implemented — the decision itself is still locked either way.

## Planners

- Three-layer hierarchy: General Planner (phases) → Stage Planner (per-phase weekly skeleton) → Weekly Planner (actual sessions). `BUILT`: Weekly Planner only. `PROPOSED`: General and Stage Planner. See `docs/design/planner-architecture.md`.
- General planner cuts phases by CALENDAR (time-based), not CTL. One universal model (Base→Build→Peak→Taper) parameterized by disciplines + weeks + event type. `PROPOSED` — not yet implemented.
- Phase model is discipline-agnostic; only session content is sport-specific. `PROPOSED`.
- Stage skeleton commits load + focus + deload flag per week — NOT full workouts. `PROPOSED`.

## First-week planner (`BUILT`, verified against code)

- Probe/baseline week; goal context excluded; menu-mode (no scheduling, athlete places sessions).
- Zones resolved in code (FTP→power, race→pace, else RPE fallback).
- Baseline tiers (UNPREPARED/DEVELOPING/TRAINED/WELL_TRAINED) computed in code.
- Prescribes power + pace only; HR never prescribed.
- Strength = duration-only (schema-enforced).
- generate → validate → repair(≤2) → deterministic fallback.

## HR

- HR is NEVER prescribed (any planner). Prescribe pace/power, verify with HR. `BUILT` for both first-week and ongoing modes (shared validation harness). **Caveat:** the ongoing-mode half of this guard is implemented and tested in the working tree but is not yet committed — until it is, this line is `BUILT` only against uncommitted work.
- HR zones from age (Tanaka), treated as approximate. `BUILT` (`athlete_zones.py`), display/reference-only.

## Evaluator (first-week evaluator — `PROPOSED`, target design; see `docs/design/first-week-evaluator.md`)

- Matching = Option A (athlete explicitly links workout → planned session). **This is the target design for the first-week evaluator specifically.** It is not yet implemented, and it is a deliberately different mechanism from the ongoing planner's already-`BUILT` `compare_week()`, which matches by nearest same-discipline date (see "Open — reconciliation" in `docs/decisions/open.md`). Locking this decision fixes the first-week evaluator's approach; it does not retroactively change `compare_week()`.
- Missed sessions = discarded, never penalized. "Discarded" means excluded from performance-comparison math, not deleted from the record — the missed session stays visible for factual/completion reporting. `PROPOSED`.
- HR effort check = soft flag only. `PROPOSED` for the first-week evaluator (the general HR-never-prescribed rule above is already `BUILT`; the evaluator's soft-flag mechanism specifically is not).
- Judge by intent (intensity) first, volume second. `PROPOSED`.
- Evaluator = facts + suggested signal; planner decides. `PROPOSED`.
- overall_signal = deterministic rule table, not an LLM call. `PROPOSED`.

## Data model

- 4 roles: fitness state / plans / actuals / evaluations. Plans and actuals are `BUILT` (`weekly_training_plans`, `workouts` + discipline detail rows). Evaluations are `BUILT` for the ongoing planner only (`weekly_plan_outcomes`), `PROPOSED` for first-week. Fitness state is `PROPOSED` — see next line.
- Fitness snapshots = one row per week, append-only (history = progress trend). **Correction:** no such table (`athlete_fitness_snapshots` or otherwise) exists yet. This is the *recommended* structure (see `docs/design/fitness-state.md`), locked as the target shape, not a description of current persistence.
- "Current/last/overall" = reads, not separate tables. Applies once the above is built; there is nothing to read yet.

## Process

- Astra (UI/UX agent) works on its own branch, never merges to main, presentation-only.
- One agent writes to main.
