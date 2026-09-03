# Multi-horizon planning: macrocycle, quality gate, progress evaluation, coaching style

Written 2026-09-03, from a brainstorming session against `main` at commit `09dcd21`.

Companion to `2026-08-28-adaptive-planning-design.md`. That document is the source of
truth for the weekly writer (its section 4), the fitness model (section 5), feedback
(section 6), and mid-week adaptation (section 7). **This document does not repeat or
replace any of that.** It adds one time horizon above the week, one check inside it,
and one athlete-facing preference. Where this document is silent, the 2026-08-28 spec
still governs.

Everything in section 1 was checked against running code, not recalled. Sections 2
onward are design, following the same "decided, not yet implemented" convention as
the document it extends.

---

## 1. Corrections to what we thought was true

Two claims made earlier in this brainstorm, and one claim in the 2026-08-28 spec, do
not match the code as it stands today. Recorded here so the record is right before
building on it.

### The cold-start problem is already solved

Earlier in this conversation, a brand-new athlete with no workout history was
described as blocked by the readiness gate, the way the real athlete was in the
2026-08-28 spec's section 2. That is no longer true.

The gate now opens on **either** real workout evidence (3 sessions on 2 days) **or**
the athlete simply having filled in the self-reported baseline form for at least one
target sport (`app/services/weekly_planning/evidence.py:80-89`). A fourth evidence
state exists beyond the three the 2026-08-28 spec proposed:
`DisciplineEvidenceState.SELF_REPORTED` (`app/domain/enums.py:65-71`). The planner's
fixed instruction already tells the model exactly what to do with it: no `HARD`
intensity, do not exceed the stated recent volume, use duration ranges rather than
exact pace or power (`app/workflows/prompts/weekly_planning.py:28-36`).

So stage "week 1" needed no new design. It is built, and it is more careful than the
three-state version this brainstorm started from.

### Self-reported test numbers already reach the model, uniformly under-trusted

`recent_ftp_watts` (cycling), `recent_400m_seconds` (swimming), and
`recent_race_result` (running) are already optional fields on the self-reported
baseline (`app/schemas/baseline.py`), already collected by the onboarding mini app
(`app/api/routes/baseline_web_app.py`), and already sent to the model inside
`self_reported_baseline` (`app/services/weekly_planning/service.py:499-502`).

No new field is needed for "the athlete can report an FTP test." It exists. What
does not exist is any distinction between a number from a real, recent, measured test
and a guess — both are labelled `SELF_REPORTED` and given the identical instruction
to avoid exact power or pace. Section 3 below is where this is decided, not assumed.

### The feedback tables this brainstorm assumed exist do not

The 2026-08-28 spec's section 6 describes `activity_feedback` and `WorkoutFlowStep` as
"designed and unbuilt, zero rows." Both are gone. `workout_flow_sessions` was dropped
as dead storage in migration `0043_cleanup_dead_storage.py`, and
`athlete_baseline_assessments` was dropped in `0040_remove_workout_baseline_assessments.py`.
Neither held a row worth keeping, so nothing is lost, but anyone building section 6
of that spec now writes a fresh table, not a resurrection.

### Still true, unchanged

Checked again rather than assumed stale: `weekly_training_plans` still carries no
notion of a previous week ever being read (no code path fetches it), the goal's
`event_date` still reaches the model as a raw date with no phase or week-count
computed (`app/services/weekly_planning/service.py:449-451`), and age, sex, height,
and weight still never reach the prompt at all — `profile` is fetched
(`service.py:438`) and used only for availability and health-limitations text.

---

## 2. Scope

**In scope:** a macrocycle layer above the weekly writer, a quality check inside the
weekly generation pipeline, a periodic progress check that decides whether the
macrocycle has gone stale, and an athlete-set coaching-style preference that tunes
all three without touching the hard safety rules.

**Deliberately not re-specified here:** feedback collection, session identity,
mid-week changes, and the fitness/baseline model. The 2026-08-28 spec's sections 5-7
already decide these in enough detail to build from. This document's progress
evaluator (section 6) is designed to work with what exists today — stored plans and
synced workouts — so it does not depend on section 6/7 of that spec being built
first. If they are built later, the progress evaluator gets a better signal
(reported effort, not just completed hours) for free, with no redesign.

---

## 3. Coaching style: a new athlete preference

### 3.1 What it is

One athlete-set choice, `CONSERVATIVE` / `NORMAL` / `DEMANDING`, describing how hard
the athlete wants to be pushed. It tunes numbers already in the design. **It never
disables a safety rule.** The pain-triggered load cut (2026-08-28 spec, section 4.6:
"pain is not a dial, it is a switch") and the volume safety net's existence both hold
at every setting; only where the net sits moves.

### 3.2 Storage

A new nullable column on `athlete_profiles`:

```
coaching_style: coaching_style_enum ('CONSERVATIVE', 'NORMAL', 'DEMANDING')
    NULL, defaulting to 'NORMAL' when absent
```

Same table as `health_limitations_text` and the other profile-level preferences
(`app/db/models.py:285-323`), for the same reason: it describes the athlete, not the
current goal. A new `CoachingStyle` enum in `app/domain/enums.py`, following the
`AthleteGender` pattern already there. New migration, next number after `0046`.

### 3.3 Collection

A new deterministic onboarding step, `COACHING_STYLE_INTAKE`: three buttons, no free
text, no model involved — the same shape as every other onboarding choice since the
deterministic-bot rework. Placed after `HEALTH_LIMITATIONS_INTAKE` and before
`BASELINE_INTAKE`, so the athlete has already stated goal and limitations, and the
choice can carry copy like "how hard do you want to be pushed on \<their goal\>."
Editable afterward through the existing profile-settings mini-flows
(`app/services/onboarding/service.py` already has this pattern for other fields).

**Default for an athlete who never sees this step** (anyone onboarded before this
ships): `NORMAL`. No backfill migration needed since the column is nullable and every
read site treats `NULL` as `NORMAL`.

### 3.4 What it tunes, and where

| Parameter | CONSERVATIVE | NORMAL | DEMANDING | Lives in |
|---|---|---|---|---|
| Volume safety-net ceiling (2026-08-28 §4.6) | +30% over last week's actual | +50% | +70% | Weekly writer's post-generation check |
| Deload cadence the macrocycle plans for | every 3 weeks | every 4 weeks | every 5 weeks | Macrocycle agent's fixed instruction |
| Framing for `THIN`/`SELF_REPORTED` sports | "progress very gradually" | "progress steadily" | "progress steadily, a slightly faster introduction is acceptable" | Weekly writer's fixed instruction, one inserted clause |

These numbers are an opinion, not a coached one, exactly like the phase boundaries in
the 2026-08-28 spec's section 4.10 — kept in one place, in configuration, expected to
be revisited once seen in practice. **Never overridden by style:** the pain cut, the
requirement that a `THIN`/`NONE`/`SELF_REPORTED` sport gets no `HARD` session, and the
existence of the safety net itself.

---

## 4. The macrocycle agent

### 4.1 What it answers

"What should the next block of training build toward, in what order, for this
athlete." Not a week. The phase arc plus which discipline gets priority when there is
more than one.

### 4.2 Trigger — precise condition

Checked once, at the top of every `generate_next_week` call, immediately after
`build_plan_readiness` already runs (no new evidence computation needed, it reuses
the `readiness.disciplines` rows already produced at
`app/services/weekly_planning/service.py:388-395`):

> Generate a new macrocycle when no live one exists for this athlete's current goal,
> **and** at least one row in `readiness.disciplines` has
> `state in (WELL_EVIDENCED, THIN)` — real workout evidence, not `SELF_REPORTED`.

Deliberately not "all disciplines." A triathlete's weakest sport may stay thin for
months (the 2026-08-28 spec's section 2 records this happening for the one real
athlete); the arc should not wait on it. This mirrors the same "don't let the weakest
sport veto" rule already governing the weekly gate.

**A goal change invalidates the current macrocycle.** Mirrors the existing
`goal_signature` check already used for the self-reported baseline
(`service.py:392-394`): if the stored macrocycle's goal signature does not match the
athlete's current goal, treat it as absent and regenerate.

### 4.3 Storage — new table

```
athlete_macrocycles
  id                 uuid, pk
  athlete_id         uuid, fk -> users.id, cascade delete
  goal_signature      text        -- same signature function as athlete_self_reported_baselines
  phase_boundaries_jsonb jsonb    -- e.g. [{"phase": "BASE", "weeks_from_race": [19, null]}, ...]
  priority_discipline enum or null (Discipline)
  priority_rationale  text        -- one sentence, model-authored
  input_digest        char(64)    -- sha256 of the context sent, same pattern as weekly_training_plans
  prompt_version      int
  model               text, nullable
  created_at          timestamptz
  superseded_at       timestamptz, nullable   -- set by the progress evaluator (section 6) or a goal change
```

One live row per athlete at a time (`superseded_at IS NULL`), same
supersede-rather-than-overwrite pattern as `weekly_training_plans.superseded_at`.

### 4.4 Prompt input

One JSON message, built entirely from data already in the database:

```
{
  "goal": { "main_goal", "event_date", "weeks_to_race": <computed in code>,
            "target_contexts": [...] },   // same shape already sent to the weekly writer
  "profile": { "age": <computed from birth_year>, "gender", "weight_kg", "height_cm" },
  "self_reported_baseline": { ... },      // same object the weekly writer already receives
  "evidence_so_far": { <discipline>: { state, session_count, active_day_count } },
  "coaching_style": "CONSERVATIVE" | "NORMAL" | "DEMANDING"
}
```

`weeks_to_race` and `age` are computed in code before the call, the same way phase
was always meant to be pure arithmetic (2026-08-28 spec, section 4.9) — this is the
first place either number is actually computed anywhere in the codebase. Age, sex,
weight, and height reach a model call for the first time here, closing the gap noted
in section 1.

### 4.5 Fixed instruction, content

Not the weekly writer's rules. A different, smaller set:

- Phase structure and boundaries (2026-08-28 spec, section 4.10), including the
  `GENERAL` phase for no race date.
- Deload cadence, per the coaching-style table in section 3.4.
- Polarized training as the default split (roughly 80% easy, 20% hard) across the
  arc — new content, not in either existing spec.
- For more than one target discipline: name the one furthest behind as priority,
  using `evidence_so_far` and `self_reported_baseline` (an athlete with `NONE` or the
  weakest self-reported volume in one sport is the natural candidate), and state in
  one sentence why. For exactly one target discipline, `priority_discipline` is that
  discipline trivially and the rationale says so in one line — never omitted, never
  worded as if a choice was made among several.
- Triathlon-specific: mention interference (a hard bike degrades the run that
  follows it) as something the *weekly* writer must manage, since the macrocycle
  does not sequence individual days.

### 4.6 Output schema

```
MacrocyclePlan:
  phase_boundaries: list[{ phase: Phase, weeks_from_race_start: int | None,
                            weeks_from_race_end: int | None }]
  priority_discipline: Discipline | None
  priority_rationale: str (1 sentence, <= 200 chars)
```

Sent via `function_calling`, the same choice already made and measured for the
weekly writer (2026-08-28 spec, section 4.3), for the same reason: the schema must
not drift from the Pydantic model, and it is proven to work against the live
provider.

### 4.7 How the weekly writer consumes it

The weekly writer does not call the macrocycle agent. It reads the live
`athlete_macrocycles` row (falling back to no macrocycle context when none exists
yet, i.e. before the trigger in 4.2 has fired) and resolves the *current* phase in
code, by comparing `week_start` against `phase_boundaries_jsonb` — pure arithmetic,
same as the 2026-08-28 spec's section 4.9 always intended phase to be. The resolved
phase, the priority discipline, and its rationale are added to `prompt_context` as one
new `"macrocycle"` key. This is the only change to the weekly writer's prompt content
in this document.

---

## 5. The quality gate

### 5.1 What it catches that the existing checks cannot

The 2026-08-28 spec's section 4.5 checks are structural and numeric: volume within
the safety net, no `HARD` session for a thin sport, fits stated availability. None of
those can catch a plan whose stated intent contradicts its own numbers, a hard
session placed the day before a key session in the same or a different discipline, or
a week that is technically safe but repeats the same session five days running. Those
need a reading, not a rule.

### 5.2 Where it runs, and the total repair budget

After the existing deterministic checks pass, not before and not instead. Cheap
checks first; the second model call only spends money on a candidate that already
cleared shape and safety.

**One repair attempt total per plan request, not one per kind of check.** Order:
generate, run the deterministic checks, then run the quality gate only if those
passed. Whichever check fails first consumes the single repair (2026-08-28 spec,
section 4 step 8): regenerate once, naming only that failing constraint. The
regenerated candidate is then checked again, deterministic checks first, quality
gate second, exactly as the first attempt was. If anything still fails after that
one repair, refuse rather than spend a second model round-trip chasing it.

### 5.3 Prompt input

The candidate `WeeklyPlan` plus exactly the context the writer already had
(`prompt_context`, unchanged) plus the macrocycle context from section 4.7, so the
reviewer can check the plan against the same intent the writer was given.

### 5.4 Output schema, and the repair loop

```
QualityVerdict:
  passed: bool
  problem_category: Literal["intent_mismatch", "interference",
                             "monotony", "other"] | None
  detail: str | None (<= 200 chars, names the specific day/session)
```

On `passed: false`, this feeds the same repair-once mechanic already decided in the
2026-08-28 spec's section 4 step 8: regenerate once, naming only this failing
constraint, then refuse rather than save something wrong. No new refusal path is
introduced; `problem_category` and `detail` simply become another reason a repair can
name, alongside a failed numeric check.

### 5.5 Storage

One new nullable column on `weekly_training_plans`:

```
quality_review_jsonb  jsonb, nullable   -- the QualityVerdict that passed, or null
                                         -- if the gate has not been enabled yet
```

Nullable rather than a new table: it is one small object per plan, always exactly
one, never queried independently of its plan.

### 5.6 Cost, stated plainly

One extra model call per week, per athlete. Roughly doubles the weekly LLM cost for
plan generation. A cheaper or faster model is a reasonable choice for this call,
since judging a plan is a smaller task than writing one — left as an implementation
choice, not decided here, because it needs a second live-provider comparison the way
`function_calling` vs `json_mode` was measured before being decided (2026-08-28 spec,
section 11).

---

## 6. The progress evaluator

### 6.1 What it answers, and why it must exist

Section 4's macrocycle, once generated, otherwise never updates. If the self-reported
baseline it partly relied on was wrong, or the athlete's real trajectory diverges from
the phase, nothing notices. This is the same staleness problem the weekly drift
proposal already solves one level down (2026-08-28 spec, section 7.4); this is that
mechanism one octave up, on the arc instead of the week.

### 6.2 What it compares, and with what already exists

No new table is required to compute the comparison. Two numbers, both derivable from
data already stored:

- **Adherence.** Sum of `duration_minutes` across `plan_jsonb` for each of the last 3
  completed weeks (`weekly_training_plans` where `superseded_at IS NULL`), against
  actual synced workout duration for the same calendar weeks
  (`workouts.duration_seconds`, already used by the fitness calculator). This needs
  no session identity and no feedback table — a week-level sum is enough to notice a
  pattern, the same coarse level already proposed for the weekly-writer's own briefing
  in the 2026-08-28 spec's section 4.2.
- **Fitness trend for the priority discipline.** The `recent_evidence` calculation
  the fitness service already recomputes on every planner run
  (`app/services/fitness/service.py`), read for the last 3-4 completed weeks and
  compared for direction: flat or falling counts as a signal, rising does not.

### 6.3 Trigger — deterministic first, model second

Runs on the same cadence as weekly generation (once per `generate_next_week` call,
after a macrocycle exists), but the comparison in 6.2 is pure code, not a model call.
It escalates to a model call only when:

> adherence has been below 70% of planned hours for 2 or more consecutive completed
> weeks, **or** the priority discipline's fitness trend has been flat or falling for
> the same stretch.

Both numbers are configurable, following the same house convention as the weekly
drift threshold (2026-08-28 spec, section 7.4: "fifteen is a starting number to be
tuned once it has been seen in practice, not a law"). These two are exactly that:
starting numbers, not measured ones.

**Below that bar, nothing happens and nothing is sent to the athlete.** Same "propose,
never impose" governance already decided for weekly drift (2026-08-28 spec, section
7.4) applies here without change: this check may flag the macrocycle as needing
attention, but it never changes the plan or the athlete's week on its own.

### 6.4 Prompt input and output, when it fires

Input: the two computed signals from 6.2, the current macrocycle's `phase_boundaries`
and `priority_discipline`, and the goal. Output:

```
ProgressVerdict:
  needs_macrocycle_update: bool
  proposal_text: str (<= 300 chars, athlete-facing, e.g. "Training has been lighter
      than planned the last two weeks — want me to ease the next block instead of
      pushing ahead?")
  reason: Literal["adherence", "fitness_trend", "both"]
```

`needs_macrocycle_update: true` sets `athlete_macrocycles.superseded_at` for the
current row, so the trigger in 4.2 fires again on the athlete's next plan request. It
does not regenerate the macrocycle immediately or silently — the athlete sees
`proposal_text` first, following the same accept-or-refuse shape already used for
weekly drift proposals (2026-08-28 spec, section 8.1).

### 6.5 Storage

```
athlete_progress_checks
  id                uuid, pk
  athlete_id        uuid, fk -> users.id, cascade delete
  checked_at        timestamptz
  window_weeks_jsonb jsonb    -- the week_start values the comparison covered
  adherence_ratio    float, nullable
  fitness_trend      enum ('RISING', 'FLAT', 'FALLING'), nullable, per priority discipline
  escalated          bool
  proposal_text      text, nullable
  athlete_response   enum ('ACCEPTED', 'DECLINED', 'PENDING'), nullable
```

Kept as a history, not a single mutable row, for the same reason evidence snapshots
are kept on every plan (2026-08-28 spec, section 5.1): nothing else in this design
reads it back yet, but it is the natural source for showing an athlete their own
trajectory later, and it costs one row per check.

---

## 7. Open questions

**None blocking**, but two are explicitly deferred rather than decided:

| Question | Status |
|---|---|
| Should a *measured, recent* self-reported test (a real FTP test dated last month) be trusted sooner than a guess, ahead of real workout evidence? | Deferred. Section 1 names this as a real decision; this document does not resolve it. Today's uniform caution stays as-is unless decided otherwise. |
| Should `coaching_style` live on `training_goals` instead of `athlete_profiles`, so it can vary per goal? | Deferred. Placed on the profile because there is currently one goal per athlete (`training_goals` has a unique constraint on `user_id`), so the two are equivalent today. Revisit only if multiple concurrent goals are ever supported. |

**Known unknown:** the quality gate's cost-saving option (a cheaper model for review
than for writing) has not been measured against the live provider. Treat it as an
implementation-time decision, verified the same way `function_calling` was verified
in the 2026-08-28 spec's section 11, not assumed here.

---

## 8. Deliberately out of scope

- Feedback collection, session identity, and mid-week changes. Already specified in
  the 2026-08-28 spec's sections 6-7; this document does not touch them.
- A trust tier for measured versus guessed self-reported numbers (section 7).
- Per-goal coaching style (section 7).
- Choosing the quality gate's model. Left to implementation, per section 5.6.
- Anything about the interface (Telegram vs. mini app) for showing macrocycle or
  progress-check content to the athlete. The existing interface decisions in the
  2026-08-28 spec's section 8 apply unchanged; a proposal here is just another
  message using the same mechanism as a drift proposal.
