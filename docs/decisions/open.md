# Open Decisions

Only product decisions that block the active first-week evaluator milestone
belong here. Approved evaluator behavior is recorded in `locked.md`. Questions
for fitness state, ongoing planning, General/Stage planning, and future load
modes remain documented in their owning designs, but are deliberately deferred
and do not gate this milestone.

The previous 19-item list was reduced on 2026-09-07. E6, the signal
vocabulary/safety scope, and outcome persistence are approved and recorded in
`locked.md`; downstream decisions 11–19 were removed from the active gate
because the evaluator can persist a versioned outcome without implementing
those consumers.

Four more items, E8–E11, were added on 2026-09-08: gaps found while designing
the per-session ("Layer 2") verdict logic that the 2026-09-07 review did not
cover, since it focused on the weekly signal table rather than the per-session
comparison feeding it. They do not all block the same work:

- E8, E9, and E11 block vertical slice 4 (per-session deterministic
  comparison) in `docs/roadmap.md`'s Milestone 1.
- E7 and E10 block slice 5 (weekly aggregation).
- Slices 1–3 (stable session references, explicit linking, evidence exposure)
  are not blocked by any item in this file and can proceed now.

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

## E8 — Volume overshoot and the per-session intent verdict

Status: `OPEN DECISION`. Surfaced 2026-09-08 while reviewing the per-session
("Layer 2") verdict design; not previously tracked here.

The per-session intensity-intent verdict (`AS_PRESCRIBED` / `OVERCOOKED` /
`EASIER_THAN_EXPECTED` / `NOT_COMPARABLE`, see
`docs/design/first-week-evaluator.md`) is `DESIGNED` to be driven only by
actual HR against the reference band. Duration and distance deltas are
reported as separate facts and never change this verdict. As specified, a
session run 37% over its planned duration with HR fully inside the easy band
still receives `AS_PRESCRIBED`, identical to a session that matched the plan
exactly. This is also why the E7 weekly signal table never fires `BACK_OFF`
or `WATCH_EFFORT` on volume overshoot alone without an accompanying HR flag:
nothing upstream of it treats overshoot as a verdict-relevant fact.

Proposed options, none selected:

- **A. Keep as designed.** Duration/distance remain pure facts; only HR
  decides intent. A large, clean-HR overshoot is not treated as a session
  problem.
- **B. Add an independent volume flag.** A new session-level soft flag (for
  example `VOLUME_OVERSHOOT`), triggered when the duration or distance delta
  exceeds a threshold, reported alongside the intent verdict without changing
  it.
- **C. Let overshoot downgrade the verdict itself.** Above a chosen overshoot
  percentage, `AS_PRESCRIBED` becomes unavailable regardless of HR.

## E9 — "Positive efficiency evidence" as a single-session judgment

Status: `OPEN DECISION`. Surfaced 2026-09-08.

The per-session "positive efficiency evidence" flag is `DESIGNED` to be
computed absolutely within one session: output at or above the expected
range, HR inside the intended band, adequate source quality, and no
confounder. It requires no prior session or comparison. The design document's
own worked example cautions "one session does not prove a fitness gain," but
nothing in the specification withholds, renames, or qualifies the flag on
that basis; it is still recorded as unqualified positive evidence in week
one, when no prior week exists to compare against.

Proposed options, none selected:

- **A. Ship as designed.** Compute and surface it exactly as specified, an
  absolute single-session read; let downstream consumers (the weekly
  aggregate, later fitness-state trends) apply the "not yet a trend" caveat
  themselves.
- **B. Rename and defer.** Replace "efficiency evidence" with a name that
  does not imply a fitness claim (for example `CALIBRATION_EVIDENCE`) until a
  second comparable week exists to compare against.
- **C. Compute but mark provisional.** Keep the flag and its current name,
  but attach an explicit "unconfirmed, single-session" qualifier at the data
  level until multi-week comparison exists.

## E10 — Where intensity-intent adherence belongs in the weekly contract

Status: `OPEN DECISION`. Surfaced 2026-09-08.

The proposed aggregation equations in `docs/design/first-week-evaluator.md`
compute `intensity_intent_adherence_percent` alongside
`matched_duration_percent`, a capability measure. But intent adherence,
whether the athlete ran at the prescribed effort, is a behavioral,
plan-following fact, not a physiological capability fact, and not a
plan-completion fact either (its denominator is matched sessions with
comparable effort, not all planned sessions). As specified it sits
ambiguously inside the capability grouping.

Proposed options, none selected:

- **A. Leave it under capability**, as currently drafted.
- **B. Report it as a third, explicitly named axis** in the `WeeklyEvaluation`
  contract, separate from completion and capability.
- **C. Fold it into completion** instead, since it describes whether the
  athlete followed the plan's intent, not how fit they are.

## E11 — Confirm HR as the sole intent arbiter, pace/power as the output arbiter

Status: `OPEN DECISION`. Surfaced 2026-09-08; a confirmation of an existing
prose decision, not a new proposal.

`docs/design/first-week-evaluator.md` states the intensity-intent verdict is
based on "structured planned intent and usable HR context," while pace/power
receive a separate output-comparison flag (`BELOW`/`WITHIN`/
`ABOVE_EXPECTED_OUTPUT`) that never feeds the intent verdict. In effect: HR
alone decides whether the effort was right; pace/power alone describes what
the body produced. This is a real precedence choice, but it exists only in
that document's prose, not as an explicit bullet in `docs/decisions/locked.md`.

Proposed options, none selected:

- **A. Confirm as designed and promote to `locked.md`.** HR alone decides the
  intent verdict; pace/power stays a separate, non-arbitrating fact.
- **B. Require corroboration.** Since the reference HR band is only
  age-estimated (see the approximation caveat already locked for E6), assign
  `OVERCOOKED`/`EASIER_THAN_EXPECTED` only when pace/power output also
  disagrees with the plan; use HR to corroborate a pace/power-detected
  mismatch rather than to decide alone. Worked example 4 in the design doc
  (pace on target but HR high) already shows the ambiguity a wrong
  age-estimated zone can cause under option A.

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
