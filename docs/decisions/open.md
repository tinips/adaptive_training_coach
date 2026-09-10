# Open Decisions

Only product decisions that block the active first-week evaluator milestone
belong here. Approved evaluator behavior is recorded in `locked.md`. Questions
for fitness state, ongoing planning, General/Stage planning, and future load
modes remain documented in their owning designs, but are deliberately deferred
and do not gate this milestone.

The previous 19-item list was reduced on 2026-09-07. E6, the signal
vocabulary/safety scope, and outcome persistence are approved and recorded in
`locked.md`; downstream decisions 11-19 were removed from the active gate
because the evaluator can persist a versioned outcome without implementing
those consumers.

E7 through E11 were added on 2026-09-08 as gaps found while designing the
per-session ("Layer 2") verdict logic. All five are now resolved (2026-09-10)
and recorded in `locked.md`, "Signal scoring, THRIVING, and volume status":
the additive per-session formula and weekly bands (E7), duration/distance
never touching the intent verdict (E8, Option A), the ratio-gated
positive-efficiency-evidence definition shipping under its existing name
(E9), the variance-flag mechanism that supersedes the old intent-mismatch
note while keeping its Option B axis placement (E10), and HR alone deciding
the intent verdict (E11, Option A). Slices 1-5 in `docs/roadmap.md`'s
Milestone 1 are no longer blocked by anything in this file.

One sub-question from E7's minimum-evidence subsection was never part of the
E7-E11 resolution above and remains genuinely open:

## Low comparable-metric coverage above the minimum-evidence bar

Status: `OPEN DECISION`. Carried over from E7, unresolved since 2026-09-08.

A week can meet the 75%-matched minimum-evidence bar (see `locked.md`,
"First-week evaluator", "Minimum evidence") while still having low
comparable-metric coverage among those matched sessions, for example matched
sessions with missing HR or missing actual pace/power. Whether such a week
should still be fully evaluated with a lower-confidence note, or held back
further (closer to `INSUFFICIENT_EVIDENCE` treatment), is not decided.

This depends in part on how well the now-mandatory HR-capture rule (see
`locked.md`, "Workout capture") reduces missing-data cases in practice, so it
may be worth revisiting after that rule has been live for a few weeks of real
data rather than deciding it from first principles now.

## Deferred, non-blocking product work

The following topics are intentionally absent from the active approval gate:

- fitness-history storage, seeding, confidence, and correction rules -
  `docs/design/fitness-state.md`;
- the exact `last_week_feedback` consumer contract and ongoing-planner
  activation/matching convergence - `docs/design/planner-architecture.md`;
- how the weekly planner's own prompt should weigh a THRIVING signal against
  a meaningfully `BELOW_RANGE` volume status in the same week (for example,
  planning the next week closer to how a `WATCH_EFFORT` week would be
  handled) - flagged 2026-09-10 in `locked.md`'s "Weekly volume range
  status", exact wording and thresholds are weekly-planner prompt design
  work, deliberately not decided at the evaluator level;
- extending the screenshot scanner's per-session volume floor (`locked.md`,
  "Workout capture") to TCX file capture, the other ingestion path - flagged
  2026-09-10, deliberately out of scope for the first build since TCX
  capture is optional and off by default;
- General Planner phases, Stage progression/disruption budgets, and load-mode
  formulas/activation - `docs/design/planner-architecture.md` and
  `docs/design/load-model.md`.

They return to this file only when their milestone becomes active.
