# Roadmap — Build Order

Status was verified against `main` on 2026-09-07. Code is the source of truth.
`BUILT` does not imply production reachability; dormant capabilities are called
out explicitly. The documentation map is in `docs/README.md`.

## Build order (start here)

The simple version. Everything below this section is supporting detail, gates,
and rules for each step — read it before executing a step, not before deciding
what step is next.

1. ~~Fix repair regression~~ — **done** (2026-09-07). The first-week repair
   loop no longer raises on an ordinary, non-HR repair. See
   [Locked decisions](decisions/locked.md).
2. ~~Approve E7-E11~~ — **done** (2026-09-10). All five are `DECIDED`. See
   [Locked decisions](decisions/locked.md), "Signal scoring, THRIVING, and
   volume status," for the full mechanism. Calibration numbers (density cut
   lines and similar) are `PROVISIONAL`: implement them as named, isolated
   constants, not inline literals, so they can be recalibrated later.
   Nothing in step 3 or 4 below is still blocked by E7-E11.
3. First-week evaluator — deterministic core (identity, linking,
   evidence, per-session comparison, weekly aggregation/signal, immutable
   outcome persistence) `BUILT, DORMANT` (2026-09-10); Telegram
   link/evaluate/review UI, integration test, and regression test remain
   `DESIGNED`. See [First-week evaluator](briefs/backlog/first-week-evaluator.md).
4. Fitness history — `PROPOSED`, not implemented. See
   [Fitness state](briefs/backlog/fitness-state.md).
5. General Planner phases — `DESIGNED`, not implemented. See
   [General Planner phase foundation](briefs/backlog/general-planner-phase-foundation.md).
6. Wire `OngoingWeeklyPlanner` — `BUILT` as a service, not production-wired.
7. Stage Planner — `DESIGNED`, not implemented.
8. CTL/TSS and advanced adaptation — later; `PROPOSED`. See
   [Training-load model](design/load-model.md).

## Current production boundary

| Capability | Status |
|---|---|
| First-week menu generation, validation/repair/fallback, persistence, and Telegram rendering | `BUILT` and production-wired |
| Self-reported baseline, first-week tiers, and pace/power/RPE zone resolution | `BUILT` |
| Settings-gated screenshot and TCX workout capture; pace/speed derivation; cycling power storage; reference HR zones; `/zones` | `BUILT` (screenshot defaults on; TCX optional, defaults off) |
| Ongoing dated weekly-plan generation/validation | `BUILT` service capability, not production-wired |
| Dated-plan `compare_week()`/`compare_finished_week()` | Removed 2026-09-08 — superseded by the first-week evaluator's athlete-explicit matching; `weekly_plan_outcomes` remains in the schema but is unused |
| Explicit first-week links and evaluation | `BUILT, DORMANT` (2026-09-10) — deterministic core exists and is tested; no Telegram UI calls it |
| Fitness state/history | `PROPOSED`; storage choice deferred to its milestone |
| General Planner and Stage Planner | `DESIGNED`, not implemented |

The production athlete journey currently stops after first-week plan generation
and workout logging. There is no session link, first-week evaluation,
fitness-state update, feedback handoff, or automatic transition to ongoing
planning.

The previously documented “Astra branch” is not present among current local or
remote branches and is not evidence of completion. The fixes once described as
“not yet committed” are in commit `5d16036`; that wording was stale.

## Priority 0 — HR prescription invariant (done)

Status: `BUILT` and covered for both weekly prescription paths.

Both model-facing weekly prescription schemas exclude HR intensity,
`average_hr_bpm`, and `hr_range_bpm`. Wider persisted types retain legacy-load
compatibility, and the existing validator remains defense in depth. Completed
workout HR remains valid evidence.

That narrowing had a follow-on defect: rebuilding the narrow prescription
after first-week repair round-tripped through the wide, persisted session
type, which dumps `average_hr_bpm`/`hr_range_bpm` even when unset. The narrow
schema forbids those keys outright, so any ordinary (non-HR) repair on a
running/cycling/swimming session raised an unhandled error instead of
producing `model_repaired` or a safe `fallback`. Fixed 2026-09-07: the
reconstruction now removes those legacy-only keys explicitly, and
`HEART_RATE_PRESCRIBED` was added to first-week repair's own safety-clearing
codes so a legacy HR-prescribed session also repairs clean. Regression-tested
and verified live against the rebuilt bot image.

## Milestone 1 — first-week evaluator

Status: deterministic core `BUILT, DORMANT` (2026-09-10); delivery (Telegram
UI, integration test, regression test) not started.

The milestone was gated by approval of the E7-E11 decisions (E6 is already
locked). All five are resolved as of 2026-09-10; see `docs/decisions/locked.md`,
"Signal scoring, THRIVING, and volume status." Calibration numbers (density
cut lines, and formerly E7's numbers) remain provisional, ship as named
constants. The executable work package is
[First-week evaluator](briefs/backlog/first-week-evaluator.md).

Implemented in vertical slices:

1. **Stable planned-session references.** `BUILT` — `PlanSession.id` is a
   code-generated UUID (`Field(default_factory=uuid.uuid4)`), never authored
   by the LLM-facing prescription types. First-week plans write at schema
   v5; a legacy v4 plan still loads (the field defaults), but is not
   linkable (no durable identity).
2. **Explicit one-to-one primary matching.** `BUILT, DORMANT` — the
   `planned_session_links` table and `LinkingService` enforce ownership,
   plan-revision eligibility, and the athlete-local week boundary; relinking
   before evaluation updates the row in place. No Telegram link/review UI
   exists yet. Unlinked plan entries classify as `MISSED`
   (`classification.py`). As of the 2026-09-08 revision, every workout must
   link to a planned session before it can be confirmed; the deterministic
   predicate for that block (`has_linkable_plan_for_workout`) exists but is
   not wired into the capture/confirm flow.
3. **Objective evaluator evidence.** `BUILT` — `EvaluatorWorkoutEvidence`
   exposes stored power, speed, summary HR, sampled-HR quality (via direct
   queries, not the lazy-loaded ORM relationships), duration, distance, and
   pace with provenance. Actual RPE is not required; optional feel text is
   not interpreted. Missing HR or the relevant pace/power metric makes the
   comparison `NOT_COMPARABLE`.
4. **Per-session deterministic comparison.** `BUILT` — `compare_session()`
   implements the direction-aware duration/distance/pace/power calculations,
   output comparison, intensity verdict, the additive point-value/
   variance-flag formula, the ratio-gated positive-efficiency-evidence
   check, and HR soft flags (E8, E9, and E11 applied, see
   `decisions/locked.md`).
5. **Weekly aggregation.** `BUILT` — `aggregate_week()` keeps completion,
   matched capability, and total actual-volume denominators separate;
   implements the additive scoring formula, the variance-flag note, the
   five-condition `THRIVING` gate, the weekly volume range status (per
   discipline and blended), and the per-discipline efficiency factor, using
   the approved five-value signal enum. Calibration numbers are provisional,
   shipped as named constants (`app/services/weekly_evaluation/constants.py`).
6. **Trigger and persistence.** Persistence half `BUILT`: immutable,
   versioned `first_week_evaluation_outcomes` rows
   (`FirstWeekEvaluationOutcome`/`OutcomeService`), idempotent replay, and
   superseding-revision correction behavior. The manual trigger (an
   "Evaluate week" action in the bot) does not exist yet. `last_week_feedback`
   remains downstream, unbuilt; the dated comparator this used to reference
   no longer exists in code (removed 2026-09-08).

Remaining for this milestone: the Telegram link/evaluate/review UI (wiring
`LinkingService`, a manual evaluate trigger, and `OutcomeService` into
`backend/app/bot/`), wiring `has_linkable_plan_for_workout` into the capture
confirm flow, an integration test against real PostgreSQL, and a regression
test proving no route reaches `OngoingWeeklyPlanner` by accident.

Completion of Milestone 1 does not itself create fitness history or switch the
production planner to ongoing mode.

## Milestone 2 — fitness state and history

Status: separation and behavior principles `DESIGNED`; storage choice and exact
rules `OPEN DECISION`; no implementation.

Use [Fitness-state foundation](briefs/backlog/fitness-state.md) after approving
storage, week-zero seeding, correction, confidence/tier, comparability, and
feedback decisions. The leading `PROPOSED` storage is hybrid: versioned weekly
history plus a current derived view/cache.

Implement:

1. versioned input from accepted weekly evaluation facts;
2. explicit self-reported versus observed provenance and confidence;
3. weekly volume progression and confidence updates from observed evidence;
4. the approved minimum multi-week pace/power/HR efficiency comparisons with
   sample count, quality, and confounder flags;
5. current, previous, and history reads with correction/supersession; and
6. the bounded planner-feedback projection.

Do not require CTL/TSS. Do not infer missing environmental, recovery, or medical
context, and do not convert one HR mismatch into a fitness change.

## Milestone 3 — General Planner phase foundation

Status: responsibility and phase order `DESIGNED`; allocation rules
`OPEN DECISION`; no implementation.

Use [General Planner phase foundation](briefs/backlog/general-planner-phase-foundation.md)
after approving phase allocation, minimum lengths, date convention,
infeasibility, transition, revision, and re-plan behavior.

Implement deterministic boundaries using the approved phase vocabulary (the
candidate is Base → Build → Specific/Peak → Taper) from the current
catalog-backed primary goal, optional supporting goal, event/target fields,
timezone, and approved rule table. Persist a versioned, reproducible calendar
and expose the phase effective on an athlete-local date. Do not create sessions
or a Stage skeleton in this slice.

## Milestone 4 — wire goal-directed ongoing planning

Status: ongoing planner service `BUILT`, production transition `OPEN DECISION`.

After a reviewed evaluation, fitness-state handoff, and current phase are
available:

1. approve the deferred transition/no-feedback policy in the planner design;
2. extend the ongoing input contract with current phase and bounded fitness/
   evaluation feedback;
3. keep deterministic scheduling, validation, safety, persistence, repair, and
   fallback around the existing Weekly Planner LLM role; and
4. production-wire the transition with end-to-end Telegram and regression
   coverage.

Do not mark the ongoing loop `BUILT` and production-wired merely because
`OngoingWeeklyPlanner` already exists.

## Milestone 5 — Stage Planner skeleton

Status: responsibility `DESIGNED`; skeleton fields and budgets
`PROPOSED`/`OPEN DECISION`; no implementation.

Implement the lightweight week-by-week phase skeleton only after ongoing
planning can consume a phase and feedback. Start with deterministic qualitative
focus, progression direction, and recovery/deload intent. It must not contain
complete workouts. Approve lost-week and escalation budgets before making the
skeleton adaptive.

## Milestone 6 — training-load modes

Status: qualitative Mode A `DESIGNED`; computed Mode B and CTL-driven Mode C
`PROPOSED`; formulas and activation gates `OPEN DECISION`.

Follow [Training-load model](design/load-model.md):

1. keep `MODE_A_QUALITATIVE` fully functional;
2. introduce versioned `MODE_B_EMERGING` observations only after approved
   discipline-specific inputs/formulas and quality gates;
3. validate candidate load against accumulated athlete history; and
4. permit `MODE_C_CTL_DRIVEN` planning influence only after approved continuity,
   coverage, threshold-freshness, normalization, and safe-ramp gates.

CTL can improve load precision; it does not choose phase names, make an
infeasible goal feasible, or justify compressing missed load.

Richer long-term trend interpretation may be added as evidence accumulates, but
the minimum weekly-volume and comparable efficiency trends belong in Milestone
2 before planner feedback is wired. Later additions must preserve discipline/
source separation and make treadmill/outdoor, indoor/outdoor cycling, elevation,
temperature, device, and missing recovery context visible rather than treating
them as equivalent.

## Adaptation escalation after the foundations exist

Status: escalation principle `DESIGNED`; thresholds `OPEN DECISION`.

- One missed session stays at the Weekly Planner; never compress it into later
  days automatically.
- A lost week may revise/supersede Stage intent after an approved threshold.
- An extended disruption may re-cut the General phase calendar after athlete
  confirmation.
- If no layer can absorb the change safely, explain the infeasibility and offer
  a target/date change rather than fabricating a plan.

## Deferred

- Vacation/recovery workflows and autonomous zone recalculation.
- Environment-based metric selection such as indoor cycling power versus
  outdoor speed/HR.
- Broad database cleanup, including Apple Health remnants.
- Any Mini App beyond the minimal evaluator link/effort/review experience.
- Archiving or deleting historical `docs/CLAUDE.md`; it remains a separately
  approved cleanup candidate.
- A logging path for athletes without a heart-rate-capable device, needed once
  workout confirmation requires heart rate (see `decisions/locked.md`,
  "Workout capture"). Without one, these athletes cannot confirm any logged
  workout under the new rule.
