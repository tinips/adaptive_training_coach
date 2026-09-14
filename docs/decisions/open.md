# Open Decisions

This file records unresolved product choices. Approved evaluator behavior is
recorded in `locked.md`. Questions for fitness state, ongoing planning,
General/Stage planning, and future load modes remain documented in their owning
designs, but are deliberately deferred and do not gate this milestone.

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

## Discipline prescription contract and evaluator coverage

Status: `OPEN REFINEMENT` (2026-09-13). The first-week implementation now
enforces the current contract below, but the long-term product policy and its
ongoing-planner equivalent are not locked. This section is the decision record
to refine before extending the contract beyond the first-week menu.

### Current first-week contract

| Discipline / condition | What the plan prescribes | Why | What the evaluator can compare when a workout is explicitly linked |
|---|---|---|---|
| Running with a supported race result | Pace range plus distance-volume range. Duration is not independently prescribed: code derives it from the range averages (`distance ÷ pace`) whenever the model omits a duration — already built and verified live (2026-09-14): 8.0–9.0 km @ 300–335 s/km → 45 min. See `locked.md`, "Duration derivation". The open item is narrower than previously stated here: a model-*supplied* duration is not yet rejected or overridden for consistency. | Pace is the useful external-output target for a run, while distance states the planned work; deriving duration in code keeps the three numbers consistent instead of letting the model set three independent values that can silently disagree. | Actual canonical pace against the planned pace range, plus duration and distance completion. Pace is `NOT_COMPARABLE` if the workout has no usable pace. |
| Running without a supported pace source | RPE, duration, and distance-volume range. | Do not invent pace from age, goal, or a guessed fitness level. | Duration and distance completion; no objective pace-output verdict. Average HR may supply only the evaluator's soft effort-context flag when available. |
| Cycling with an indoor trainer available (`INDOOR`/`BOTH`) and a reported FTP | Power range derived from FTP, plus duration and distance-volume range. | Trainer power is repeatable enough to prescribe and directly supports a power-based calibration signal. | Actual average power against the planned watt range, plus duration and distance completion. Missing power makes the output verdict `NOT_COMPARABLE`; distance does not replace power. |
| Cycling without both indoor access and FTP | RPE, duration, and distance-volume range. | The app must not invent watts; an FTP alone is not currently used for an outdoor-only cycling prescription. | Duration and distance completion; no objective power-output verdict. |
| Swimming with a supported 400 m time | Pace range (per 100 m) plus distance-volume range. Duration is not independently prescribed: code derives it from the range averages (`distance ÷ pace`) whenever the model omits a duration, exactly like running — same `_DURATION_DERIVED_DISCIPLINES` mechanism, confirmed by direct execution (2026-09-14). Duration includes rests. | Pace is a repeatable-enough external-output target once a real benchmark exists, mirroring running; deriving duration keeps the numbers consistent instead of letting the model set independent values that can silently disagree. | Actual canonical pace against the planned pace range, plus duration and distance completion. Pace is `NOT_COMPARABLE` if the workout has no usable pace. |
| Swimming without a supported pace source (no 400 m time on file) | RPE, duration, and distance-volume range. Duration includes rests. | Do not invent pace from age, goal, or a guessed fitness level — same rule as running without a pace source. | Duration and distance completion; no objective pace-output verdict. Average HR may supply only the evaluator's soft effort-context flag when available. |
| Strength | Duration only, with qualitative controlled-execution guidance. | The product intentionally avoids sets, reps, loads, and numeric RPE in the current first-week strength menu. | Duration/completion only; no pace, power, or objective-output verdict. |

The plan is not plain text: every target above is stored as typed plan JSON on
the planned session. The Telegram message is only a rendering of that record.
The evaluator reads the structured intensity target and volume ranges, never
parses the message prose. A workout must be linked to the intended planned
session before it contributes to comparison or weekly aggregation.

### Decisions still needed

Items 1, 2, and 4 below were resolved 2026-09-13 in a design discussion
covering all four disciplines; they're kept here, marked resolved, rather
than deleted, so the reasoning stays attached to the original question.
Items 3 and 5 remain genuinely open.

1. **Cycling volume on an indoor trainer — RESOLVED (2026-09-13).** Distance
   stays a completion-volume target only; watts remain the objective
   intensity target. No kJ or other power-duration load metric is being
   added. This confirms the current-contract row above as long-term policy,
   not a placeholder pending a future change.
2. **Swimming progression — RESOLVED (2026-09-13), corrected (2026-09-14).**
   The 2026-09-13 note here previously said "no numeric swim pace target is
   ever planned" — that was wrong, caught during a docs-consistency check
   before implementation, not a new decision. Swimming has always mirrored
   running: `resolve_first_week_zones` already computes a real `NUMERIC`
   swim-pace mode (`SWIM_PACE_SECONDS_PER_100M`) from a reported 400 m time,
   with a genuine model-facing schema field
   (`PrescribedSessionTargets.swim_pace_seconds_per_100m`) and the same
   zone-widening bands as running — see `docs/design/first-week-planner.md`.
   That mechanism stays exactly as built; nothing about it changes here.
   The only thing this item resolves is the *no-benchmark* case (no 400 m
   time on file): that now follows the same rule as "Running without a pace
   source" below, generalized to both disciplines — see item 3.
3. **Endurance disciplines without a pace source — directional decision
   recorded (2026-09-13), generalized to swimming (2026-09-14), design
   still open.** Originally scoped to running only; the same rule now
   covers swimming without a 400 m time, so the two disciplines stay
   symmetric rather than swim getting a bespoke no-benchmark mechanism.
   Direction: derive a deliberately conservative pace from stated
   volume/frequency, but only use it once the athlete has explicitly
   reviewed and accepted it as a placeholder; no silent inference. Still
   undecided: the exact derivation formula (per discipline), and how
   "explicit acceptance" is implemented in the Telegram flow (a one-time
   confirmation, or re-confirmed on some cadence).
4. **Volume semantics — RESOLVED (2026-09-13).** One strict definition
   applies to every discipline: `distance_range_meters` is total distance,
   including warm-up, cool-down, swim rests/pauses, and trainer virtual
   distance. No per-discipline variation.
5. **Evaluator feedback:** unchanged, still deliberately deferred — the
   evaluator persists deterministic facts; it does not automatically change
   prescription, FTP, pace zones, or fitness state.

## Deferred, non-blocking product work

The following topics are intentionally absent from the active approval gate:

- fitness-history storage, seeding, confidence, and correction rules (the
  correction-cascade rule itself is decided, see `docs/decisions/locked.md`,
  "Fitness state"; storage, seeding, and confidence remain open) -
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
  `docs/design/load-model.md`;
- whether an outdoor-cycling power-meter flag should be added to the
  baseline - raised 2026-09-13: today, `riding_environment` alone gates
  power-zone resolution (see `zones.py`), so an outdoor-only rider with FTP
  and a real power meter still gets the RPE fallback, never power. Not
  decided; explicitly deferred, not part of the current discipline
  prescription contract.

They return to this file only when their milestone becomes active.
