# Implementation Brief: First-Week Evaluator

Status: **implementation-ready, not executed.** This brief does not resolve the open product questions listed below — implementation should not start until those are answered. See `docs/design/first-week-evaluator.md` and `docs/design/fitness-state.md` for the full design this brief implements.

---

## Goal

Give the first-week planner a working evaluation loop: let the athlete link a logged workout to a first-week planned session, deterministically compare planned vs. actual, aggregate the week, persist the outcome, and start a fitness-state history — so a future planner (general planner, or the ongoing weekly planner in the meantime) has real `last_week_feedback` to read instead of nothing.

## Non-goals (explicitly out of scope for this milestone)

- General planner or stage planner (separate roadmap items, after this).
- CTL/TSS activation (Mode C) or any load-math-driven adaptation.
- Changing the ongoing planner's existing `compare_week()` matching mechanism — that stays as-is unless a future decision says otherwise (see open question below).
- Any UI/UX polish beyond what's needed to expose the linking step and the weekly summary (Astra's branch, not this brief).
- Vacation/recovery handling or phase re-cutting.

---

## Current implementation, as discovered from the repo (ground truth, verified 2026-09-05)

- First-week plans are generated and persisted (`WeeklyTrainingPlan`, `plan_jsonb`) with **no stable per-session identifier** — `PlanSession` has none, and the whole plan is one JSON blob.
- `Workout` rows (actuals) carry **no foreign key** to any plan or session — matching, whatever its mechanism, must be computed and stored independently of the `Workout` table's own schema, or `Workout` needs a new nullable link column (see persistence section).
- `compare_finished_week()` (`backend/app/services/weekly_planning/service.py`) already special-cases `FirstWeekPlan` by returning `None` — this is the exact point where first-week evaluation logic needs to be added.
- `compare_week()` (`backend/app/services/weekly_planning/comparison.py`) is a **working, different mechanism** for the ongoing planner: greedy nearest-same-discipline-date matching. It is not reused as-is for first-week (no dates to match against) but its module is a reasonable place for a sibling first-week comparison function, and its `TargetOutcome`/`SessionOutcome`/`WeekComparison`-style Pydantic result shapes are a reasonable pattern to mirror for consistency.
- `weekly_plan_outcomes` table + `WeeklyPlanOutcomeRepository` (`BUILT`) already provide one-row-per-athlete-per-week upsert persistence with a JSON payload column (`comparison_jsonb`) and a unique constraint on `(athlete_id, week_start)`. Whether first-week outcomes reuse this exact table is an open question (below) — but the *pattern* (JSON payload, upsert, one row per athlete-week) is proven and should be followed even if a new table is needed.
- No fitness-snapshot table exists anywhere. `athlete_self_reported_baselines` is mutable, one row per athlete, no history.
- `format_pace_min_sec()`, `athlete_zones.py`'s `ReferenceHeartRateZones`, and the zone-resolution formulas in `zones.py` are all reusable as-is for the comparison math — no new unit conventions should be introduced.

---

## Locked decisions this brief must respect

(Full text: `docs/decisions/locked.md`, Evaluator section.)

- Matching: athlete-explicit linking, not date/greedy inference.
- Missed sessions: discarded from comparison math, never penalized, never deleted from the record.
- HR effort check: soft flag only, never auto-adjusts zones.
- Judge by intent (intensity) first, volume second.
- Evaluator emits facts + suggested signal; it does not make the planning decision.
- `overall_signal` (or equivalent) computed by a deterministic rule table, not an LLM call.

## Remaining product questions (do not resolve without asking — see `docs/decisions/open.md`)

1. Evaluation trigger: manual "Evaluate week" button, automatic at week-end, or both?
2. How should completion rate treat an intentionally-cancelled (vs. simply missed) session?
3. Should linking happen immediately after screenshot import, or as a separate later step?
4. Exact `last_week_feedback` field list?
5. Is the first fitness snapshot created after evaluation, or seeded at onboarding?
6. Is the first-week matching decision (explicit linking) meant to stay permanently different from the ongoing planner's greedy-date matching, or should the ongoing planner eventually converge on explicit linking too?
7. Does the first-week outcome reuse `weekly_plan_outcomes`, or need a distinct table?

---

## Proposed architecture

New module, mirroring the existing `weekly_planning` package structure rather than inventing a new location:

- `backend/app/services/weekly_planning/first_week_evaluation.py` (new) — the deterministic comparison + aggregation functions, analogous to `comparison.py` but for first-week menus (no dates, explicit links instead).
- A new repository method or small new repository for reading/writing the athlete → workout → planned-session link (exact shape depends on question 7's resolution).
- `WeeklyPlanningService` gains the linking + evaluation entry points, replacing today's unconditional `None` return for `FirstWeekPlan` in `compare_finished_week()` — or a new sibling method, if triggering (question 1) ends up wanting a distinct manual entry point rather than reusing the existing automatic one.

## Persistence requirements (without assuming table shapes)

State that must exist somewhere, without prescribing exact tables:

- A durable link from one `Workout` row to one identified planned session, created by explicit athlete action, not inferred. Two plausible shapes: (a) a new nullable FK-like column on `Workout` (or a small join table) pointing at a new stable session identifier; (b) a link recorded on the outcome/comparison side instead, keyed by `(workout_id, session_id)` pairs in a JSON payload. Either can satisfy the locked decision; which one depends on how the stable session identifier (below) is implemented.
- A stable identifier for each planned session within a `WeeklyTrainingPlan`'s `plan_jsonb`. This likely means adding a `session_id` (UUID, assigned at generation time, before the plan is ever shown to the athlete) to `PlanSession` in the schema, then propagating it through generation, validation, and rendering. This is a schema change to `backend/app/schemas/weekly_plans.py` and is the first implementation step — nothing else here can be built without it.
- One persisted deterministic comparison result per athlete per evaluated first week (matched/missed/extra sessions, per-session target outcomes, weekly aggregate, suggested signal) — following the `weekly_plan_outcomes` upsert pattern, in that table or a sibling one (question 7).
- One persisted fitness-state row per athlete per evaluated week, append-only (per `docs/design/fitness-state.md`'s recommendation) — new table, exact columns deferred until `last_week_feedback`'s field list (question 4) is resolved, since the snapshot's fields are downstream of what the evaluator produces.
- A `last_week_feedback` value readable by a future planner — likely derived from the two persisted rows above at read time, rather than a third copy of the same data, but this is an implementation choice once question 4 is answered.

---

## TDD task order

Write tests first for each step; do not proceed to the next step until the current one's tests pass.

1. **Schema:** `PlanSession` gains a stable `session_id`. Test: generating a first-week plan produces sessions with unique, stable ids that survive a round-trip through validation and persistence (`plan_jsonb` serialize/deserialize).
2. **Linking:** a service method that records an athlete's explicit link between a `workout_id` and a `session_id`, rejecting a link to a session from a different athlete's plan or a nonexistent session id. Test: valid link succeeds; cross-athlete and invalid-session-id links are rejected; re-linking the same workout to a different session updates rather than duplicates.
3. **Per-session comparison:** pure function, given a linked (planned session, workout) pair, returns the deterministic `TargetOutcome`-style comparison per `docs/design/first-week-evaluator.md`'s metric list. Test each worked-example scenario from that document (aerobic-efficiency positive signal, `OVERCOOKED`, ambiguous-fatigue) as a fixture, plus a session with no comparable targets (e.g. RPE-fallback discipline with no numeric metric at all) to confirm it degrades to intent-only comparison rather than erroring.
4. **Missed/extra classification:** given a plan's full session set and the set of linked workouts, classify unlinked sessions `MISSED` and unlinked workouts `EXTRA`. Test: a session with a link is neither; a workout linked to nothing is `EXTRA`; a session with nothing linked to it is `MISSED`; both can be true simultaneously in the same week without conflict.
5. **Weekly aggregation:** given the full set of per-session comparisons plus missed/extra counts, compute the aggregate facts and the deterministic suggested signal (`ABSORBED_WELL`/`ON_TRACK`/`WATCH_EFFORT`/`BACK_OFF`). Test each signal's boundary condition explicitly (define the rule table as part of this step, in code and in a test that pins its behavior, not just in prose).
6. **Persistence:** the evaluation result upserts correctly (idempotent re-run for the same athlete-week produces the same row, not a duplicate), respects the chosen table/schema, and is retrievable by athlete + week.
7. **Fitness snapshot:** given a persisted evaluation result, append a new fitness-state row; test that this is truly append-only (re-running the same week's evaluation does not create a duplicate snapshot, but does not overwrite the prior week's row either) and that a multi-week history is readable in order.
8. **`last_week_feedback` contract:** a read function producing the agreed field list (once question 4 is resolved) from the two persisted rows above. Test the shape is stable and handles the "no prior week" case (a brand-new athlete) without erroring.
9. **Trigger wiring:** whichever mechanism question 1 resolves to (manual command, automatic check, or both) — test that triggering twice for the same week is safe (idempotent), and that triggering before the plan's week has actually elapsed is rejected or handled per the resolved decision.
10. **End-to-end:** a full first-week plan → screenshot import → link → evaluate → snapshot flow, exercised as an integration test through the same layers the Telegram bot would call.

## Acceptance criteria

- All TDD steps above pass, with tests committed alongside the code they test (per the project's "write tests first" convention).
- No change to the ongoing planner's `compare_week()` behavior — verified by re-running `test_weekly_planning_comparison.py` unchanged and green.
- No HR value is ever used as a match/mismatch pass-fail gate — only as a soft flag, verified by a test that asserts a HR-outside-zone session still produces a valid (non-erroring, non-auto-adjusting) comparison result.
- A missed session never appears in any ratio/average whose denominator implies it should count as zero performance — verified by a test asserting the specific denominator used (per whatever question 2's resolution defines).
- `generation_source`-style observability: the evaluation result records enough to distinguish "evaluated cleanly" from any degraded/fallback path, consistent with the project's observability principle, if any fallback path exists at this layer (e.g. missing workout detail data).

## Documentation updates required after implementation

- `docs/CLAUDE.md` "Current implementation status" section: move the newly-built pieces from NOT YET BUILT to BUILT, with the same code-citation style used elsewhere in that file.
- `docs/design/first-week-evaluator.md`: update the per-stage status table (all `PROPOSED` rows that are now `BUILT`), and replace the "illustrative numbers" caveat on the worked examples with a note confirming they were validated against real test fixtures, or replace the numbers with the actual fixture values used.
- `docs/design/fitness-state.md`: replace the recommendation section with a description of what was actually built, once implemented.
- `docs/decisions/open.md`: remove resolved questions (or move them to `locked.md` with their resolution recorded), keep unresolved ones.
- `docs/roadmap.md`: move completed steps (1-7 in the current numbering) to "Done."

## Deployment / live-verification checklist

Per the project's "deploy ≠ pass tests" convention:

- [ ] `docker compose up -d --build` after merge — passing tests is not sufficient confirmation.
- [ ] Manually generate a first-week plan for a test athlete, log a workout via screenshot, link it, trigger evaluation (whatever mechanism was built), and confirm a real weekly outcome + fitness snapshot row exist in the live database.
- [ ] Confirm the ongoing planner's existing weekly comparison flow is unaffected (`/`-command or whatever surface triggers `compare_finished_week()` for an ongoing-mode athlete still behaves as before).
- [ ] Confirm no HR value appears anywhere as a prescribed target in the first-week evaluator's output (spot-check the persisted `comparison_jsonb`-equivalent payload directly, not just the rendered message).
- [ ] Confirm a missed session is visible in the athlete-facing weekly summary (not silently dropped) even though it's excluded from performance averages.
