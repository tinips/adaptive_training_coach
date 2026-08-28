# A deterministic bot: design

Written 2026-08-28. Verified against `main` at commit `68ef4e6` and the live
development database.

Companion to `2026-08-28-adaptive-planning-design.md`. That document makes the
planner work. This one removes everything else that calls a model.

---

## 1. Why

The product has five separate LLM surfaces. Four of them serve onboarding, and one
generates the weekly plan. The plan is the part with actual value: it is the coaching.
The other four turn a form into a conversation, at the cost of roughly 2,300 lines of
workflow code, four more ways to fail, and a bill on every onboarding step.

**Decided: the weekly planner is the only model call left in the product.**

At the same time the goal catalog narrows to swimming, cycling and running, which is
what the athlete actually trains and what the planner can actually plan.

---

## 2. What we verified

### Catalog expansion has never run

`goal_templates` holds 23 rows and `training_contexts` holds 22. Every single one is
`source = SEEDED`. The LLM has never invented a goal or a context in any environment.

The workflow is reachable (`app/services/training_catalog/service.py`) but has never
produced anything. Deleting it removes a subsystem that has never done work.

### The conversation layer is already optional

`app/bot/service.py:98` declares `agent_workspace: TelegramAgentWorkspace | None = None`,
and line 205 reads:

```python
        if self._agent_workspace is None:
            return await self._dispatch(identity, event_type, content)
```

So a fully working path with the orchestrator absent already exists and is already
exercised by the tests that construct the service without one.

It is also only reachable in one narrow case: free text, from an athlete who has
finished onboarding, that is neither a command nor a keyboard label nor a profile
settings edit. Onboarding never touches it (`bot/service.py:182-186` routes
onboarding straight to `_dispatch`).

When it is gone, that text lands on `handle_text`, which already exists.

### Context extraction only validates; the text is already stored verbatim

`app/services/onboarding/service.py:3052-3056` writes the athlete's availability and
health text exactly as typed. Its own comment says so:

> The original text is retained exactly; the workflow result is only a go/no-go
> validation signal.

So the `onboarding_context` workflow is a 222-line LLM call that answers "is this a
sensible answer?" and changes nothing else. Removing it costs a validation step, not
data.

### There is no existing goal menu

`goal_input_keyboard()` (`app/bot/keyboards.py:111`) contains one button: Cancel. The
goal step is free text only.

The `ob:v1:goal:choice:` callback at `bot/service.py:442` looks like a menu and is
not. It handles `choose_goal_clarification`, which is the branch taken when the model
asked a clarifying question and offered options. It is part of the LLM flow, not an
alternative to it.

So replacing goal extraction means building a menu that does not exist today. This is
the one part of this work that is not deletion.

### Supporting goals never reach the plan

The planner resolves disciplines through `_primary_target_contexts`
(`app/services/weekly_planning/service.py:424-443`). It reads the **primary** goal,
keeps only its **TARGET** contexts, and returns those. `supporting_goal_template_id`
is never read.

The baseline service does the opposite. `_goal_disciplines`
(`app/services/fitness/service.py:200-208`) includes supporting goals, so baselines
can be created for a discipline that no plan will ever mention.

Consequence: an athlete who selects strength maintenance as a support today gets it
recorded, gets fitness numbers built for it, and never receives a strength session.

### The catalog is already mostly right

18 primary goals. 14 are swim, bike, run or triathlon. Four are not: `GENERAL_HIKING`,
`GENERAL_STRENGTH`, `HYROX`, `OBSTACLE_RACE`.

5 supporting goals: `IMPROVE_RUNNING`, `IMPROVE_CYCLING`, `IMPROVE_SWIMMING`,
`MUSCLE_RETENTION`, `STRENGTH_MAINTENANCE`.

Seeded from `app/training_catalog_seed.py` (834 lines) and the migrations that call
it.

---

## 3. Decisions

### 3.1 The catalog

**Keep all fourteen** swim, bike and run primary goals. A marathon runner, a
club cyclist, an open-water swimmer and a triathlete are all first-class. This
preserves the requirement in section 1.1 of the planning design.

**Remove four primary goals:** `GENERAL_HIKING`, `GENERAL_STRENGTH`, `HYROX`,
`OBSTACLE_RACE`, along with the training contexts used only by them.

**Keep all five supporting goals**, including both strength ones. Strength work
alongside endurance training is normal and wanted.

`GENERAL_STRENGTH` goes as a *primary* goal only. Strength remains available as a
support through `MUSCLE_RETENTION` and `STRENGTH_MAINTENANCE`, which already exist,
so no new catalog entry is needed.

### 3.2 Ingestion keeps every discipline

**Do not touch the `Discipline` enum, and do not touch the `ck_workouts_workout_discipline`
check constraint.**

Goals and recorded training are different things. The watch records whatever the
athlete does, and anything the app cannot classify becomes `OTHER` and is stored but
ignored by the planner. That is correct behaviour. Removing `HIKING`, `STRENGTH` or
`OTHER` from the enum would leave the sync endpoint with nowhere to put an
unrecognised workout, so it would reject it and the training would be lost.

Narrowing the catalog narrows **what can be planned**, never **what can be recorded**.

### 3.3 Supporting goals reach the planner

`_primary_target_contexts` also returns the supporting goal's contexts, marked as
supporting rather than target. The planner gates and plans them like any other
discipline, and the prompt is told which are which so it treats a support as
secondary.

This composes with the evidence states from the planning design: a supporting
discipline will usually be `THIN`, so it already receives one short easy session
without any extra rule.

It also removes the existing inconsistency where the baseline service and the planner
disagreed about which disciplines matter.

### 3.4 Goal selection becomes a two-level menu

Fourteen buttons in one list is unusable on a phone. Two levels:

| First choice | Then |
|---|---|
| Running | General running, 5K, 10K, half marathon, marathon, trail race |
| Cycling | Road cycling event, MTB race |
| Swimming | Open water swim, pool swimming event |
| Triathlon | Sprint, Olympic, half distance, full distance |

Then, separately and optionally: a supporting goal from the five, or none. Then an
optional race date. Then an optional free-text note, **stored verbatim with no model
involved**, which the planner already puts in its prompt.

The menu is built from the catalog at runtime, not hardcoded, so adding a goal stays
a seed change.

### 3.5 Context validation becomes a length check

Availability and health limitations keep their existing steps and keep storing the
athlete's exact words. The model call that judged whether an answer was sensible is
replaced by a bounds check on length.

An athlete who types nonsense gets nonsense in their prompt. That is an acceptable
trade for removing a subsystem, in a product with one user who is also its author.

### 3.6 The conversation layer goes

Delete `app/workflows/telegram_orchestrator/`. Remove the constructor parameter and
the branch in `bot/service.py`. Remove the wiring in `bot/main.py`.

Free text from a finished athlete falls through to `handle_text`, which is what
already happens whenever the workspace is absent.

---

## 4. Scope boundaries

**In scope:** deleting catalog expansion, the orchestrator, goal extraction and
context validation. Pruning the catalog. Building the goal menu. Making supporting
goals reach the planner.

**Out of scope:**

- The `Discipline` enum and the workout check constraint. Section 3.2.
- Anything in `2026-08-28-adaptive-planning-design.md`. The two projects touch
  different files and should not be interleaved.
- The onboarding step machine itself. Consent, birth year, gender, weight, height,
  equipment and history import are already deterministic and stay exactly as they
  are.
- `DeterministicFakeOnboardingModel`. The planner still needs a fake provider in
  tests.

**A correction to an earlier estimate.** This was first described as cutting deeply
into the 3,840-line onboarding service. That was wrong. Most of that file is the
deterministic step machine and it survives. What leaves is the goal extractor calls
at lines 1750, 3260 and 3392, the context workflow, and the code paths that only
existed to handle model clarification and failure.

---

## 5. Expected size

| Change | Lines | Risk |
|---|---|---|
| Delete catalog expansion | ~500 including schemas and service methods | none, never used |
| Delete the orchestrator | ~900 including wiring | low, the absent path already exists |
| Replace context validation | ~250 out, ~10 in | low |
| Prune the catalog | seed edits plus one migration | low |
| Replace goal extraction with a menu | ~850 out, ~250 in | **this is the real work** |
| Supporting goals reach the planner | ~40 changed | low |

---

## 6. Open questions

**None blocking.**

One thing to decide while building 3.4: whether the supporting goal step is offered
to every athlete or only to those whose primary goal is a triathlon. Offering it to
everyone is simpler and a marathon runner may well want strength maintenance too.
Defaulting to everyone unless it proves annoying.
