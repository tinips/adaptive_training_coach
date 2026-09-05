# The First-Week Evaluator — Design

Status: **PROPOSED design, not yet implemented.** Nothing described below exists in code today except where explicitly marked `BUILT`. See `docs/CLAUDE.md` → "Current implementation status" for the verified BUILT/NOT YET BUILT split, and `docs/decisions/open.md` for unresolved product questions this doc depends on.

This document is the target design for the next milestone. It does not describe running behavior.

---

## Why the first week needs its own evaluator

The ongoing weekly planner already has a comparison mechanism (`compare_week()` in `backend/app/services/weekly_planning/comparison.py`, **BUILT**) that matches actual workouts to planned sessions by nearest same-discipline date. That works because ongoing weekly plans have scheduled dates per session.

First-week plans do not. They are menus (**BUILT** — see `docs/design/first-week-planner.md`, Step 1: "menu-mode, no scheduling, athlete places sessions"). There is no date to match against, so date-nearest matching cannot apply here even in principle — this is a structural reason the first-week evaluator needs its own matching approach, not a preference. `WeeklyPlanningService.compare_finished_week()` (**BUILT**) already reflects this: it detects a `FirstWeekPlan` and returns `None` immediately, with a comment that comparison "begins once actual session placement/logging is linked in the evaluator." That linking is what this document specifies.

---

## Proposed flow

```text
First-week plan
    ↓
Athlete completes a session
    ↓
Screenshot is extracted and workout is saved
    ↓
Athlete explicitly links the workout to a planned session
    ↓
Per-session deterministic comparison
    ↓
Weekly deterministic aggregation
    ↓
Persist weekly outcome
    ↓
Create/update the fitness-state history
    ↓
Provide last_week_feedback to the future planner
```

Status per stage:

| Stage | Status |
|---|---|
| First-week plan | `BUILT` |
| Screenshot extracted, workout saved | `BUILT` (screenshot capture, pace/speed/power derivation) |
| Athlete explicitly links workout → planned session | `PROPOSED` — no UI, schema, or service exists |
| Per-session deterministic comparison | `PROPOSED` — spec below; no first-week-specific implementation exists (`compare_week()` is date-based and does not apply) |
| Weekly deterministic aggregation | `PROPOSED` — spec below |
| Persist weekly outcome | `DESIGNED` at the mechanism level — `weekly_plan_outcomes` table and `WeeklyPlanOutcomeRepository` are `BUILT` and could plausibly be reused, but nothing writes a first-week outcome today; whether first-week outcomes reuse this table or need a distinct shape is an open question (see `docs/decisions/open.md`) |
| Create/update fitness-state history | `PROPOSED` — see `docs/design/fitness-state.md`; no snapshot table exists |
| `last_week_feedback` to the future planner | `PROPOSED` — no field, contract, or consumer exists; the future planner itself (general planner) is also not built |

---

## Matching decision (LOCKED — target design)

This is locked as the design this milestone builds toward. It is **not** yet true of any running code, and it is deliberately different from the ongoing planner's existing `compare_week()` mechanism (see "Why the first week needs its own evaluator" above and the open reconciliation question in `docs/decisions/open.md`).

- First-week sessions have no scheduled dates (confirmed: menu-mode, athlete-placed).
- The athlete explicitly selects which planned session a logged workout corresponds to.
- Matching must not rely primarily on nearest date or automatic guessing — there usually is no date to guess from, and even where a date exists (the workout's own timestamp), it must not become the primary signal.
- The system needs a stable identifier for every planned session. Today `PlanSession` has none — `WeeklyTrainingPlan` stores the whole plan as one `plan_jsonb` blob with only a plan-level UUID. This identifier is the first implementation dependency (roadmap step 1) — nothing else in this document can be built without it.
- Unlinked workouts are `EXTRA`.
- Planned sessions without linked workouts are `MISSED`.

---

## Missed-workout rule

- A missed workout must not imply reduced fitness. Missing a session says nothing, by itself, about the athlete's physiological state — it is excluded from every capability comparison (pace, power, HR, physiological effort), not averaged in as a zero or a penalty.
- It remains visible as a missed planned session for factual reporting — "discarded" means excluded from comparison math, never deleted from the record or hidden from the athlete's own weekly summary.
- It is excluded from pace, power, HR, and physiological-capability comparisons specifically because there is no actual performance to compare — including a "0" or a worst-case value would fabricate a data point that never happened.
- Completion/adherence reporting must clearly state its denominator (e.g. "3 of 4 planned sessions completed" vs. "75% of planned minutes completed" are different numbers). **The exact completion-rate definition is an open product decision** — see `docs/decisions/open.md`, question 2 (how intentionally-cancelled sessions should be treated) and the denominator question generally.

---

## Per-session comparison (PROPOSED — deterministic spec)

All of the below is pure, deterministic computation over a matched (planned session, logged workout) pair. No LLM call is part of this step, consistent with the project's rate-limit and "code disposes" principles.

### Metrics to compare, where the planned session specifies a target

- Planned vs. actual duration.
- Planned vs. actual distance, where relevant (endurance disciplines only; not strength).
- Running pace:
  - Internal unit: seconds/km (matches the existing convention in `zones.py` and `comparison.py` — no new unit introduced).
  - Athlete display: min:sec/km, via the existing `format_pace_min_sec()` (**BUILT**, `backend/app/services/formatting.py`) — reused, not reinvented.
- Swimming pace:
  - Internal unit: seconds/100m (matches `zones.py`'s existing swim convention).
  - Athlete display: min:sec/100m, via `format_pace_min_sec()`.
- Cycling power in watts. Actuals-side capture already exists (`CyclingWorkoutDetails.average_power_watts`/`max_power_watts`, **BUILT**, migration `0051_cycling_power`) — this closes the loop the first-week-planner doc flagged as the reason that migration was added ("added specifically so smart-trainer/static-bike power can be logged and eventually compared against the FTP-derived power zones").
- Cycling speed in km/h, as context only (not a target the plan prescribes — informational alongside power).
- Actual average/max HR, where present on the workout (from screenshot extraction or the athlete-supplied fallback, both **BUILT**).
- HR vs. the approximate age-based effort zone (`ReferenceHeartRateZones`, **BUILT**, `athlete_zones.py`) — reference comparison only, never a pass/fail target, consistent with HR never being prescribed anywhere in the system.
- Whether the session respected its intended easy/moderate/hard character — derived from the planned session's `intensity` field (RPE range or numeric zone) versus what the actual data implies.

### Interpretation rules

These are deterministic classifications applied to the comparison above, not LLM judgments:

- **More distance in the same time while HR remains appropriate for the intended zone:** positive aerobic-efficiency evidence. (Same pace/power, lower-than-expected HR is the same signal read the other way — both indicate the athlete is more capable than the reference zone assumed, not that the plan was wrong.)
- **More distance because HR was above the intended effort zone:** `OVERCOOKED` (or equivalent soft flag) — the athlete did more, but by working harder than the session's intent called for, which breaks the plan's structure rather than demonstrating fitness.
- **Pace/power on target but HR unusually high:** flagged as ambiguous — possible fatigue, heat, illness, or an inaccurate age-based reference zone. This is exactly why HR is a soft flag and never auto-adjusts anything: the system cannot distinguish these causes from data alone.
- **A single HR mismatch must never automatically change zones or declare lost fitness.** This holds for the same reason it's locked project-wide in `docs/decisions/locked.md`: HR is a pattern signal across multiple sessions, not a one-shot trigger.

### Worked numeric examples

**Example 1 — aerobic efficiency (positive signal).**
Planned: easy run, zone 277–315 s/km (per the first-week zone resolver's ×1.10/1.25 band off a 252 s/km threshold pace, i.e. a 10k-in-42:00 athlete — see `docs/design/first-week-planner.md` Step 2), 40 min.
Actual: 8.5 km in 40 min (2400 s) → actual pace = 2400 / 8.5 ≈ 282 s/km — within the easy zone (faster end of it, i.e. more distance, same duration).
Actual avg HR: 128 bpm, against a reference easy-zone ceiling (Tanaka, 60–75% of max HR) of ~135 bpm for this athlete.
→ More distance, same duration, HR still within the intended easy zone → **positive aerobic-efficiency evidence.**

**Example 2 — overcooked (soft flag).**
Same plan: easy run, zone 277–315 s/km, 40 min, reference easy-zone ceiling ~135 bpm.
Actual: 9.0 km in 40 min → actual pace ≈ 267 s/km — faster than the easy zone's fast bound (315→277; 267 is outside/faster than 277).
Actual avg HR: 152 bpm — above the easy-zone ceiling, into the reference moderate/hard band.
→ More distance, but achieved by running faster than the easy zone allows and at a HR consistent with harder effort → **`OVERCOOKED`**: the athlete broke the easy day's intended structure rather than demonstrating fitness gain.

**Example 3 — ambiguous fatigue signal.**
Planned: easy run, zone 277–315 s/km, 40 min.
Actual: 8.0 km in 40 min → actual pace = 300 s/km — squarely inside the easy zone, on target.
Actual avg HR: 149 bpm — well above the ~135 bpm reference easy ceiling, despite pace being correct.
→ Pace on target, HR unusually high → flagged as ambiguous (possible fatigue, heat, illness, or an inaccurate age-based reference zone) — **not** auto-escalated, **not** used alone to change zones or declare lost fitness. Logged as a soft flag for pattern tracking across future sessions.

*(Note: these examples use illustrative numbers to demonstrate the classification logic. Exact zone-boundary math should be re-derived from `zones.py`'s real formulas for a given athlete's actual baseline when this is implemented and tested — do not treat the numbers above as fixtures.)*

---

## Weekly aggregate (PROPOSED — deterministic spec)

Aggregated once per completed first week, from the set of per-session comparisons above:

- Matched, missed, and extra session counts (per the matching decision's `MISSED`/`EXTRA` labels).
- Planned vs. actual minutes per discipline.
- Comparable-metric adherence (duration/distance-based, computed only over matched sessions — missed sessions excluded from the denominator per the missed-workout rule, exact denominator definition still open).
- Intensity-intent adherence (how many matched sessions respected their planned easy/moderate/hard character vs. were flagged `OVERCOOKED` or similar).
- HR soft flags — count and pattern (a single flag is informational; a repeated pattern across the week is the signal worth surfacing).
- Weekly volume (total minutes/distance actually completed, independent of adherence to plan).
- Baseline-verification evidence — whether this week's actuals corroborate or contradict the self-reported baseline tier (`UNPREPARED`/`DEVELOPING`/`TRAINED`/`WELL_TRAINED`) used to generate the plan.
- A deterministic suggested signal, computed by a rule table (not an LLM call, consistent with the project's locked evaluator rule and rate-limit principle):
  - `ABSORBED_WELL`
  - `ON_TRACK`
  - `WATCH_EFFORT`
  - `BACK_OFF`

The evaluator produces facts plus this suggested signal. It does not make the planning decision — the future planner (general planner / ongoing weekly planner reading `last_week_feedback`) makes the final call, per the project's locked "evaluator emits facts, planner decides" rule.

The exact rule table mapping aggregate facts → signal is not specified here — it is implementation work, to be designed test-first per `docs/briefs/backlog/first-week-evaluator.md`.

---

## What this document does not decide

- The exact persisted shape of a first-week outcome (reuse `weekly_plan_outcomes`, or a distinct table/shape) — open, see `docs/decisions/open.md`.
- Whether evaluation is triggered manually, automatically, or both — open.
- The exact field list for `last_week_feedback` — open.
- Whether the first fitness snapshot is seeded at onboarding or created only after this evaluation — open, see `docs/design/fitness-state.md`.

None of these are resolved here. They are listed in `docs/decisions/open.md` for explicit sign-off before implementation.
