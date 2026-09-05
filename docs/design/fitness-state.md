# Fitness-State History — Design

Status: **PROPOSED design, not yet implemented.**

## Verified current state (code, not docs)

There is no fitness-snapshot table today. What exists:

- `athlete_self_reported_baselines` (**BUILT**, `backend/app/db/models.py:719`) — one row per athlete (`UniqueConstraint("athlete_id")`), holding `baseline_jsonb` + a `goal_signature`. It is explicitly mutable: the model's own docstring says "can be replaced after a goal change." No history is kept — updating it overwrites the prior value.
- `backend/app/services/fitness/calculator.py` + `fitness/service.py` (**BUILT**) — compute fitness *evidence* on demand from raw `Workout` rows (recent session/active-day counts per discipline, HR-quality-filtered samples). This is a read-time computation over actuals, not a persisted fitness state.
- `WeeklyPlanOutcome` / `weekly_plan_outcomes` (**BUILT**, migration `0050`) — persists one row per athlete per completed week, but its `comparison_jsonb` payload is a plan-vs-actual comparison (via `compare_week()`), not a fitness-state summary. It's built for the ongoing planner today and doesn't run for first-week plans.

An earlier draft of `docs/CLAUDE.md` referred to an `athlete_fitness_snapshots` table as if it already existed and was populated weekly. That was incorrect — it does not exist in the schema or any migration. This document treats fitness-state history as something to design, not something to describe.

---

## The two structures under consideration

### Option 1 — a single mutable fitness-state row

One row per athlete, updated in place each time new evidence arrives (e.g. after each evaluated week).

Pros: simplest possible schema; trivial to read "current fitness"; matches the existing pattern already used for `athlete_self_reported_baselines`.

Cons: no history. Cannot answer "how did this change over the last 4 weeks," "same pace at lower HR than 3 weeks ago," or any trend question — every trend question in the goal list below requires comparing at least two points in time, which a mutable row cannot provide once overwritten. This also doesn't fit the project's own working convention that captured data should be preserved (`docs/CLAUDE.md`: "migrations are append-only for captured data").

### Option 2 — append-only weekly fitness snapshots

One immutable row per athlete per week, written once (after that week's evaluation) and never updated afterward. History = the full sequence of rows for that athlete, ordered by week.

Pros: supports every trend question directly (read the last N rows, compare any two). Matches the project's existing append-only convention (`weekly_training_plans` is itself immutable-per-revision; `weekly_plan_outcomes` is one row per athlete per week, not updated in place except via explicit upsert for re-computation of the *same* week). Also matches what `docs/CLAUDE.md`'s original data-model description was reaching for, even though the table it named doesn't exist yet.

Cons: more rows over time (bounded — one per athlete per week, not per workout); "current fitness" requires a most-recent-row read rather than a flat lookup, though that's a trivial indexed query (`athlete_id`, `week_start DESC` limit 1 — the same pattern `WeeklyPlanOutcomeRepository.get()` already uses for a different table).

### Recommendation

**Option 2 (append-only weekly snapshots).** Every goal below needs history, not just a current value, and the project already has this pattern in two other tables. A mutable single row would need to be rebuilt into an append-only structure the moment the first trend question ("progress over several weeks") mattered — which is immediately, per the goals list. There's no version of this feature that stays simple under Option 1.

This recommendation does not specify exact columns or a table name — that's implementation work per the brief in `docs/briefs/backlog/first-week-evaluator.md`. It specifies the shape (append-only, one row per athlete per evaluated week) and defers the rest.

---

## What must be persisted, regardless of exact schema

Derived from the stated goals, not invented beyond them:

- Enough to answer "what is current fitness" — the most recent row read alone must be self-sufficient, without needing to replay history.
- Enough to compare with last week specifically — meaning each row must be identifiable by week and comparable field-by-field to the immediately preceding row.
- Enough to show progress over several weeks — meaning the same fields must be present and comparably-shaped across all rows (no schema drift between weeks that would break a multi-week read).
- Enough to detect "same pace/power at a lower HR" and "higher pace/power at a similar HR" — meaning each row needs paired (performance metric, HR context) values per discipline, not performance and HR stored in a way that can't be correlated back to the same session or week.
- Enough to support later CTL/TSS activation (Mode C) without a schema migration at that time — meaning whatever raw inputs CTL/TSS math will eventually need (some measure of load per week, at minimum) should already be captured in the row shape from week one, even while the math itself stays inactive per the project's confidence-tiered load rule. This is a "don't paint yourself into a corner" requirement, not a request to build CTL now — CTL activation itself remains explicitly out of scope for this milestone.

Whether this requires a new table, or can extend `weekly_plan_outcomes` (which already has one-row-per-athlete-per-week shape), is left to implementation planning — see `docs/briefs/backlog/first-week-evaluator.md`, which lists it as a persistence question rather than assuming an answer here.

---

## Open questions this document depends on

- Should the first fitness snapshot be created after the first-week evaluation, or seeded at onboarding (from the self-reported baseline) and then updated by evaluation? — see `docs/decisions/open.md`, question 5.
- Exact field list — deferred to implementation planning once the evaluator's per-session and weekly-aggregate outputs (`docs/design/first-week-evaluator.md`) are finalized, since the snapshot's inputs are that document's outputs.
