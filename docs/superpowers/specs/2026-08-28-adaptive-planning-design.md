# Adaptive planning: design

Written 2026-08-28, from a brainstorming session against `main` at commit `d2a3e9a`.

Everything in the "What we verified" section was checked against running code or the
live development database, not recalled. Where something is a guess or an opinion it
says so.

This document records the whole discussion. Only sections 3 and 4 are specified in
enough detail to build from. Sections 5 to 8 are decided direction, to be specified
properly when we reach them.

**This is the design, not the implementation plan.** It says what should be true and
why. The ordered, task-by-task plan for building it is a separate artifact and does
not exist yet. Section 11 should be settled before that plan is written, because its
outcome changes sections 4.3, 4.4 and 4.5.

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
2. **Gather.** As today, plus the previous week's plan and its stated intent, the
   previous week's actual workouts, and the effort scores and discomfort reported
   against them.
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
- A summary of what was prescribed last week, **including the intent the previous
  plan stated for itself** (section 4.7).
- Reported effort scores for last week's sessions, and any reported discomfort.
- How much of last week was actually completed, as planned hours against actual
  hours.
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

- The volume safety net from 4.6. Not a target range, only the dangerous extremes.
- No `THIN` or `NONE` sport received a `HARD` session.
- The week fits the athlete's stated availability.
- The `malformed` flag from the provider adapter, which today is set and never read.

### 4.6 Volume: the model decides, code catches danger

**Decided:** the model decides how much training next week holds. Code does not
compute a target range.

This reverses an earlier decision in the same session and the reversal was
deliberate. The first design had code computing a range that the model distributed.
That sat awkwardly against the rest of section 4, where the model decides and code
checks afterwards. It also failed on its own terms: told that a fixed percentage rule
was too mechanical, the natural next move was a more complicated rule with week roles
and sequences, and that would have been brittle in the same way. Periodisation is
judgement, not arithmetic.

Concretely, a low week means opposite things depending on why it was low. Three hours
because a recovery week was planned is not three hours because of illness. Holding
volume flat for two weeks so the third can step up is a deliberate choice that no
volume-history rule can express. The model can reason about all of that, given the
context. A rule cannot, without becoming a coaching engine in its own right.

**What "load" means here.** Until heart rate arrives, load is total training duration
in minutes, summed across all target sports for the week. That is a crude proxy: an
easy hour and a hard hour count the same. It is the only honest measure available
from the data we currently ingest. Revisit it with the fitness model work.

**The safety net.** A net, not a leash. It fires only when something has clearly gone
wrong, and everything between the bounds belongs to the model.

- Reject an increase beyond roughly half again on the previous week's actual load.
- Reject any increase at all when moderate or severe discomfort was reported.

**Pain is not a dial, it is a switch.** Reported moderate or severe discomfort cuts
the load and pulls back the affected sport, as a hard rule in code applied before the
model is ever called. A model asked to be encouraging will find a reason to keep the
long run.

This applies to **generating next week only.** What pain may and may not do to a plan
already running is section 7.7, and the answer there is different.

**Compliance is available now, roughly.** Prescribed hours against actual hours for
the week needs nothing new. Knowing precisely which sessions were skipped needs
session identity (section 7).

### 4.7 The plan states its own intent

Every plan records, in one sentence, what its week was for. "Holding volume so the
step up lands next week." "First week back after illness, deliberately light."

The model writes it as part of the plan. Code does not compute it.

Three reasons this matters:

1. Without it, next week's call sees two identical weeks and no reason for them, so
   it cannot reason across weeks and starts fresh every Monday.
2. It gives the athlete something readable when a plan looks strange.
3. It documents the model's own reasoning, which makes a bad plan diagnosable.

The stated intent goes into the following week's briefing (section 4.2).

### 4.8 Turn reasoning on, for this call only

DeepSeek is currently called with thinking explicitly disabled
(`app/integrations/llm/live.py:66`).

That was reasonable when the model was distributing a number handed to it. It is the
wrong setting now that the model owns the periodisation judgement.

The cost argument does not apply here. The constraint was not paying for a full
re-plan on every nudge, and that still holds, because nudges are code (section 7.2).
This is one call per athlete per week.

**Decided:** enable reasoning for the weekly planner call. Leave it disabled
everywhere else, including onboarding.

**This is not a settings change. The adapter cannot currently express it.**

`OpenAICompatibleOnboardingModel` builds one `ChatOpenAI` and caches it on
`self._chat_model`, with `thinking: {"type": "disabled"}` baked into the DeepSeek
branch at construction time. One cached instance cannot serve reasoning-on for the
planner and reasoning-off for onboarding.

Worse, there is no channel to tell it which kind of call this is.
`ainvoke_structured` takes `step: OnboardingStep` and then immediately does
`del step`.

So this requires a real adapter change: either cache two instances and select
between them, or pass an explicit per-call flag through `ainvoke_structured`. The
implementation plan must budget for it rather than treating it as a one-line config
edit.

### 4.9 Why phase stays in code while volume does not

These look inconsistent and are not.

Phase is close to pure arithmetic: count the weeks to the race, read off the block.
Keeping it in code makes it stable, so the plan cannot drift between blocks for no
reason, and it is a fact the athlete can check.

Volume is judgement about the specific athlete in the specific week. That is what the
model is for.

The coaching opinion inside the phase calculation is only where the boundaries sit,
which is a small, readable, arguable set of numbers. See open questions.

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

### 5.3 Gaps in training, and how fitness survives them

Section 7.5 says illness should not mark an athlete down. Taken naively that
contradicts 5.1, because a value recomputed from scratch every time cannot be "held".
This section resolves it.

**Decided: excluded periods, not a held value.**

A declared gap is recorded as a period with a reason: ill from 10 to 17 March,
injured from 2 April, and so on. The calculator stays pure and stays a function of
the workouts inside a window. What changes is the window the caller asks for.

When computing current fitness, the planner skips excluded periods and **extends the
window backwards by the same length** to compensate. A 30 day window containing a
7 day illness becomes a 37 calendar day window covering 30 days of actual training
opportunity.

This is chosen over the two alternatives on purpose:

- *Storing an override value* reintroduces exactly the mutable fitness row that 5.1
  exists to avoid.
- *Reading the previous plan's saved snapshot instead of recomputing* leaves the
  planner with two possible sources of truth and needs a rule for choosing between
  them, and the snapshot may be weeks stale.

**What this costs.** One new stored concept: periods of declared unavailability with
a reason and a date range. That is the only new storage in this design, and it earns
its place independently, because "I was ill for a week in March" is exactly the kind
of history a coach wants and the system currently cannot record at all.

**Undeclared gaps are not excluded.** A period is only skipped when the athlete gives
a reason. Silence means the gap counts, which is the conservative direction: it
lowers the estimate rather than flattering it.

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

**One message, not two.** When the same sync also triggers a drift proposal (section
7.4), the effort question and the proposal share a single message.

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

### 7.4 Watching the week

**The governing rule: propose, never impose.** The system never changes a plan on its
own. It notices, it says something, it suggests, and the athlete accepts or refuses.

This is not only a courtesy. It is what makes the whole loop safe. The system cannot
walk an athlete's training downhill on its own, because it cannot change anything
without a tap.

**What gets compared.** Every workout that syncs is compared against what was
planned. Two levels:

| Level | Needs | Purpose |
|---|---|---|
| The week is drifting | nothing new, two sums | triggers a proposal |
| This session went differently | session identity (7.1) | makes the message specific |

The trigger is the week, not the session. Forty-five minutes instead of an hour is
noise. The week tracking a third below plan by Thursday is a signal.

**The threshold.** Projected weekly hours more than **15 percent** away from planned.
Inside that, silence and no change. Outside it, propose. Fifteen is a starting number
to be tuned once it has been seen in practice, not a law.

**Quiet by default.** No message when a session lands on plan. Encouraging for a
week, irritating by the third. The comparison is visible when the athlete goes and
looks at the plan, rather than pushed at them.

**When it runs.** On workout sync, which already happens on app launch and
foreground, so it costs nothing extra. It shares one message with the effort question
from section 6 rather than sending two.

**Accepting a proposal** is a change like any other and is recorded in the change log
(7.3) with its reason.

### 7.5 When training is missed

An earlier draft said a missed session is simply gone and the week does not
compensate. That was rejected, correctly. It treats missing as normal and does
nothing with the information.

**Decided:** a significant miss means the plan no longer matches reality. The right
response is to find out why, and rebuild from where the athlete actually is, rather
than patching a hole.

**The reason is load-bearing, not politeness.** Current fitness is recomputed from
workouts in a rolling window, so a lost week drops it by roughly a quarter of a 30
day window. Real detraining over one week off is far smaller. Without a reason, a
naive recompute punishes illness: the athlete looks unfit, gets an easy week, does
less, and looks less fit again. The plan walks downhill and nobody notices.

The reason decides whether the gap is excluded from the fitness window (section 5.3)
and what happens to the plan:

| Reason | Response |
|---|---|
| Illness | exclude the period from the fitness window, ease back in, do not mark down |
| Injury | exclude the period, and pull that sport back until the athlete says otherwise |
| Travel or work | exclude the period, fitness unchanged, resume |
| Motivation | do **not** exclude. Fitness is intact but the plan was unrealistic, which is a different conversation and should not be hidden by adjusting the window |

Mechanically, "do not mark down" means the period is recorded as excluded and the
fitness window extends past it, per 5.3. Nothing is held, overridden, or frozen.

### 7.6 How the two thresholds relate

Sections 7.4 and 7.5 both fire on training not happening. They are **one escalating
mechanism, not two independent ones.**

| Situation | Response |
|---|---|
| Projected week more than 15% off plan | propose an adjustment to the rest of the week |
| Too little done for the week to be salvageable, suggested at under 60% of planned hours by midweek | do not propose an adjustment. Ask why, and offer to replan from current fitness |

The second supersedes the first. At 40 percent off, the athlete gets the ask-why
conversation, not a tinkering proposal followed by a second message.

### 7.7 Pain: what it may and may not do on its own

Section 4.6 says pain is a hard rule applied in code. Section 7.4 says the system
never changes a plan on its own. Both hold, because they govern different things.

**Generating next week.** Code applies the pain constraint before the model is
called. This is not imposing on anything, it is constraining a plan that does not
exist yet. No conflict.

**The plan already running.** Pain reported on Tuesday does not delete Thursday's
long run. The governing rule stands: nothing changes without a tap.

But it is not treated as ordinary drift either. A pain report:

- sends its proposal immediately, ignoring the 15 percent threshold entirely
- marks the affected sessions as **not recommended, with the reason shown**, if the
  proposal goes unanswered

So an unanswered pain report leaves the plan unchanged but visibly flagged, rather
than silently intact. That respects the athlete's control without the system
pretending everything is fine.

**Silence is not invisibility.** The watch keeps reporting whether the athlete talks
to the bot or not, so a silent week still shows two hours done of six planned. What
silence costs is the reason and the subjective feedback, not the objective data.

The honest response to unexplained is to propose rather than assume, which is exactly
the rule in 7.4. An athlete who never answers ends up with an unchanged plan, which
is the safe failure.

---

## 8. The interface

### 8.1 The split

Most of what this design decides is conversational, and Telegram already does it
well. Inline keyboards are established in `app/bot/keyboards.py` and need no new
surface:

- The effort question after each session, one to ten as buttons (section 6).
- Drift proposals, accept or refuse (section 7.4).
- The reason for missed training: ill, injured, travel, other (section 7.5).
- Skip, easier and move on a single session (section 7.2).

What Telegram cannot do is anything that has to be looked at and compared: the week
at a glance, planned against actual, or whether pace at a given heart rate is
improving over months.

### 8.2 Decided: a Telegram mini app for the visual half

Rejected alternatives, and why:

**A screen in the iPhone app.** Login and a list screen already exist, but every
change costs an Xcode build and install, which is too slow while the design is still
moving, and it only ever works on that one phone.

**Images rendered into the chat.** Cheapest by far and genuinely useful, but the
ceiling is low: you can look and not explore. Nothing exists for image generation
today, so it is not free either.

**A mini app** is designed for exactly this case and the stack already supports it.

### 8.3 Why it is cheaper than a normal web app

`python-telegram-bot` 22.8 is installed and takes a `web_app` parameter on
`InlineKeyboardButton` directly. Opening the app is one button, not an integration.

Telegram hands the page a block of identity data signed with the bot token. The
backend recomputes the signature and knows who the user is with no password, no
OAuth, no session and no login screen. `Settings.telegram_bot_token` is already
available to the API process, since the API and bot containers share one environment
file.

It also works in desktop Telegram, which the iPhone option does not. Reviewing a
training week on a phone screen is cramped.

### 8.4 What has to be built

- A web page. Nothing exists today: no templates, no static files, no frontend. This
  is the single largest new piece of work in this design.
- Signed identity validation on the backend.
- Read routes. The API has four routes in total and two are health checks. There is
  no way to read a plan over HTTP at all.
- A button in the bot that opens it.

### 8.5 Per-message buttons, never a fixed menu button

The backend is reachable only through ngrok, and a free ngrok address changes on
restart. A URL configured once in BotFather breaks every time it rotates. A button
built into each message carries the current address, so it always works.

The deeper problem remains: the mini app only works while the Mac is awake with
Docker and ngrok running. A failed sync retries quietly. A button opening a dead page
is far more visible and more irritating. Worth handling deliberately rather than
discovering.

### 8.6 Build it in three stages

The greenfield risk is real, and a half-finished screen is worse than the text
message it replaced. So, smallest first:

1. **One read-only screen** showing the week, planned against actual. One button, one
   signature check, one read route, one page. This proves the whole path end to end
   and is immediately better than the current wall of text.
2. **Make it interactive.** Tap a session, change it, accept a proposal.
3. **Progress charts**, once there are enough weeks of data for a chart to say
   anything. Realistically a couple of months out.

### 8.7 The Telegram plan view still needs work now

Stage 1 does not remove the need for a readable plan in the chat itself. Two concrete
constraints:

**Message length.** Telegram caps a message at 4096 characters and the renderer
already asserts against it (`app/bot/messages.py`, `_assert_telegram_length`). A
triathlon week with ten or twelve sessions, each with an objective and a structure,
may not fit.

**Buttons attach to messages, not to lines.** A button cannot sit beside Thursday's
second session inside one long message. Either one message per day, which means seven
messages every time the plan is viewed, or a two-step menu: pick a day, pick a
session, choose what to do.

**Decided:** the plan message stays read-only with a single "change something" button
beneath it, opening the two-step menu.

### 8.8 A product shift worth naming

If the mini app becomes where plans are read, the bot stops being the interface and
becomes the notification channel. That is probably the right shape, but it is a
change in what the product is and should be chosen deliberately rather than drifted
into.

---

## 9. Open questions

1. **Where the phase boundaries sit.** Proposed, counting back from race day: race
   week is the last week, taper the two before it, peak the four before that, build
   the twelve before that, base everything earlier. At 45 weeks out that puts the
   current athlete deep in base. This is the common structure, not a coached opinion,
   and a 45 week runway is long enough that a coach would likely split base in two
   and place a smaller race mid-season. Not confirmed.
2. **Whether 30 second heart rate readings are the right granularity** (section 3.6).
   Recommended on mechanical grounds. Not confirmed.
3. **What counts as a significant miss** (section 7.5). A starting suggestion is less
   than 60 percent of planned hours completed by midweek, measured on proportion of
   time rather than session count. Not confirmed.
Settled during the session and no longer open: the volume decision (4.6), the
missed-training model (7.5), the 15 percent drift threshold and quiet-by-default
(7.4), and the interface (section 8).

---

## 10. Deliberately out of scope

- Resting heart rate, HRV and sleep. They require new HealthKit read types and a
  further authorization prompt, and none of the decisions above depend on them.
- Normalizing the weekly plan into database rows. Section 7.1 achieves identity
  without it.
- Merging the gate window and the baseline window. Section 3.4 explains why.
- Reworking the confidence score beyond the ceiling in section 3.5.
- Computing a target volume range in code. Considered and rejected in 4.6. Only the
  safety net remains.
- Encoding week roles (build, hold, recover) as a code-side rule reading the sequence
  of previous weeks. Superseded by the model stating its own intent (4.7).

---

## 11. Settle this before writing the implementation plan

The planner has never been called. Not once, against any model.

So sections 4.3, 4.4 and 4.5 are designed against behaviour nobody has observed.
Writing repair logic for failures that have never been seen is guesswork, and if
DeepSeek turns out to handle `function_calling` badly, or to conform fine without
help, those three sections change and the tasks written from them get rewritten.

**The experiment.** Two real calls with synthetic evidence. One with the prompt
exactly as it is today. One with the schema sent. Compare what comes back.

**What each outcome changes:**

| Result | Consequence |
|---|---|
| Guesses the shape badly today, conforms with the schema sent | 4.3 and 4.4 confirmed as written; 4.5 can be lighter |
| Conforms fine either way | 4.4's per-request schema is still worth it for the sport restriction, but the urgency drops |
| Struggles even with the schema sent | `function_calling` may not suit DeepSeek; reconsider 4.3, and 4.5 becomes load-bearing rather than a safety net |

This is roughly twenty minutes and two API calls. It should happen before the
implementation plan is written, not after.

*Not yet run. Awaiting a decision.*
