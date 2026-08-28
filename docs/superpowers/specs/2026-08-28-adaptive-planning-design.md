# Adaptive planning: design

Written 2026-08-28, from a brainstorming session against `main` at commit `d2a3e9a`.

Everything in the "What we verified" section was checked against running code or the
live development database, not recalled. Where something is a guess or an opinion it
says so.

This document records the whole discussion. Only sections 3 and 4 are specified in
enough detail to build from. Sections 5 to 8 are decided direction, to be specified
properly when we reach them.

---

## 1. Why this exists

The coach refuses to write a plan for the only real athlete in the system, and the
numbers it would send if it did are wrong in ways that would mislead it. Beyond that,
there is no notion of progress, no feedback, and no way to change a plan once written.

The goal of this work is a coach that gives you a plan, keeps up with you as you get
fitter, and lets you push back on it.

---

## 2. What we verified

These findings drove the decisions. Each one was checked.

### The mobile-only path works, end to end

An athlete who only ever syncs from an iPhone does reach a baseline and a created
plan. The existing test `tests/use_cases/test_mobile_sync_planner_visibility.py`
drives exactly that path and passes. This closes the "unverified" note in
`docs/planner-brainstorm-context.md`.

### The real athlete cannot get a plan

Running the planner's own readiness computation against the live database:

```
gated disciplines : CYCLING, RUNNING, SWIMMING   (Ironman 70.3, three TARGET contexts)
  CYCLING   sessions=2 active_days=2 ready=False
  RUNNING   sessions=2 active_days=2 ready=False
  SWIMMING  sessions=1 active_days=1 ready=False
PlanReadiness.ready = False  ->  kind = "insufficient"
```

Zero baselines and zero plans exist. The record matches.

Two causes. The bar is three sessions and two active days per sport inside the window.
And `PlanReadiness.ready` is `all(...)` (`app/schemas/weekly_plans.py:83`), so the
weakest sport vetoes the whole plan.

### The readiness window is not the one the context doc claimed

The gate uses `planner_window_days`, default 30 (`app/config.py:72`), applied in
`app/services/weekly_planning/service.py:206`. It does not use `fitness_window_days`.

### The gate window and the baseline window are different

The gate looks back 30 days from now. The baseline that then gets frozen looks back
14 days from the athlete's most recent workout
(`app/services/fitness/service.py:139-149`).

An athlete can clear a 30 day check and have their permanent number frozen from a
much thinner 14 day slice.

### The coach is shown both, unlabelled

`recent_evidence` is a full `BaselineCalculation` dump, so it carries its window
bounds. The `baselines` block omits them (`app/services/weekly_planning/evidence.py:72-86`).
The model receives two different session counts for the same sport, with dates on
only one of them.

### The coach is never told which sports are thin

`readiness` is computed and persisted in the evidence snapshot but is not copied into
the prompt context (`app/services/weekly_planning/service.py:299-300`).

### The model is never told the output shape

`method="json_mode"` (`app/integrations/llm/live.py:92`) binds only
`response_format: {"type": "json_object"}`. The schema is used solely to parse the
reply. The model must guess `week_start`, `days`, `date`, `sessions`, `discipline`,
`objective`, `duration_minutes`, `intensity`, `structure` and `rest_note`. None of
those words appear in the prompt.

The same codebase already does this correctly elsewhere: the catalog expansion prompt
sends `schema.model_json_schema()` (`app/workflows/catalog_expansion/nodes.py:47`).

### The planner has never run

The `llm_usage` table holds exactly one row ever, an onboarding clarification. The
weekly planner has never been called against a live model.

### Indoor rides record zero distance and it is treated as real

Both trainer rides carry `distance_meters: 0.0`, not null. Zero is not `None`, so
they count as distance sessions, `MISSING_DISTANCE` never fires, and
`_cycling_metrics` computes `elapsed_speed_kph = 0.0`. The prompt would tell the
coach the athlete rides at zero kilometres per hour.

Confirmed cause: the athlete records those sessions as indoor bike, so the watch
measures heart rate and not distance. Distance genuinely does not apply.

### The phone sends far less than the backend can read

`HealthKitActivityType.syncKey` emits seven values only: `running`, `cycling`,
`swimming`, `hiking`, `traditionalStrengthTraining`, `functionalStrengthTraining`,
`other` (`ios/CoachHealthSync/CoachHealthSync/Models/HealthKitActivityType.swift:36-59`).

The backend adapter handles about twenty (`app/services/activities/adapters/healthkit.py:56-111`),
including trail running, treadmill, indoor cycling, pool swimming and open water
swimming. None of those are reachable.

Anything outside the seven becomes `other`, which maps to `Discipline.OTHER`, which
is never a goal target, so those sessions are invisible to the planner and to every
fitness number.

Indoor versus outdoor and pool versus open water are not part of
`HKWorkoutActivityType` at all. They live in workout metadata
(`HKMetadataKeyIndoorWorkout`, `HKMetadataKeySwimmingLocationType`), which the client
never reads. This is why the athlete's open water target sees zero open water
evidence.

### The phone only ever asks for seven days

There is one call site and it is fixed at minus seven days
(`ios/CoachHealthSync/CoachHealthSync/App/SyncViewModel.swift:146`).

Two consequences. On first pairing only a week of history is collected, however many
years sit in Apple Health. And a gap longer than seven days between app launches
loses those workouts permanently, with no catch-up.

### Heart rate never arrives from the phone

`HealthKitWorkoutPayload` has no heart rate field and forbids extras
(`app/schemas/mobile_sync.py:47-56`). The iOS target contains no reference to heart
rate, resting heart rate, HRV or sleep. It authorizes exactly one read type,
`HKObjectType.workoutType()`.

Heart rate observation rows are created in exactly one place
(`app/repositories/heart_rate_observations.py:108-116`), reached only from the Apple
Health export and TCX adapters.

Confirmed working: `workout.statistics(for:)` returns real values with only
`workoutType()` authorized. The run returned 15017 metres. So reading quantities off
an authorized workout is fine.

### Quality grading already handles the heart rate design choice

Heart rate readings are graded by the time span each one covers
(`app/integrations/apple_health/parser.py:494-502`): an instant reading is
`EXACT_SAMPLE`, up to 60 seconds is `SHORT_INTERVAL`, longer is `COARSE_INTERVAL`.
Only the first two count as reliable (`app/services/fitness/calculator.py:22-25`).

So a single average across a whole workout would be stored and then excluded from
every reliable aggregate. Readings covering 60 seconds or less need no calculator
change at all.

### Plan sessions have no identity

`PlanSession` has no id (`app/schemas/weekly_plans.py:17-24`). The plan is one JSONB
blob. The repository has only `get_for_week` and `create`, with no update
(`app/repositories/weekly_plans.py:22-57`). The Telegram renderer emits plain text
with no numbering and no buttons (`app/bot/messages.py:273-292`).

A day may hold up to three sessions, so the context doc's claim that date plus sport
is enough to pair a plan with an actual workout does not hold.

### The feedback feature is designed and unbuilt

`activity_feedback` exists with effort 1 to 10, an effort label, discomfort flag,
body area, severity, description, mobility done, and a manual average heart rate.
`WorkoutFlowStep` lists all twelve steps of the conversation that would collect it.

No Python code writes to or reads from either. No repository exists. Both tables hold
zero rows.

### Commands are not registered as commands

`/view_weekly_plan` and `/plan_next_week` are not registered as `CommandHandler`s
(`app/bot/router.py:39-54`). They work only as reply-keyboard label text mapped to a
pseudo-command (`app/bot/service.py:154-155`). The text handler filter is
`filters.TEXT & ~filters.COMMAND` (`router.py:57-59`), so typing either literally
reaches no handler at all.

### The athlete's record is richer than the prompt

Race date 2027-07-11, which is 317 days away, about 45 weeks. Age 22, male, 182 cm,
72 kg. Availability text present, limitations declared, 14 equipment rows.

The prompt receives the raw date with no computed weeks and no phase. It never
receives age, sex, height or weight, because `get_athlete_profile_context` returns
only the two free-text fields (`app/repositories/profiles.py:44-54`).

Numbers reach the model in machine units. Running pace arrives as
`elapsed_pace_seconds_per_km: 368.6` rather than "6:09 per km".

### Each week is planned blind

The planner loads the plan for the target week only, to check whether one already
exists. It never reads the previous week's plan. There is no compliance or adherence
concept anywhere in the codebase.

---

## 3. Piece one: get a plan at all

### 3.1 The gate

Replace the per-sport veto with a whole-athlete floor.

**Decided:** an athlete may be planned when, counting all target sports together,
there are at least 3 sessions on at least 2 distinct days inside the planner window.

Per-sport numbers are still calculated, but they no longer decide yes or no. They
classify each sport into one of three states:

| State | Condition |
|---|---|
| `WELL_EVIDENCED` | at least 3 sessions and at least 2 active days |
| `THIN` | at least 1 session, below the above |
| `NONE` | no sessions in the window |

A sport in `THIN` or `NONE` still appears in the plan. It receives a short, easy,
explicitly introductory session. It never blocks the plan.

Rationale: the athlete's race requires all three sports whether or not they have been
trained yet. Refusing to plan a sport because the athlete has not already done it is
backwards for a goal-driven plan. Equipment and health limitations are already in the
prompt, so the coach will not prescribe something impossible.

Both thresholds stay configurable, following the existing settings pattern.

### 3.2 Tell the coach what it does not know

Copy the per-sport evidence state into the prompt context. It is already computed and
already persisted; it is simply not sent.

The fixed instruction must state what each state means and what to do about it: a
`THIN` or `NONE` sport gets a short, easy, introductory session.

**Known risk, stated plainly.** Telling a language model to go easy does not make it
go easy. This needs a test that reads the generated plan and asserts the session for
a thin sport is actually short and easy, not merely a test that a plan came back.

### 3.3 Zero distance

**Backend:** for disciplines where distance is meaningful (running, cycling, hiking,
swimming), treat `distance_meters == 0` as unknown, exactly as `None` is treated. The
stored workout row keeps whatever the phone sent. Only the calculation ignores it.

**Phone:** send nothing rather than zero when distance does not apply.

Effect on the current athlete's cycling: speed becomes unknown instead of 0.0 km/h,
`MISSING_DISTANCE` fires, and confidence falls from 0.62 to about 0.46. Lower and
true.

Once the indoor flag arrives, an indoor session with no distance is a known condition
rather than a data quality problem, and should not be penalised as missing data.

### 3.4 The two windows

Do **not** merge them. They answer different questions.

- "Is this athlete training right now" must end today. That is the gate.
- "What has this athlete shown they can do" should not be discarded because of a
  fortnight off. That is why the baseline anchors on the last workout, and that
  behaviour matters for the file-import path.

Two changes instead:

1. When the planner creates a baseline, it passes the window it just evaluated, so
   the frozen number reflects the evidence that authorized it. The goal-disciplines
   entry point used by file import keeps its own anchoring.
2. Add the window bounds to the `baselines` block in the evidence snapshot, so the
   coach can tell the two sets of numbers apart.

### 3.5 Confidence

One change now. The rest belongs with the fitness model work.

Add a ceiling: with no reliable heart rate anywhere in the window, confidence cannot
exceed 0.6, because effort is entirely unknown. Today the score saturates at 1.0 on
volume alone.

This bumps `CALCULATION_VERSION` to 2. Existing frozen rows keep version 1 and are
not rewritten, which the current design already anticipates.

### 3.6 The phone payload contract

This is the interface between the backend work and the iOS work. It is specified here
so both can proceed independently, and so mocked data is shaped like the real thing.

| Field | Guaranteed | Notes |
|---|---|---|
| `workout_uuid` | yes | unchanged |
| `activity_type` | yes | the real HealthKit type, not one of seven collapsed words |
| `started_at`, `ended_at` | yes | unchanged |
| `duration_seconds` | yes | unchanged |
| `distance_meters` | no | omit when not measured; never send zero as a measurement |
| `calories_kcal` | no | unchanged |
| `is_indoor` | no | from `HKMetadataKeyIndoorWorkout`; absent when the recording app did not set it |
| `swim_location` | no | from `HKMetadataKeySwimmingLocationType`; absent when not set |
| `heart_rate_samples` | no | see below |

**Best effort means best effort.** `is_indoor` and `swim_location` are set by the
recording app. A third-party app or an older watch may omit them. The model must
degrade gracefully when they are absent, not assume they are present.

**Heart rate.** Send readings covering 30 seconds each, not a single average for the
workout.

Reason: readings covering 60 seconds or less are graded reliable by the existing
calculator and flow through the existing observation pipeline with no changes. A
single workout-long average is graded coarse and excluded from every reliable
aggregate, so it would need calculator changes to be useful at all. Thirty second
readings also give time-in-zone later at no extra cost.

Size: a 90 minute run is about 180 readings. A normal daily sync is one to three
workouts. The case to handle is the first sync after pairing, which may carry a year
of backfill.

*This recommendation is technical and was not explicitly confirmed. Flag for review.*

### 3.7 History and backfill

**Phone:** on first pairing, fetch a year rather than seven days. Afterwards, fetch
everything since the last successful sync plus a small overlap, rather than a fixed
seven days.

Re-sending is already safe. Identical payloads return `unchanged` and create no
duplicate rows, so overlap costs nothing.

The sync endpoint accepts at most 50 workouts per request, so a large first sync must
be batched.

---

## 4. The planning workflow

Today: gather, ask the model, save.

Decided: **gather, work things out, ask the model, check the answer, save.**

Both new stages are code. No extra model calls in the normal path.

### 4.1 Steps

1. **Trigger.** The athlete asks for next week's plan.
2. **Gather.** As today, plus two new reads: the previous week's plan, and the
   previous week's actual workouts.
3. **Gate.** Section 3.1. If below the floor, stop and report exactly what is
   missing.
4. **Build the briefing.** Section 4.2. Pure computation, independently testable.
5. **Build the prompt.** Section 4.3.
6. **One model call**, against a schema built for this athlete. Section 4.4.
7. **Check the answer.** Section 4.5.
8. **Repair once if a check fails**, naming only the failing constraint. If it still
   fails, refuse rather than save something wrong.
9. **Save** the plan and the briefing together.

### 4.2 The briefing

One compact object computed entirely from database rows, with no model involved.

- Weeks until the race, and the phase that implies.
- Physical profile: age, sex, weight. Currently never sent.
- Per sport: last week's actuals, the four week average, the trend, and the evidence
  state from 3.1.
- The load range for next week. Section 4.6.
- A summary of what was prescribed last week.
- Availability, limitations, equipment.
- Everything in coaching units. "6:09 per km" and "1h32m", not `368.6` and `5526`.

### 4.3 The prompt

Split in two.

**Fixed instruction.** The coaching rules, what each phase means, what each evidence
state means, the units used, and the output contract. Stable across athletes and
weeks, so it caches well.

**Variable message.** The briefing.

**Plus the schema**, which today is never sent at all. Either as a tool definition
(`method="function_calling"`) or written into the prompt as the catalog expansion code
already does.

### 4.4 Constrain the schema per request

Make bad plans impossible to express rather than checking for them afterwards.

- The sport field offers only this athlete's target sports. Today it accepts all six,
  so hiking may be prescribed to a triathlete.
- Session duration is capped by what the athlete has actually done, rather than the
  blanket 5 to 360 minutes.

A rule the schema enforces cannot be ignored, argued with, or forgotten.

### 4.5 Checks after generation

Things a schema cannot express, because they are relationships rather than shapes.

- Total load falls inside the range from 4.6.
- No `THIN` or `NONE` sport received a `HARD` session.
- The week fits the athlete's stated availability.
- The `malformed` flag from the provider adapter, which today is set and never read.

### 4.6 The load range

**Decided:** code computes a range for next week's total load. The model distributes
it. The schema and the checks keep it inside.

**What "load" means here.** Until heart rate arrives, load is total training
duration, in minutes, summed across all target sports for the week. That is a crude
proxy: an hour easy and an hour hard count the same. It is the only honest measure
available from the data we currently ingest. Once heart rate is flowing, this
definition should be revisited as part of the fitness model work, and the range
recomputed against something intensity-aware.

Five inputs move the range:

| Input | Effect |
|---|---|
| Phase | base builds gradually, taper cuts hard |
| Trend | three consecutive weekly increases in load force an easier week |
| Reported effort | higher than usual for the same work means hold or drop |
| Compliance | six hours prescribed and three done means do not increase |
| Pain | overrides everything, see below |

**Compliance is available now, roughly.** Prescribed hours against actual hours for
the week needs nothing new. Knowing precisely which sessions were skipped needs
session identity (section 7).

**Pain is not a dial, it is a switch.** Reported moderate or severe discomfort cuts
the load and pulls back the affected sport, as a hard rule in code applied before the
model is called. A model asked to be encouraging will find a reason to keep the long
run.

---

## 5. Fitness and progress

### 5.1 The word "baseline" is doing two jobs

**Starting point.** A permanent record of where the athlete began. Never changes.
Used to show progress to the athlete, and for nothing else. This is the existing
immutable row and it stays as it is.

**Current fitness.** The system's present estimate. Must move.

The second already exists in the code, unnamed. Every planner run computes a fresh
calculation over the recent window using the same pure function. That is current
fitness. It is called `recent_evidence` and displayed beside the frozen number with
nothing explaining the difference.

**Decided:** name the two concepts apart. Current fitness is recomputed on demand and
is never held as a mutable row, which removes the mutability problem entirely rather
than solving it. The planner plans from current fitness.

Historical snapshots are a separate thing and already accumulate as a side effect:
every plan saves the evidence snapshot it was built from. Those are immutable records
attached to a plan, not a live value anyone updates. Nothing has ever read them back,
and they are the natural source for a progress chart.

### 5.2 Measuring progress

Volume says what was done, not whether the athlete improved. Someone can train more
and get slower.

Improvement shows up as the same pace costing fewer heartbeats, or the same heart
rate carrying more speed.

Pace exists today. Heart rate does not. So there is currently no honest way to say
whether the athlete is improving, only whether they are doing more. This is the
strongest argument for the heart rate work in section 3.6.

---

## 6. Feedback

The tables and the step enum already exist and are well shaped. Nothing is wired up.

**Decided:** ask after each session, and keep it to one tap.

When a workout syncs, the bot sends one message asking how hard it felt, one to ten,
as buttons. One tap ends it. Further questions only when the number is high or the
athlete reports discomfort, following the existing `WorkoutFlowStep` sequence.

Rationale: effort only means something next to the specific session it belongs to. A
weekly summary loses that, which is most of what makes the signal useful.

**Known risk.** Five prompts a week may become annoying, and if the athlete stops
answering the signal degrades silently. Worth a way to notice that.

**What it buys.** The watch says what happened. Feedback says what it cost. The most
useful pairing is the same session at the same pace and heart rate feeling harder,
which is accumulating fatigue and is the earliest warning available before
overtraining. Heart rate alone usually misses it.

---

## 7. Session identity and mid-week changes

### 7.1 Identity is cheaper than first assumed

The plan does not need breaking into database rows. Each session needs a stable
identifier, and we can stamp those on ourselves after the model replies and before
saving.

The model never generates them, which matters, because models are unreliable at
inventing stable identifiers.

This one change makes a session addressable, and unblocks both mid-week changes and
the tracker.

### 7.2 Most changes never reach the model

- "Skip Thursday" is a deletion.
- "Make Thursday easier" is a fixed transformation: intensity down one step, duration
  down a quarter.
- "Move Thursday to Friday" is a move.
- "I did Tuesday's run on Wednesday" is a move.

All code. Instant, free, and identical every time.

The model is needed only for open-ended requests, for example "I have had a cold for
three days, reshape the rest of my week". One call, only when asked.

### 7.3 Record keeping

**Decided:** keep the original plan untouched and record every change as an entry
saying what changed, when, and why. The current plan is the original with the changes
applied.

The change list is literally the minimal diff the context doc asked for, it answers
"why does my plan look like this", and it matches the provenance-keeping style used
throughout this codebase. Reading the current plan means replaying the changes, so
the result will likely be cached.

### 7.4 A coaching opinion, stated rather than hidden

A missed session is gone. The rest of the week does not get harder to compensate.
Cramming a missed session into the remaining days is how people get injured.

*Not confirmed by the athlete. Flag for review.*

---

## 8. Not yet designed

**The plan versus actual tracker.** Where it lives is unresolved and materially
changes the work. Telegram can only really show text. The iPhone app already has a
list screen and a bearer token, but it only ever sends data and has no GET call to
the backend, so it would need its first one. A web view is the third option.

**The new UI more generally.** Raised and not yet discussed.

---

## 9. Open questions

1. How large a weekly increase in load is allowed before the check objects.
2. Where the phase boundaries sit. More than 16 weeks out as base is a guess.
3. Whether a missed session is simply gone (section 7.4).
4. Whether 30 second heart rate readings are the right granularity (section 3.6).
5. Where the tracker lives, and what the new UI is.

---

## 10. Deliberately out of scope

- Resting heart rate, HRV and sleep. They require new HealthKit read types and a
  further authorization prompt, and none of the decisions above depend on them.
- Normalizing the weekly plan into database rows. Section 7.1 achieves identity
  without it.
- Merging the gate window and the baseline window. Section 3.4 explains why.
- Reworking the confidence score beyond the ceiling in section 3.5.

---

## 11. A cheap experiment worth running first

The planner has never been called. Before building repair logic for failures nobody
has seen, make two real calls with synthetic evidence: one with the prompt as it is
today, one with the schema included. That shows how badly the model guesses the shape,
and how much of section 4.5 is actually needed.
