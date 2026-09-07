# Open Decisions

Only product decisions that block the active first-week evaluator milestone
belong here. Approved evaluator behavior is recorded in `locked.md`. Questions
for fitness state, ongoing planning, General/Stage planning, and future load
modes remain documented in their owning designs, but are deliberately deferred
and do not gate this milestone.

The previous 19-item list was reduced on 2026-09-07: evaluator decisions 1–6,
the signal vocabulary/safety scope, and outcome persistence were approved;
downstream decisions 11–19 were removed from the active gate because the
evaluator can persist a versioned outcome without implementing those consumers.

## E6 — Numerical metric tolerances

Status: `OPEN DECISION`. Source precedence and provenance are `DESIGNED` and
locked. The numerical table below is `PROPOSED` for product review; no evaluator
implementation may encode it before approval.

| Comparison | Proposed boundary expansion | Proposed minimum absolute allowance | Notes |
|---|---:|---:|---|
| Duration | ±10% around the planned value/range | 5 minutes | Always report the raw delta and selected source. |
| Distance | ±5% around the planned value/range | Running 250 m; cycling 1 km; swimming 50 m | Evaluate only when distance is an explicit structured target. |
| Running pace | ±5% outside each planned range boundary | 10 s/km | Lower seconds/km is faster. |
| Indoor cycling power | ±5% outside each planned range boundary | 10 W | Only `STATIONARY` cycling with a structured power target. |
| Swimming pace | ±5% outside each planned range boundary | 3 s/100 m | Require a reliable distance and duration source. |
| Reference HR band | 5 bpm outside the selected band | 5 bpm | Soft effort flag only; never a prescription or automatic state/zone change. |
| Sample-derived average-HR coverage | At least 80% of selected workout duration | — | Below this, samples cannot replace a stored summary average. |
| Summary-versus-sampled average-HR conflict | More than 10 bpm apart | 10 bpm | Emit `SOURCE_CONFLICT` and make the HR verdict `NOT_COMPARABLE`. |

For a scalar target `T`, “within” means `T ± max(percent × T, minimum)`.
For a range `[L, U]`, expand the lower and upper boundaries independently.
Raw values and deltas are retained even when a verdict is `NOT_COMPARABLE`.

Illustrative consequences of the proposal:

- a 40-minute target is within duration tolerance at 35–45 minutes;
- a `330–360 s/km` running range expands to `313.5–378 s/km`;
- a `180–220 W` stationary-cycling range expands to `170–231 W`; and
- an easy reference-HR band of `108–135 bpm` produces soft flags only below
  `103 bpm` or above `140 bpm`.

### Proposed structured-intensity mapping

The mapping uses only `session.intensity.rpe_range`; purpose, execution,
guidance, titles, and notes are never parsed.

| Structured RPE range | Proposed reference HR band |
|---|---|
| Entire range within 1–4 | Easy |
| Entire range within 5–6 | Moderate |
| Entire range within 7–10 | Hard |
| Range crosses one of those boundaries | `NOT_COMPARABLE` for HR |

Approval must either accept these numbers or replace them. Implementation must
store the selected rule-table version and metric provenance.

## E7 — Signal thresholds, precedence, and coverage

Status: `OPEN DECISION`. The five-value vocabulary and exclusion of free-text
pain/safety interpretation are `DESIGNED` and locked. The following table is
`PROPOSED`; every threshold requires product approval and later test coverage.

### Proposed minimum evidence

- A comparable endurance session has duration, its discipline's required
  structured primary target, the corresponding actual metric, and usable
  average HR. Missing HR, missing actual pace/power, or a plan with no structured
  pace/power target makes that session `NOT_COMPARABLE`; raw facts remain
  available. This deliberately makes current `RPE_FALLBACK` sessions
  non-comparable for capability in v1.
- Minimum weekly evidence is at least two comparable endurance sessions and at
  least 50% comparable coverage among matched endurance sessions.
- Strength, speed, cadence, missed sessions, and extras remain factual/contextual
  and do not satisfy the comparable-endurance minimum.

### Proposed precedence table

Apply the first matching row only.

| Priority | Proposed deterministic condition | Signal |
|---:|---|---|
| 1 | Minimum weekly evidence is not met | `INSUFFICIENT_EVIDENCE` |
| 2 | At least two comparable sessions are above the expanded HR band **and** actual weekly duration exceeds 115% of planned, or duration exceeds 130% and at least one comparable session is above the HR band | `BACK_OFF` |
| 3 | At least one comparable session is above the expanded HR band, or actual weekly duration is above 115% and at most 130% of planned duration | `WATCH_EFFORT` |
| 4 | At least 75% of matched endurance sessions are comparable; at least two show output at/above target with HR inside the expanded intended band; no over-effort condition applies; and actual weekly duration is 90–115% of planned | `ABSORBED_WELL` |
| 5 | Minimum evidence is met and no higher-priority row applies | `ON_TRACK` |

Missed sessions affect adherence only. They never directly produce
`WATCH_EFFORT` or `BACK_OFF` and never enter capability calculations. No v1
signal interprets pain, safety, diagnoses, or free text. Every result retains
the causing facts and evaluation-rule version.

In this proposal, “actual weekly duration” means all eligible actual duration,
including extras; “planned duration” means all `MATCHED + MISSED` planned
duration. For example, one or two above-band sessions with no volume overshoot
select `WATCH_EFFORT`; two above-band sessions plus 120% weekly duration select
`BACK_OFF`. HR alone never selects `BACK_OFF`. A week with only one comparable
session remains `INSUFFICIENT_EVIDENCE` regardless of completion.

## Deferred, non-blocking product work

The following topics are intentionally absent from the active approval gate:

- fitness-history storage, seeding, confidence, and correction rules —
  `docs/design/fitness-state.md`;
- the exact `last_week_feedback` consumer contract and ongoing-planner
  activation/matching convergence — `docs/design/planner-architecture.md`;
- General Planner phases, Stage progression/disruption budgets, and load-mode
  formulas/activation — `docs/design/planner-architecture.md` and
  `docs/design/load-model.md`.

They return to this file only when their milestone becomes active.
