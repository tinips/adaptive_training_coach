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
  calls), then deterministic fallback with generation provenance.

## HR

- `BUILT` — neither weekly mode may prescribe HR. Both model-facing prescription
  boundaries exclude `HEART_RATE_BPM`, `average_hr_bpm`, and `hr_range_bpm`.
  Wider persisted plan types retain those fields only for legacy loading and the
  shared validator rejects legacy HR intensity or target prescriptions.
- `BUILT` — age-estimated Tanaka HR zones are display/reference-only and carry
  an approximation caveat.
- `DESIGNED` — the first-week evaluator may use HR only as a soft effort flag.
  One mismatch never automatically changes zones or declares lost fitness.

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
- `DESIGNED` — planned sessions without links are `MISSED`; eligible workouts
  without links are `EXTRA`.
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
  They do not reuse the existing dated `WeekComparison` shape.

The exact numerical metric tolerances and signal/coverage thresholds remain the
only evaluator product gates in `docs/decisions/open.md`.

None of these evaluator decisions is `BUILT`. The existing `compare_week()` is
a separate dated-plan algorithm that greedily matches nearest same-discipline
dates. It is callable code, but no production path triggers it and it returns no
first-week result.

## Data roles and history

- `BUILT` — plans are `weekly_training_plans`; actuals are `workouts` plus the
  matching discipline detail populated by the current repository mapper. Each
  detail table is one-to-one with a workout; no cross-table database constraint
  itself proves exactly one detail kind exists.
- `BUILT` but dormant — `weekly_plan_outcomes` can persist the dated-plan
  `WeekComparison` shape through `compare_finished_week()`.
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
