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
2. Approve E6/E7 evaluator tables — `OPEN DECISION`, blocks step 3. See
   [Open decisions](decisions/open.md).
3. First-week evaluator — `DESIGNED`, not implemented. See
   [First-week evaluator](briefs/backlog/first-week-evaluator.md).
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
| Settings-gated screenshot and TCX workout capture; pace/speed derivation; cycling power storage; reference HR zones; `/zones` | `BUILT` (screenshot defaults off; TCX defaults on) |
| Ongoing dated weekly-plan generation/validation | `BUILT` service capability, not production-wired |
| Dated-plan `compare_week()` and `weekly_plan_outcomes` upsert | `BUILT` internal capability, no production trigger |
| Explicit first-week links and evaluation | `DESIGNED`, not implemented |
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

Status: behavior `DESIGNED`; implementation not started.

The milestone is gated only by approval of the proposed E6 numerical tolerances
and E7 signal/coverage thresholds in `docs/decisions/open.md`. The executable work package is
[First-week evaluator](briefs/backlog/first-week-evaluator.md).

Implement in vertical slices:

1. **Stable planned-session references.** Add a code-generated UUID and define
   schema-v4 compatibility plus explicit preserve/replace rules for revisions.
   Never use array ordinal as identity or ask the LLM to create identifiers.
2. **Explicit one-to-one primary matching.** Add athlete-owned durable links,
   the approved correction lifecycle, candidate eligibility, and the minimal
   link/review UI. Unlinked plan entries become `MISSED`; eligible unlinked
   workouts become `EXTRA`.
3. **Objective evaluator evidence.** Expose stored power, speed, cadence,
   summary HR, sampled-HR quality, duration, distance, and pace with provenance.
   Actual RPE is not required; optional feel text is not interpreted. Missing HR
   or the relevant pace/power metric makes the comparison `NOT_COMPARABLE`.
4. **Per-session deterministic comparison.** Implement the direction-aware
   duration/distance/pace/power calculations, output comparison, intensity
   verdict, HR soft flags, and worked examples in the evaluator design.
5. **Weekly aggregation.** Keep completion, matched capability, and total
   actual-volume denominators separate; implement the versioned signal table,
   using the approved five-value enum, only after E7 thresholds are approved.
6. **Trigger and persistence.** Add the manual idempotent evaluate flow,
   immutable versioned outcome, and superseding correction behavior. Keep
   `last_week_feedback` downstream and leave the dated comparator unchanged.

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
