# First-Week Evaluator — Design

Status: deterministic core `BUILT, DORMANT` as of 2026-09-10 (identity,
linking, evidence, per-session comparison, weekly aggregation/signal,
immutable outcome persistence); the Telegram delivery UI and fitness-state
handoff remain `DESIGNED`/`PROPOSED`, not implemented. See
`docs/briefs/backlog/first-week-evaluator.md` for the exact test-first status
per slice.

## Status vocabulary

- `BUILT` — present in the current code. Reachability is stated separately
  (`DORMANT` means tested and callable, but nothing in the live bot calls it
  yet; unqualified `BUILT` in this document means the same unless noted).
- `DESIGNED` — an agreed target behavior, but no implementation exists yet.
- `PROPOSED` — a recommended detail that has not been accepted as a decision.
- `OPEN DECISION` — implementation would encode product behavior that still
  needs explicit sign-off.

The first-week evaluator's deterministic core (link, per-session comparison,
weekly aggregate, immutable outcome) is `BUILT, DORMANT` in code as of
2026-09-10. No Telegram link/evaluate/review UI, fitness snapshot, or
planner-feedback contract/consumer exists yet.

## Current boundary, verified against code

| Capability | Status | Evidence and limitation |
|---|---|---|
| First-week menu generation and persistence | `BUILT` and production-wired | `FirstWeekPlanner` is composed in `backend/app/bot/main.py`; `FirstWeekPlan` is an unscheduled menu persisted in `WeeklyTrainingPlan.plan_jsonb`. |
| Workout capture | `BUILT` and production-wired behind settings | Screenshot and TCX paths persist `Workout` plus discipline detail when their feature settings permit them (`screenshot_import_enabled` defaults on; `tcx_import_enabled` is optional and defaults off). Screenshot capture can store pace, speed, cycling power, cadence, and average/max HR. Apple Health models/repositories are remnants, not a current upload path. |
| Stable reference to one planned session | `BUILT` (2026-09-10) | `PlanSession.id` is a code-generated UUID (`app/schemas/weekly_plans.py`), never authored by the LLM-facing prescription types. Plans write at schema v5; a legacy v4 plan still loads but is not linkable (no durable identity). |
| Explicit workout-to-session link | `BUILT, DORMANT` (2026-09-10) | `planned_session_links` table, `PlannedSessionLinkRepository`, and `LinkingService` (`app/services/weekly_evaluation/linking.py`) enforce ownership, plan-revision eligibility, and athlete-local week eligibility. No Telegram link/review UI exists yet. |
| First-week per-session comparison | `BUILT` (2026-09-10) | `compare_session()` (`app/services/weekly_evaluation/comparison.py`) implements the metrics and safety interpretation below. |
| First-week weekly aggregate and signal | `BUILT` (2026-09-10) | `aggregate_week()` (`app/services/weekly_evaluation/aggregation.py`) implements the full rule table; `PROVISIONAL` calibration numbers are named constants. |
| Outcome persistence | `BUILT, DORMANT` (2026-09-10) | `FirstWeekEvaluationOutcome` (migration 0053) is immutable/versioned, unrelated to the removed dated comparator's `WeekComparison` shape; `OutcomeService` persists idempotently. No manual-trigger UI calls it. |
| Fitness-state history | `PROPOSED`; storage `OPEN DECISION` | No fitness-snapshot table or repository exists. The hybrid recommendation is in `docs/design/fitness-state.md`. |
| `last_week_feedback` | Deferred downstream | No schema, field list, reader, persistence, or planner consumer exists; it does not block evaluation persistence. |
| No-HR-prescription invariant | `BUILT` | Both weekly model-facing schemas exclude HR intensity and HR target fields. Wider persisted types retain them only for legacy reads. Completed-workout HR remains valid evaluator evidence. |

A comparator for dated `WeeklyPlan` objects, `compare_week()`/
`compare_finished_week()`, previously existed and greedily paired each planned
session with the nearest unused same-discipline workout. It was never a live
athlete flow (no production code called it, the Telegram planning port never
exposed it), and it used exactly the nearest-date matching this design
rejects, so it was removed on 2026-09-08 rather than kept as precedent. The
`weekly_plan_outcomes` table and its repository remain in the schema but are
currently unused; do not reuse their `WeekComparison` shape for the first-week
evaluator's outcome record.

## End-to-end target

```mermaid
flowchart TD
    P["First-week menu<br/>BUILT"]
    X["Screenshot or TCX capture<br/>BUILT; settings-gated"]
    A["Persisted workout actuals<br/>BUILT"]
    L["Athlete selects planned session<br/>BUILT, DORMANT; no UI"]
    C["Per-session comparison<br/>BUILT"]
    W["Weekly aggregate and suggested signal<br/>BUILT"]
    O["Persist immutable evaluation revision<br/>BUILT, DORMANT; no trigger UI"]
    F["Update fitness state/history<br/>PROPOSED; storage OPEN"]
    B["Build last_week_feedback<br/>deferred downstream"]
    N["Next weekly plan consumes feedback<br/>PROPOSED; not wired"]

    P --> L
    X --> A --> L
    L --> C --> W --> O --> F --> B --> N
```

The evaluator emits facts, data-quality flags, and a deterministic suggested
signal. It never silently changes zones or authors the next plan. A future
planner consumes the evaluation and makes the planning choice within its own
guardrails.

## Terms and identities

- **Planned session** — one entry in `FirstWeekPlan.sessions`. It has intent and
  targets, but no date and currently no stable session-level identifier. The
  approved target adds a code-generated UUID.
- **Workout** — a persisted actual activity. It is independent of any plan.
- **Session reference** — a code-generated UUID used to select one planned
  session. Array position is never permanent identity.
- **Link** — the athlete-confirmed association between a workout and a session
  reference. A suggestion made by code is not a link until the athlete confirms
  it.
- **Matched** — a planned session with an accepted link to actual evidence.
- **Missed** — a planned session with no accepted link at evaluation time.
- **Extra** — an otherwise eligible workout with no accepted link to this
  plan. Removed from v1 scope (revised 2026-09-08): every logged workout must
  now be linked to a planned session before it can be confirmed, so no
  unlinked workout can exist. Reserved for a future, ongoing-planner
  milestone.
- **Cancelled agreed** — a future status for a confirmed plan change. It is not
  part of v1 and must not be inferred from a missing workout.
- **Completion** — whether planned work was done. It may include missed work in
  its denominator.
- **Capability comparison** — comparison of performance evidence from matched
  sessions only. A missed session is never a zero-valued performance sample.
- **Intensity intent** — the planned `IntensityTarget` range and its RPE range,
  not the similarly named optional scalar fields in `SessionTargets`.
- **Planned RPE** — `session.intensity.rpe_range`, a prescription present on
  every plan session. `SessionTargets.rpe`, when present, is also planned data;
  neither field is the athlete's post-workout report.
- **Actual RPE** — not required or consumed by evaluator v1. No current workout
  or feedback write boundary persists it.

These distinctions prevent “75% of sessions completed” from being confused
with “performance was 75% of target.”

## Explicit matching

Status: identity, explicit matching, primary-link cardinality, relinking, and
candidate eligibility are `BUILT, DORMANT` (2026-09-10) — `LinkingService`
and `planned_session_links` enforce all of it; no athlete-facing UI exists.

The athlete explicitly selects the planned first-week session represented by a
logged workout. The system must not infer the final link from nearest date: menu
sessions do not have planned dates. It may later offer a discipline-based
candidate list, but an automatic suggestion cannot become a persisted match
without confirmation.

For v1, a workout can link to at most one planned session, and a planned session
has at most one primary workout used by the evaluator. Unlinked planned
sessions are `MISSED`, which remains visible and does not delete or mutate
the underlying plan. As of the 2026-09-08 revision, a workout cannot be
confirmed without linking it to a planned session, so the earlier unlinked
`EXTRA` workout state no longer exists in v1; it is deferred to a future,
ongoing-planner milestone. `CANCELLED_AGREED` is reserved for a future
confirmed plan-change flow.

### Matching example

The references below are illustrative target identifiers, not fields that exist
today.

```mermaid
flowchart LR
    S1["S-run-easy<br/>40 min easy run"]
    S2["S-run-steady<br/>45 min steady run"]
    S3["S-strength<br/>30 min strength"]
    W1["W-101<br/>40 min run"]
    W2["W-102<br/>28 min strength"]

    W1 -- "athlete confirms" --> S1
    W2 -- "athlete confirms" --> S3
    S2 -. "no link = MISSED" .-> M["MISSED"]
```

Result: two matched pairs and one missed planned session. The workout dates
can help order the UI, but they do not decide either match. Under the
2026-09-08 revision, a workout with nothing to link to, for example a swim
when no planned session matches it, cannot be confirmed in v1; that case is
deferred to a future milestone rather than logged as `EXTRA`.

The v1 primary link is one-to-one. Relinking is allowed before evaluation.
Post-evaluation correction creates a superseding evaluation revision. Eligible
workouts use the athlete-local Monday–Sunday week; duplicate or unresolved
workouts remain excluded. Secondary/multi-workout matching is deferred.

## Actual evidence available to an evaluator

The database contains more data than `FitnessWorkoutEvidence` exposes. The
evaluator must deliberately define its own read model rather than reuse
`FitnessWorkoutEvidence` unchanged.

| Actual metric | Stored today | Available via `FitnessWorkoutEvidence` |
|---|---:|---:|
| Duration, distance, moving duration | Yes | Yes |
| Derived running/swimming pace | Yes; also derivable from distance and moving duration | Derivable from distance and moving duration; no stored pace field |
| Cycling speed | Yes | No |
| Cycling average/max power | Yes | No; the schema has no power field |
| Average/max HR summary | Yes on discipline detail rows | No numeric value; the projection exposes only `coarse_heart_rate_present` |
| Reliable timestamped HR observations | Yes when an import supplies them | Yes |
| Optional “how it felt” text | No | No |

V1 does not require post-workout RPE. Optional short feel text may be added to a
future write boundary, but is never required and never enters a planner prompt
automatically. Objective captured metrics are the evaluator inputs. A missing
average HR or discipline-relevant pace/power value makes the corresponding
session verdict `NOT_COMPARABLE`.

## Per-session comparison

Status: `BUILT` (2026-09-10) — `compare_session()` implements the metric set,
source-precedence architecture, safety behavior, and E6 numerical
tolerances. The locked E6 rules are in
`docs/decisions/locked.md`.

Only matched pairs enter this computation. The calculation is deterministic and
does not call an LLM.

```mermaid
flowchart LR
    P["Planned session<br/>duration, distance, intensity intent"]
    A["Linked actual<br/>measurements + provenance"]
    V["Comparable values<br/>and quality flags"]
    D["Per-metric deltas<br/>direction-aware"]
    I["Intent result<br/>plus soft HR context"]

    P --> V
    A --> V
    V --> D --> I
```

The comparison first establishes that planned and actual values are comparable;
only then does it calculate deltas. A stored HR target field is not a supported
first-week prescription and must not become a compliance target merely because
the current schema can deserialize it.

### Proposed `PerSessionInsight` contract

The name is `PROPOSED`; the facts are `DESIGNED` unless marked otherwise.

| Field group | Required meaning |
|---|---|
| Identity | Exact plan/revision, stable planned-session reference, and linked-workout reference with athlete ownership |
| Status | `MATCHED`, `MISSED`; future `CANCELLED_AGREED` (`EXTRA` deferred, see the 2026-09-08 revision above) |
| Duration | Planned/actual seconds, delta seconds, delta percent, and source/quality |
| Distance | Planned/actual metres and delta where the discipline/targets make it relevant |
| Primary output | Running pace, swimming pace, or cycling power comparison when planned and available; cycling speed as context unless explicitly targeted in a future schema |
| Effort | Average/max HR soft comparison when usable; optional feel is stored separately and is not interpreted |
| Verdict | Intensity-intent verdict, metric coverage, missing-data indicators, and efficiency evidence |
| Explanation | Factual reasons and soft flags; no diagnosis or automatic zone/state change |

`MATCHED` requires both references. `MISSED` has a planned-session reference and
no workout. The future `CANCELLED_AGREED` has a planned-session reference plus
confirmation provenance, not merely an absent workout. (`EXTRA`, a workout
reference with no planned session, is deferred; see the 2026-09-08 revision
above.)

### Core calculations

Duration:

```text
duration_delta_seconds = actual_duration_seconds - planned_duration_seconds
duration_delta_percent =
    duration_delta_seconds / planned_duration_seconds * 100
```

Use positive canonical moving duration when present, otherwise elapsed duration,
and return the selected source as provenance. Planned duration is positive
under the current plan schema, so the denominator is defined.

Distance, when the planned target contains it:

```text
distance_delta_meters = actual_distance_meters - planned_distance_meters
distance_delta_percent =
    distance_delta_meters / planned_distance_meters * 100
```

Running pace:

```text
actual_pace_seconds_per_km =
    actual_duration_seconds / (actual_distance_meters / 1000)
```

Swimming pace:

```text
actual_pace_seconds_per_100m =
    actual_duration_seconds / (actual_distance_meters / 100)
```

Cycling speed:

```text
actual_speed_kph =
    (actual_distance_meters / 1000) / (actual_duration_seconds / 3600)
```

Elevation adjustment (running and cycling), `DECIDED` gating and mechanism,
`PROVISIONAL` coefficients, see `docs/decisions/locked.md`, "Elevation
adjustment (running and cycling)": for running types `OUTDOOR`/`TRAIL`, and
for cycling types `ROAD`/`GRAVEL`/`MTB` when power is unavailable, actual
pace or speed is adjusted for the workout's recorded elevation gain and loss
before it is used below. Running types `TRACK`/`TREADMILL` and cycling types
`STATIONARY`/`OTHER` are never adjusted. Both the raw and the adjusted value
are stored; the adjusted value is what feeds the output-comparison
classification and the ratio check for a gated workout, never the raw value
alone.

For a scalar target whose planned value is the reference, also report:

```text
actual_to_planned_percent = actual / planned * 100
```

Do not use an unsigned “accuracy” percentage that hides whether the actual was
above or below the target. The raw planned/actual values, signed delta, unit,
source, and quality remain part of the result.

Running pace is stored/calculated as seconds/km and swimming pace as
seconds/100 m, then displayed through `format_pace_min_sec()`. Cycling speed is
contextual output because the current first-week planner does not prescribe a
speed target. Cycling power compares actual average watts against the planned
power range when power is captured and included in the evaluator projection.

For planned intensity, compare the actual value with
`session.intensity.target_range`:

- Running pace: seconds/km; lower is faster.
- Swimming pace: seconds/100 m; lower is faster.
- Cycling power: watts; higher is harder.
- RPE fallback: no objective output verdict in v1; the structured `rpe_range`
  may select a reference-HR band, but HR remains a soft flag.

With the locked boundary tolerance applied symmetrically, classification is
direction-aware:

| Metric | Below expected output | Within expected output | Above expected output |
|---|---|---|---|
| Running/swimming pace (seconds per unit) | Slower than the tolerated upper bound | Inside tolerated range | Faster than the tolerated lower bound |
| Cycling power (watts) | Below the tolerated lower bound | Inside tolerated range | Above the tolerated upper bound |

Cycling speed has the higher-is-more-output direction, but it remains context
unless a future plan schema explicitly prescribes a speed range. Duration and
distance deltas describe volume completion; they do not by themselves prove
fitness or intensity compliance.

Prescribed-range tolerance, `DECIDED` model and scope, `PROVISIONAL` floor
numbers, see `docs/decisions/locked.md`, "Prescribed-range tolerance model
(running/swim pace, cycling power, distance/volume)": for running pace, swim
pace, cycling power, and distance/volume for running, swimming, and cycling,
the LLM-prescribed range's own boundaries are the flagging boundaries. The
E6 boundary-expansion tolerance no longer applies to these four metrics. A
distance target for these disciplines is now a prescribed range, not an
optional scalar. Strength and RPE-fallback sessions are unaffected and keep the E6
scalar-plus-tolerance model described above.

Duration derivation, `DECIDED` model, see `docs/decisions/locked.md`,
"Duration derivation (continuous and structured sessions)": duration is no
longer independently prescribed or flagged for running, swimming, or
cycling. For a continuous single-effort session, duration is derived from
the prescribed distance and pace/power range. For a structured session
(intervals, or distinct work/rest blocks), the LLM designs an ordered list
of work segments (duration plus a pace-or-power range) and rest segments
(duration only); code deterministically computes an overall duration, an
overall duration-weighted pace/power range, and a contextual overall
distance from that list, and the output check compares the athlete's actual
overall, computed the same way from actual data, against the code-computed
overall range for one verdict per session. V1 does not verdict individual
segments. Strength, RPE-fallback sessions, and cycling's continuous/primary
mode (duration plus power) are unaffected.

HR is context. The persisted plan has a structured `rpe_range`, not a structured
easy/moderate/hard enum. An approved deterministic mapping from that RPE range
to the corresponding built, age-estimated `ReferenceHeartRateZones` band is
therefore required before comparing a reliable or explicitly accepted actual
average HR. The evaluator must not parse purpose/guidance prose to choose the
band, and it must preserve the approximation caveat. The mapping boundaries are
locked in E6.

`DESIGNED` HR verdicts:

- inside the intended approximate band: `AS_PRESCRIBED`;
- clearly above it: `OVERCOOKED`;
- clearly below it: `EASIER_THAN_EXPECTED`; and
- no usable HR or no approved intent-to-band mapping: `NOT_COMPARABLE`.

“Clearly” requires an approved tolerance. An HR verdict is always a soft flag,
never a prescription, diagnosis, zone change, or automatic fitness change.

`PROPOSED` result vocabulary:

- Output comparison: `BELOW_EXPECTED_OUTPUT`, `WITHIN_EXPECTED_OUTPUT`,
  `ABOVE_EXPECTED_OUTPUT`, or `UNKNOWN`, with direction handled separately for
  pace and power.
- Intensity-intent verdict: `AS_PRESCRIBED`, `OVERCOOKED`,
  `EASIER_THAN_EXPECTED`, or `NOT_COMPARABLE`, based on structured planned
  intent and usable HR context. V1 does not consume actual RPE or feel text.
- Data-quality flags explain missing or coarse inputs rather than inventing a
  value.
- An HR observation can add an effort flag, never turn the session into an
  error and never adjust a zone.

Positive efficiency evidence, `DECIDED` (2026-09-10), redefines and narrows
this: see `docs/decisions/locked.md`, "Ratio check and positive efficiency
evidence", for the full mechanism. In outline, it requires a ratio check
(speed-or-power divided by HR, compared against an interval derived from the
plan's own pace/power range and reference HR zone, no extra tolerance
layered on top) to land above that interval, AND one of exactly three
intent/output combinations (`AS_PRESCRIBED` + `ABOVE_EXPECTED_OUTPUT`,
`EASIER_THAN_EXPECTED` + `ABOVE_EXPECTED_OUTPUT`, or
`EASIER_THAN_EXPECTED` + `WITHIN_EXPECTED_OUTPUT`), AND adequate source
quality, AND no confounder that blocks comparability. Faster pace or higher
power without usable effort evidence, or without the ratio clearing its
interval, is `NOT_COMPARABLE` for efficiency, even though the raw output
fact remains visible.

Source precedence is `DESIGNED`: canonical moving duration precedes elapsed;
stored canonical running/swimming pace precedes a fresh derivation; stationary
cycling average power is primary; speed/cadence are contextual; and a stored
summary average HR precedes an adequately covered reliable-sample average.
Every selection retains provenance. Numerical quality/tolerance gates and the
RPE-range-to-HR-band mapping are locked in E6.

### Worked running examples

All three examples use an illustrative 40-year-old athlete. The built Tanaka
calculation gives estimated max HR `208 - 0.7 × 40 = 180 bpm`; the built easy
reference band is `108–135 bpm`. A 10 km result of 42:00 gives `252 s/km`; the
built first-week easy pace resolver gives `277–315 s/km` after rounding.
These are design examples, not current test fixtures.

1. **Positive efficiency above the expected pace.** Planned: 45-minute easy
   run with an illustrative RPE range of 2–3, expected pace `5:30–6:00/km`
   (`330–360 s/km`). Actual: 45 minutes,
   8.5 km, with average HR inside the easy reference band. Pace is
   `2700 / 8.5 = 317.6 s/km`, displayed as `5:18/km`. Output is above the
   expected range, but effort remains easy. Record positive efficiency evidence
   and do not classify the result as overtraining. Without usable HR and actual
   HR, the effort verdict becomes `NOT_COMPARABLE`, not automatically
   positive.

2. **Positive calibration evidence inside the expected range.** Planned:
   40 minutes, 8.0 km, easy pace and an illustrative RPE range of 2–3,
   `277–315 s/km`. Actual: 40 minutes, 8.5 km, average HR 128 bpm.
   Actual pace is `2400 / 8.5 = 282.4 s/km` (`4:42/km`), inside the planned
   range. Distance delta is `+500 m`, or `+6.25%`; HR is inside the easy
   reference band. Result: duration and intensity intent were respected while
   exceeding the distance target, so this is positive baseline-calibration
   evidence. One session does not prove a fitness gain.

   Revision note (2026-09-10): under the redefinition in
   `docs/decisions/locked.md`, examples 1 and 2 above both land on
   intent/output combinations that qualify for positive efficiency evidence
   (`EASIER_THAN_EXPECTED`/`ABOVE_EXPECTED_OUTPUT` and
   `EASIER_THAN_EXPECTED`/`WITHIN_EXPECTED_OUTPUT` respectively, assuming
   both are read as `EASIER_THAN_EXPECTED` on intent), but neither example
   states an exact actual HR precise enough to also verify the new ratio
   check, since example 1 gives only "inside the easy reference band" rather
   than a number. Whether either example clears `ratio_max` depends on that
   missing figure; do not treat these two worked examples as still
   automatically qualifying without checking the ratio in an actual
   implementation or test case.

3. **Overcooked easy session.** Same 40-minute plan; actual: 40 minutes, 9.0 km,
   average HR
   152 bpm. Pace is `2400 / 9.0 = 266.7 s/km` (`4:27/km`), faster than the
   `277 s/km` fast boundary. HR is in the age-estimated moderate band, not the
   easy band. Result: `ABOVE_EXPECTED_OUTPUT` plus an `OVERCOOKED` soft flag.
   Extra distance is not counted as positive capability
   evidence because the athlete changed the intended effort.

4. **Pace on target but HR high.** Same plan; actual: 40 minutes, 8.0 km, average
   HR 149 bpm. Pace is `300 s/km` (`5:00/km`), within intent, while HR is above
   the easy reference band. Result: output is within the expected range and the
   HR verdict is an `OVERCOOKED` soft flag whose cause is ambiguous. Fatigue,
   heat, dehydration, stress, poor recovery, illness, sensor error, and an
   inaccurate age-based reference are plausible; the evaluator does not choose
   among them, diagnose, or lower fitness automatically.

## Missed-workout rule

Status: `BUILT` (2026-09-10) — `classification.py`'s `classify_planned_sessions()`.

A missed session remains visible in completion reporting but is excluded from
pace, power, HR, and other capability math. It has no actual measurement,
so substituting zero would fabricate evidence. The weekly result should expose
separate denominators:

- **Planned-session completion:** `MATCHED / (MATCHED + MISSED)` for v1.
- **Matched-performance adherence:** performance comparisons over matched
  sessions with the required actual metric only.
- **Weekly volume:** all eligible actual work, matched sessions only in v1
  since `EXTRA` no longer exists (revised 2026-09-08), reported separately
  from plan adherence.

## Weekly aggregate

Status: `BUILT` (2026-09-10) — `aggregate_week()` implements every fact and
the deterministic signal rule table, see `docs/decisions/locked.md`,
"Signal scoring, THRIVING, and volume status" (`PROVISIONAL` calibration
numbers are named constants).

The aggregate contains:

- matched, missed, and unknown/cancelled counts (no `extra` count in v1, see revision note above);
- planned and actual minutes by discipline, with each denominator named;
- duration/distance/intensity comparisons over matched sessions;
- HR soft flags and data-quality counts;
- total actual weekly duration and distance (matched sessions only in v1);
- baseline-calibration evidence by discipline;
- weekly volume range status, per discipline and blended (see
  `docs/decisions/locked.md`); and
- one deterministic suggested signal plus the facts that caused it. The
  vocabulary is `THRIVING` (renamed from `ABSORBED_WELL`, 2026-09-10),
  `ON_TRACK`, `WATCH_EFFORT`, `BACK_OFF`, and `INSUFFICIENT_EVIDENCE`.

### Aggregation equations

For v1, before the future cancellation status exists:

```text
planned_session_denominator = matched_count + missed_count
session_completion_percent =
    matched_count / planned_session_denominator * 100

matched_metric_coverage_percent =
    matched_with_comparable_metric / matched_count * 100

intensity_intent_adherence_percent =
    matched_as_prescribed / matched_with_comparable_effort * 100

matched_duration_percent =
    sum(actual_duration for matched sessions)
    / sum(planned_duration for those same matched sessions) * 100

planned_volume_completion_percent =
    sum(actual_duration for matched sessions)
    / sum(planned_duration for all planned sessions) * 100

all_actual_duration =
    sum(actual_duration for matched sessions)
    + sum(actual_duration for eligible extra workouts)  # always 0 in v1; EXTRA deferred

density =
    sum(session point value for matched, comparable sessions)
    / count(matched, comparable sessions)
```

`density` selects the weekly band (`ON_TRACK` / `WATCH_EFFORT` / `BACK_OFF`);
`session point value` is intent value plus output value per session. See
`docs/decisions/locked.md`, "Signal scoring, THRIVING, and volume status" for
the full per-session formula, the band cut lines, and the `THRIVING` gate;
this document does not restate those numbers to avoid the two drifting apart.

A percentage is `UNKNOWN`, not zero, when its denominator is zero. Future
`CANCELLED_AGREED` handling is outside v1. Every persisted and displayed
percentage names its included/excluded statuses. Stored ratios remain uncapped;
a later presentation may cap only its visual fill.

### Proposed `WeeklyEvaluation` contract

The name and physical schema are `PROPOSED`; the evaluator must expose these
logical facts:

| Group | Fields |
|---|---|
| Identity/version | plan id and revision, athlete-local week start/end and timezone, evaluation-rule version |
| Classification | matched, missed, and future cancelled counts plus references (no `extra` in v1) |
| Denominators | explicitly named completion/adherence denominators and exclusions |
| Volume | planned and actual duration/distance by discipline; matched-only and all-eligible-actual totals kept separate |
| Intent/output | pace/power coverage and adherence, intensity-intent verdict counts, HR soft-flag counts, count of sessions carrying `variance_flag` and its coaching-note trigger (see `docs/decisions/locked.md`) |
| Evidence quality | missing-data coverage, HR/source quality, duplicate exclusions |
| Interpretation | positive efficiency evidence and factual observations by discipline; per-discipline efficiency factor (output divided by average HR), saved for future week-over-week comparison; weekly volume range status, per discipline and blended, the blended status gates `THRIVING` (see `docs/decisions/locked.md`) |
| Handoff | deterministic suggested signal, signal reasons, and rule version |

### Deterministic signal rule table

Status: `DECIDED` (2026-09-10), see `docs/decisions/locked.md`, "Signal
scoring, THRIVING, and volume status" for the full mechanism and every
number; this table restates only the precedence, not the formula, to avoid
the two documents drifting apart.

| Priority | Condition | Suggested signal |
|---:|---|---|
| 1 | Comparable metric/effort coverage is below the minimum-evidence bar | `INSUFFICIENT_EVIDENCE` |
| 2 | `density` (sum of session point values / matched-comparable count) is `+0.6` or higher | `BACK_OFF` |
| 3 | `density` is `+0.3` up to `+0.6` | `WATCH_EFFORT` |
| 4 | `density` is below `+0.3`, and the `THRIVING` gate (zero `OVERCOOKED`, zero `BELOW_EXPECTED_OUTPUT`, zero `variance_flag`, blended volume status not `BELOW_RANGE`, 1+ positive-efficiency session) is met | `THRIVING` |
| 5 | `density` is below `+0.3` and the `THRIVING` gate is not met | `ON_TRACK` |

`THRIVING` is reached only through row 5's territory, it can never fire
independently of a below-`+0.3` density (locked 2026-09-10, resolving the
"does `THRIVING` require `ON_TRACK`" question raised during artifact
review). The implementation must evaluate this as a versioned deterministic
rule table and retain every causing fact. V1 does not interpret pain,
safety, diagnoses, or free text. A missed session alone never implies
reduced fitness or `BACK_OFF`.

```mermaid
flowchart LR
    M["Matched comparisons"] --> C["Completion facts<br/>planned denominator"]
    X["Missed sessions"] --> C
    M --> A["Capability facts<br/>matched-only denominator"]
    M --> V["Actual weekly volume<br/>matched sessions only in v1"]
    C --> R["Versioned rule table<br/>DECIDED 2026-09-10"]
    A --> R
    V --> R
    R --> S["Suggested signal + reasons"]
```

The three paths deliberately keep completion, capability, and total volume from
sharing an unnamed denominator.

### Worked weekly example

Assume four planned sessions and the following target-design actuals. The
strength RPE in this example assumes the future actual-effort capture described
above; it cannot be read from today's workout model.

| Planned session | Planned min | Actual min | Outcome |
|---|---:|---:|---|
| Easy run | 40 | 40 | Matched; within intent |
| Moderate ride | 60 | 63 | Matched; overcooked HR/effort flag |
| Easy strength | 30 | 28 | Matched; within recorded RPE intent |
| Steady run | 45 | — | Missed; excluded from capability math |

Deterministic facts:

- Session completion is `3 / 4 = 75%` before any cancellation policy.
- Matched planned minutes are `40 + 60 + 30 = 130`; matched actual minutes are
  `40 + 63 + 28 = 131`, so the uncapped matched-duration ratio is
  `131 / 130 = 100.8%`.
- All planned minutes are `175`; actual minutes linked to planned sessions are
  `131`, a separate completion-volume ratio of `74.9%`.
- Total actual weekly volume is `131 min`, matched sessions only. (Before the
  2026-09-08 revision this example also included an unlinked extra swim,
  which is no longer a possible v1 case.)
- Intensity intent is respected in `2 / 3 = 66.7%` of matched sessions; one has
  an effort flag. The missed run is not in that denominator.

`WATCH_EFFORT` would be a plausible illustrative signal, but assigning it is
not part of the specification until the rule table and safety precedence are
approved. In particular, `BACK_OFF` must not be derived from a missed session
alone.

## Implementation risks and ambiguity rules

Status: `PROPOSED`.

- Missing metrics produce `UNKNOWN` plus a quality flag, not failure and not
  zero.
- Duplicate source records must not produce two matches. Existing baseline
  calculation deduplication is source-specific and should not be assumed to
  solve evaluator identity.
- A re-run must be idempotent, while preserving an auditable response when a
  link or workout is corrected.
- All reads and writes must be athlete-owned. A link must not join another
  athlete's plan or workout.
- Week membership must use the athlete's timezone consistently. The existing
  comparison service queries UTC midnight boundaries, which can misclassify
  edge-of-day workouts for non-UTC athletes; do not copy that behavior without
  an explicit test and decision.

## Unresolved decisions and contradictions

E7 through E11 (signal thresholds/precedence, volume overshoot and the
intent verdict, single-session efficiency evidence, where intensity-intent
adherence belongs, and HR as sole intent arbiter) were the blocking product
decisions gating the weekly aggregate and the per-session comparison. All
five are resolved as of 2026-09-10; see `docs/decisions/locked.md`, "Signal
scoring, THRIVING, and volume status". [Open decisions](../decisions/open.md)
now carries only the low-comparable-metric-coverage sub-question, which was
never part of E7-E11 and does not block either the per-session comparison or
the weekly aggregate as specified.

One architectural tension was resolved by removing the conflicting code
rather than reconciling it, and three more are now resolved by the
2026-09-10 implementation:

1. ~~First-week linking is athlete-explicit (`DESIGNED`), while the built but
   dormant ongoing comparator uses greedy date matching.~~ Resolved 2026-09-08:
   the greedy-date comparator was removed, since it modeled the approach this
   design rejects, not a variant to reconcile with it.
2. ~~The approved immutable evaluator history differs from
   `WeeklyPlanOutcomeRepository.upsert()`, which replaces the same week's dated
   comparison.~~ Resolved 2026-09-10: `FirstWeekEvaluationOutcome` is its own
   append-only, versioned table (migration 0053), not a reuse of
   `WeeklyPlanOutcome`.
3. ~~Positive-efficiency classification needs trustworthy objective effort
   context, but summary HR and cycling power are missing from the current
   evaluator evidence projection.~~ Resolved 2026-09-10:
   `EvaluatorWorkoutEvidence` carries both.
4. ~~Reference HR bands are labeled easy/moderate/hard, while a persisted
   session carries a numeric `rpe_range` rather than that structured
   category.~~ Resolved 2026-09-10: `hr_bands.py`'s
   `resolve_reference_hr_band()` implements the approved deterministic
   mapping; it reads only `rpe_range`, never purpose/guidance prose.

Two repository-root files also contain known stale implementation claims but
are outside this task's docs-only edit boundary: `CLAUDE.md` names a nonexistent
`athlete_fitness_snapshots` history and speaks of an evaluator as current, while
`STATE.md` still describes Apple Health ZIP as an available import path. This
document does not treat either statement as evidence that those features are
built; they should be reconciled in a separately authorized root-doc change.

The next implementation brief is
[First-week evaluator](../briefs/backlog/first-week-evaluator.md). It begins
with a decision gate and does not treat any item in this document as running
behavior. Fitness-state persistence and progression continue in
[Fitness state](fitness-state.md).
