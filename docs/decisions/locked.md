# Locked Decisions

These decisions are settled. Their status tag says whether code currently
enforces them; `DESIGNED` is a locked target, not running behavior. Status terms
are defined in `docs/README.md`.

## Planner hierarchy

- `DESIGNED` — three layers: General Planner (dated phases), Stage Planner
  (per-phase weekly skeleton), and Weekly Planner (complete sessions).
- `BUILT` — the Weekly Planner service has first-week and ongoing modes.
  First-week mode is production-wired; ongoing mode is not.
- `DESIGNED` — the General Planner cuts ordered, dated phases by calendar time,
  not CTL. The candidate Base → Build → Specific/Peak → Taper vocabulary and
  exact allocation remain deferred product work in the planner design.
- `DESIGNED` — the phase model is discipline-agnostic; sport-specific content
  belongs in weekly sessions. It supports running, cycling, swimming, triathlon,
  and optional supporting strength without assuming every athlete is a
  triathlete.
- `DESIGNED` — the Stage Planner stores load/progression intent, focus, and a
  deload/recovery flag per week, not complete workouts.
- `DESIGNED` — concrete session design is the Weekly Planner's LLM role. Code
  owns calendar placement, validation, safety, persistence, repair, and fallback.
- `DESIGNED` — adaptation reacts at the lowest layer that can absorb a
  disruption safely: one session at Weekly, a lost week at Stage, and extended
  disruption at General. No layer compresses missed load unsafely.
- `DESIGNED` — if the goal is infeasible inside safe/available time, the system
  explains why and proposes changing the target or date rather than fabricating
  a plan.

## First-week planner

- `BUILT` — a probe/familiarization menu with no scheduled session dates; the
  athlete places sessions.
- `BUILT` — event/goal context is excluded from the first-week prompt.
- `BUILT` — code resolves FTP to cycling power, race/400 m evidence to pace,
  and otherwise uses RPE fallback.
- `BUILT` — code computes `UNPREPARED`, `DEVELOPING`, `TRAINED`, and
  `WELL_TRAINED` tiers.
- `BUILT` — strength targets are duration-only. HR cannot be prescribed through
  either weekly model-facing schema: prescription intensity excludes the HR
  metric and prescription targets exclude `average_hr_bpm` and `hr_range_bpm`.
  The wider persisted types remain compatible with legacy plans, and shared
  validation still rejects legacy HR prescriptions.
- `BUILT` — generation uses validate, bounded repair (at most two model repair
  calls), then deterministic fallback with generation provenance. Code-level
  repair reconstruction is crash-safe for every discipline: an ordinary,
  non-HR repair on a running/cycling/swimming session no longer raises an
  unhandled error (fixed 2026-09-07; regression-tested).

## HR

- `BUILT` — neither weekly mode may prescribe HR. Both model-facing prescription
  boundaries exclude `HEART_RATE_BPM`, `average_hr_bpm`, and `hr_range_bpm`.
  Wider persisted plan types retain those fields only for legacy loading and the
  shared validator rejects legacy HR intensity or target prescriptions.
- `BUILT` — age-estimated Tanaka HR zones are display/reference-only and carry
  an approximation caveat.
- `DESIGNED` — the first-week evaluator may use HR only as a soft effort flag.
  One mismatch never automatically changes zones or declares lost fitness.

## Workout capture

- `DESIGNED`, not yet implemented (2026-09-08). Confirming a screenshot-captured
  workout now requires average and max heart rate. The existing optional
  "Add heart rate" prompt becomes a hard requirement: the confirm action is
  blocked until both values are present, replacing the current behavior where
  an athlete may confirm without them.
- This decision does not yet address athletes without a heart-rate-capable
  device; they would be unable to confirm any workout under this rule until a
  fallback path is designed. Tracked in `roadmap.md`'s Deferred section.

## Elevation adjustment (running and cycling)

Status: `DECIDED` for the gating rule and mechanism (2026-09-09); `PROVISIONAL`
for the specific coefficients below, which need calibration against real
ride/run data. Ship every coefficient as a named, isolated constant,
commented as provisional, same as the E7 scoring numbers.

- Gating uses the workout's own recorded type (`RunningType`, `CyclingType`),
  never inferred from an elevation reading or free text. Adjustment runs for
  running types `OUTDOOR` and `TRAIL`, and for cycling types `ROAD`,
  `GRAVEL`, and `MTB` only when power is unavailable for that session (power
  remains primary whenever present). Adjustment never runs for running types
  `TRACK` and `TREADMILL`, or cycling types `STATIONARY` and `OTHER`,
  regardless of what elevation the workout reports.
- Stationary cycling keeps duration and power as its pair; it does not adopt
  distance-and-pace comparison, since an indoor trainer's distance is
  computed from power and speed, not independently measured.
- Treadmill incline is not currently captured anywhere in the data model, so
  an inclined treadmill session is treated as flat. Known limitation, not
  solved by this decision; a future fix if incline training becomes common.
- Formula: for a gated workout with both `elevation_gain_meters` and
  `elevation_loss_meters` present, `climb_grade_percent =
  elevation_gain_meters / distance_meters * 100` and `descent_grade_percent =
  elevation_loss_meters / distance_meters * 100`. Running:
  `elevation_adjusted_pace_seconds_per_km = actual_pace_seconds_per_km *
  (1 - CLIMB_PACE_CREDIT * climb_grade_percent + DESCENT_PACE_PENALTY *
  descent_grade_percent)`. Cycling (power-fallback only):
  `elevation_adjusted_speed_kph = actual_speed_kph * (1 + CLIMB_SPEED_CREDIT *
  climb_grade_percent - DESCENT_SPEED_PENALTY * descent_grade_percent)`.
- Illustrative starting coefficients, not yet calibrated: `CLIMB_PACE_CREDIT
  = 0.03`, `DESCENT_PACE_PENALTY = 0.01` for running; `CLIMB_SPEED_CREDIT =
  0.05`, `DESCENT_SPEED_PENALTY = 0.02` for cycling. This is a linear
  approximation and likely understates cost at very steep grades; a cap or a
  non-linear curve may be needed later, not a v1 blocker since typical
  training routes are far more moderate.
- Sanity guard: if the computed adjustment would move pace or speed by more
  than `MAX_ELEVATION_ADJUSTMENT_PERCENT` (illustrative: 25%), treat the
  elevation reading as unreliable, emit a data-quality flag, and fall back
  to the unadjusted value rather than applying an implausible correction.
- Missing `elevation_gain_meters` or `elevation_loss_meters` on a gated
  workout never becomes zero. Fall back to the unadjusted actual pace or
  speed, attach a data-quality flag noting the adjustment could not be
  applied, and do not mark the session `NOT_COMPARABLE` for this reason
  alone; raw pace/speed is still usable.
- Both the raw and the elevation-adjusted value are stored. The adjusted
  value feeds the output-comparison flag and the ratio check described in
  `docs/decisions/open.md`; the raw value remains visible as its own fact
  and is never overwritten.

## First-week evaluator

- `DESIGNED` — matching is athlete-explicit. Date or discipline may organize
  candidates, but cannot create the final link automatically.
- `DESIGNED` — every planned session receives a code-generated UUID. Array
  ordinal is never permanent identity. A plan revision preserves a session UUID
  only when it represents the same intended session; materially replaced
  sessions receive new UUIDs, and the revision records replacement provenance.
- `DESIGNED` — in v1, a workout links to at most one planned session and each
  planned session has at most one primary workout used by evaluation.
- `DESIGNED` — links are athlete-confirmed. They may be corrected before
  evaluation; correction afterward creates a superseding evaluation revision.
  Secondary/multi-workout matching is deferred.
- `DESIGNED` — eligible workouts fall inside the athlete-local Monday–Sunday
  plan week. Duplicate or unresolved workouts are excluded until resolved.
- `DESIGNED` (revised 2026-09-08) — in v1, every logged workout must be
  linked to a planned session before it can be confirmed; there is no
  unlinked `EXTRA` state. A planned session with no linked workout is
  `MISSED`. The capture flow blocks confirming a workout with no planned
  session available to link it to (see "Workout capture"). Allowing a
  genuinely unplanned workout to be logged on its own is deferred to the
  ongoing-planner milestone, not part of v1.
- `DESIGNED` — `CANCELLED_AGREED` is reserved for a future confirmed plan-change
  flow and is never inferred from a missing workout.
- `DESIGNED` — missed sessions stay visible in completion reporting and are
  excluded from physiological/capability math. “Discarded” never means deleted.
- `DESIGNED` — adherence is `MATCHED / (MATCHED + MISSED)`. Capability uses
  matched sessions only. Uncapped duration ratios and all eligible actual volume
  are stored separately.
- `DESIGNED` — v1 does not require actual RPE. It uses captured duration and the
  discipline's objective pace/power plus average/max HR when present; distance
  is factual and speed/cadence are contextual. Missing HR or the relevant
  pace/power value makes that comparison `NOT_COMPARABLE`.
- `DESIGNED` — optional short feel text may be stored but is never required and
  never enters planner prompts automatically.
- `DESIGNED` — evaluation is a manual, idempotent action after link review.
  Later corrections create superseding immutable evaluation revisions.
- `DESIGNED` — judge intensity intent first and extra volume second.
- `DESIGNED` — the evaluator emits facts, quality flags, and a deterministic
  suggested signal; a planner makes the later planning choice.
- `DESIGNED` — no LLM call computes evaluation or the suggested signal.
- `DESIGNED` — metric selection is typed and provenance-bearing: running uses
  pace plus HR; stationary cycling uses power plus HR; swimming uses pace plus
  HR when reliable; duration is always reported; speed/cadence are contextual.
  Intent comes only from structured session fields, never prose.
- `DESIGNED` — age-estimated HR zones produce soft flags only and never
  automatically modify zones or fitness.
- `DESIGNED` — signal vocabulary is `ABSORBED_WELL`, `ON_TRACK`,
  `WATCH_EFFORT`, `BACK_OFF`, and `INSUFFICIENT_EVIDENCE`. Signals are
  deterministic and versioned. V1 does not interpret pain/safety from free text.
- `DESIGNED` — evaluation records are immutable and versioned, containing
  athlete/plan references, plan kind/revision, evaluation-rule version, evidence
  provenance, per-session insights, weekly aggregate, and supersession reference.
  They do not reuse the removed dated comparator's `WeekComparison` shape.
- `DESIGNED` — E6 numerical metric tolerances are deterministic and versioned.
  Duration is within ±10% of the planned value or range, with no fixed minimum.
  Distance is within ±5% with minimum allowances of 250 m for running, 1 km for
  cycling, and 50 m for swimming; it is evaluated only for an explicit structured
  target. Running pace is within ±5% of each range boundary with a 10 s/km
  minimum; stationary-cycling power is within ±5% with a 10 W minimum; and
  swimming pace is within ±5% with a 3 s/100 m minimum when reliable duration
  and distance are available.
- `DESIGNED` — for a scalar target `T`, metrics with a fixed minimum are within
  `T ± max(percent × T, minimum)`; duration is within `T ± (10% × T)`. For a
  range `[L, U]`, lower and upper boundaries are expanded independently. Raw
  values and signed deltas remain available for every verdict.
- `DESIGNED` — planned `session.intensity.rpe_range` selects the evaluator's
  approximate HR reference band: an entire range in 1–4 selects easy, 5–6
  selects moderate, and 7–10 selects hard. A range crossing those boundaries is
  `NOT_COMPARABLE` for HR. Purpose, execution, guidance, titles, and notes are
  never parsed.
- `DESIGNED` — a stored average-HR summary takes precedence. Reliable samples
  covering at least 80% of selected workout duration may supply an average when
  no summary is available. If a stored and sampled average differ by more than
  10 bpm, emit `SOURCE_CONFLICT` and make the HR verdict `NOT_COMPARABLE`. The
  selected reference-HR band has a ±5 bpm allowance and remains a soft effort
  flag only.

## Prescribed-range tolerance model (running/swim pace, cycling power, distance/volume)

Status: `DECIDED` for the model and scope (2026-09-09); `PROVISIONAL` for the
specific minimum-width numbers below, pending calibration against real
athlete data, same as the E7 scoring numbers and the elevation coefficients.

This supersedes the E6 boundary-expansion tolerance above for four metrics
only: running pace, swim pace, cycling power, and distance/volume for
running, swimming, and cycling when a session has an explicit distance
target. For these, the LLM prescribes the target as a range, and the range's
own boundaries are the flagging boundaries directly. No percent-or-minimum
tolerance is added on top of the prescribed range; the range is the margin.

- Running pace and swim pace already prescribe a range
  (`session.intensity.target_range`); this decision only removes the E6
  boundary expansion that used to widen it further, it does not change how
  the range itself is authored.
- Cycling power already prescribes a range the same way; the E6 boundary
  expansion is removed for it too.
- Distance/volume changes shape: for running, swimming, and cycling, an
  explicit distance target moves from an optional scalar value to a
  prescribed range (minimum and maximum). It is still evaluated only when a
  session has an explicit structured distance target, exactly as E6 already
  specified.
- A validator enforces a minimum width on every prescribed range covered by
  this decision, so the LLM cannot prescribe a range too narrow to be
  meaningful. If a prescribed range is narrower than the floor, the
  validator widens it to the floor before it is stored. The floor numbers
  reuse the E6 minimums, now applied as a total-width floor instead of a
  per-boundary expansion: running pace 10 s/km, swim pace 3 s/100m, cycling
  power 10 W, running distance 250 m, swim distance 50 m, cycling distance
  1 km.

Not covered by this decision; these three keep the E6 model above unchanged:

- Strength: duration-only, no pace/power/distance target exists on it.
- RPE-fallback sessions: no pace or power to build a range from.
- Duration, for running, swimming, and cycling: no longer independently
  prescribed or flagged at all, except where noted below. See "Duration
  derivation (continuous and structured sessions)" immediately below for the
  resolved model.

This is a schema and validator change, not only a docs change: distance
targets for running, swimming, and cycling need to become ranges instead of
optional scalars, and a new minimum-range-width validator needs to exist.
Code implementation is a separate pass; this decision only locks the model,
the scope, and the floor numbers.

## Duration derivation (continuous and structured sessions)

Status: `DECIDED` for the model (2026-09-09). This is a schema and evaluator
change, not only a docs change; code implementation is a separate pass.

Duration is no longer independently prescribed or flagged for running,
swimming, or cycling, except in the unaffected exceptions below. It is
computed by code from what is actually prescribed, never authored or judged
on its own. This resolves the "still-open" duration question left in
"Prescribed-range tolerance model" above.

- **Continuous single-effort sessions** (no internal structure: an easy run,
  a steady ride, a straight-through swim). Distance and pace/power are
  prescribed as ranges, per "Prescribed-range tolerance model" above.
  Duration is derived from the range endpoints (`duration = distance /
  pace`, or the cycling equivalent), producing a derived duration range. It
  is never independently prescribed, never a target, never flagged on its
  own.
- **Structured sessions** (intervals, or any session with distinct work and
  rest blocks). The LLM designs the session as an ordered list of segments.
  A work segment prescribes a duration and a pace-or-power range (running
  and swimming use pace, cycling uses power). A rest segment prescribes
  only a duration, no pace or power requirement. The LLM has full latitude
  over segment count, length, and shape; only the segment shape (duration
  plus an optional pace/power range) is fixed by the schema.
- From the segment list, code deterministically computes three overall
  numbers, none of them separately authored: overall duration is the sum of
  every segment's duration, work and rest included; the overall pace/power
  range is a duration-weighted blend of the work segments' individual
  ranges (rest segments do not contribute, they carry no intensity target);
  overall distance is derived from each work segment's own duration and
  pace, summed, and stays contextual, never independently prescribed or
  flagged.
- The evaluator computes the athlete's actual overall the same way: actual
  total distance over actual total work time gives the actual blended pace
  (duration-weighted average power for cycling); actual total duration is
  the sum of what was actually logged. The output check compares this
  single actual-overall value against the code-computed overall range for
  one `BELOW_EXPECTED_OUTPUT` / `WITHIN_EXPECTED_OUTPUT` /
  `ABOVE_EXPECTED_OUTPUT` verdict per session, same vocabulary as today.
- V1 does not verdict individual segments. A session with a strong rep and a
  weak rep can average out to a single "fine" verdict. This is an accepted
  v1 simplification, not a free win; revisit once per-segment verdicts are
  in scope.

Unaffected exceptions, unchanged by this decision:

- Strength: duration-only, matched not flagged.
- RPE-fallback sessions: no pace or power data; duration stays
  independently prescribed on the E6 scalar target and ±10% tolerance.
- Cycling's continuous/primary mode: duration and power are still
  prescribed together as already locked; this decision only adds the
  structured/segment case on top, for cycling interval sessions.

Code implementation is a separate pass: `SessionTargets` needs a segment
sub-structure (an ordered list of work/rest segments) for structured
sessions, a deterministic overall-range aggregator, and the evaluator's
output-check computation needs to consume actual segment-level data (or an
equivalent actual breakdown) to compute the actual-overall blend. This
decision locks the model and scope only.

E7 through E11 in `docs/decisions/open.md` are the remaining evaluator
product gates: signal thresholds/precedence, volume-overshoot handling in the
per-session verdict, single-session efficiency-evidence semantics, where
intent adherence belongs in the weekly contract, and confirming HR as the
sole intent arbiter.

None of these evaluator decisions is `BUILT`. A separate dated-plan algorithm,
`compare_week()`, once matched workouts to planned sessions by nearest
same-discipline date; it was never wired to production and was removed on
2026-09-08 as superseded by the athlete-explicit matching decided above, not
kept as a first-week result.

## Data roles and history

- `BUILT` — plans are `weekly_training_plans`; actuals are `workouts` plus the
  matching discipline detail populated by the current repository mapper. Each
  detail table is one-to-one with a workout; no cross-table database constraint
  itself proves exactly one detail kind exists.
- `weekly_plan_outcomes` remains in the schema and can persist a dated-plan
  `WeekComparison`-shaped payload, but `compare_finished_week()`, its only
  writer, was removed on 2026-09-08; the table is currently unused by any code.
- `DESIGNED` — a first-week evaluation is a separate logical role even if an
  existing table is eventually reused.
- `DESIGNED` — onboarding baseline, workout evidence, weekly evaluation, current
  derived fitness state, historical progression, and confidence are distinct
  concepts and contracts.

Fitness-state storage, first-state seeding, correction semantics, and
`last_week_feedback` remain deferred to their owning design documents. They do
not block first-week evaluation persistence.

## Future load model

- `DESIGNED` — new athletes and athletes with insufficient evidence use
  `MODE_A_QUALITATIVE`; CTL/TSS is not an onboarding dependency.
- `DESIGNED` — CTL can improve load precision but does not determine phase names,
  and target finish time does not directly determine a CTL target.
- `DESIGNED` — load-mode selection and load calculations are deterministic, not
  LLM judgments. Exact formulas and activation gates remain open.

The `MODE_B_EMERGING` and `MODE_C_CTL_DRIVEN` names/activation designs are not
locked; they remain `PROPOSED` in `docs/design/load-model.md` and open decision
19.

## Process

- Documentation must not move a feature to `BUILT` until code and reachability
  have both been checked; callable-but-unwired behavior is labeled explicitly.
- Presentation-only work must not silently merge or broaden backend behavior.
