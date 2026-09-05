    # Task: Update project documentation for the next implementation
    You are working inside the Adaptive Training Coach repository.
    ## Goal
    Review the current code and documentation, then update the documentation so the project is ready to design and implement its **next milestone: the first-week evaluator and its handoff to the future general planner**.
    This is a documentation and planning task only.
    ## Hard boundaries
    - Do not modify application code, tests, schemas, migrations, prompts, or database models.
    - Only edit files under `docs/`.
    - Do not implement the evaluator or general planner.
    - Treat the current code as ground truth.
    - Do not describe proposed functionality as already implemented.
    - Clearly label every feature as:
      - `BUILT`
      - `DESIGNED`
      - `PROPOSED`
      - `OPEN DECISION`
    - Preserve useful existing documentation and remove or merge only genuinely stale or duplicated content.
    - Do not commit or merge anything without asking me.
    ## Step 1: Inspect the repository
    Read:
    - `docs/CLAUDE.md`
    - `docs/roadmap.md`
    - `docs/decisions/locked.md`
    - `docs/decisions/open.md`
    - `docs/design/first-week-planner.md`
    - Any existing evaluator, comparison, fitness-state, general-planner, or architecture docs
    - The relevant implementation needed to verify current behavior, especially:
      - first-week plan persistence
      - workout/screenshot persistence
      - existing comparison logic
      - weekly plan outcomes
      - fitness/baseline storage
      - ongoing weekly planner inputs
    Report any disagreement between the docs and current code before editing.
    ## Step 2: Update the project status
    Update the appropriate documentation to state clearly:
    ### Built
    - First-week planner
    - Baseline tiers and resolved intensity zones
    - Screenshot workout capture
    - Cycling power capture
    - Pace and speed derivation
    - Age-estimated HR zones for information and future evaluation
    - `/zones`
    - Ongoing weekly planner
    - Plan validation, repair, fallback, and generation provenance
    ### Not yet built
    - Explicit linking of a logged workout to a first-week planned session
    - First-week evaluation
    - Per-session comparison insights
    - Weekly aggregate evaluation
    - Fitness snapshots
    - `last_week_feedback`
    - General planner
    - Stage planner
    - CTL/TSS activation and long-term adaptation
    Verify every status against the code.
    ## Step 3: Create or update the evaluator design document
    Create or update:
    `docs/design/first-week-evaluator.md`
    It must explain the proposed flow clearly:
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
    
    

### Matching decision

Document this as locked:

-   First-week sessions have no scheduled dates.
-   The athlete explicitly selects which planned session a logged workout corresponds to.
-   Matching must not rely primarily on nearest date or automatic guessing.
-   The system needs a stable identifier for every planned session.
-   Unlinked workouts are `EXTRA`.
-   Planned sessions without linked workouts are `MISSED`.

### Missed-workout rule

Document carefully:

-   A missed workout must not imply reduced fitness.
-   It remains visible as a missed planned session for factual reporting.
-   It is excluded from pace, power, HR and physiological-capability comparisons.
-   Do not describe “discarded” as deleting data.
-   Completion/adherence reporting must clearly state its denominator. Flag the exact completion-rate definition as an open product decision if it is not yet final.

### Per-session comparison

Specify deterministic calculations for matched sessions:

-   Planned versus actual duration
-   Planned versus actual distance, where relevant
-   Running pace:
    -   internal unit: seconds/km
    -   athlete display: min:sec/km
-   Swimming pace:
    -   internal unit: seconds/100m
    -   athlete display: min:sec/100m
-   Cycling power in watts
-   Cycling speed in km/h as context
-   Actual average/max HR
-   HR versus the approximate age-based effort zone
-   Whether the session respected its intended easy/moderate/hard character Document these interpretations:
-   More distance in the same time while HR remains appropriate: positive aerobic-efficiency evidence.
-   More distance because HR was above the intended effort: `OVERCOOKED` or equivalent soft flag.
-   Pace/power on target but HR unusually high: possible fatigue, environmental effects, or an inaccurate reference zone.
-   A single HR mismatch must never automatically change zones or declare lost fitness. Include worked numeric examples.

### Weekly aggregate

Describe how per-session results aggregate into:

-   matched, missed and extra session counts
-   planned versus actual minutes per discipline
-   comparable-metric adherence
-   intensity-intent adherence
-   HR soft flags
-   weekly volume
-   baseline-verification evidence
-   deterministic suggested signal such as:
    -   `ABSORBED_WELL`
    -   `ON_TRACK`
    -   `WATCH_EFFORT`
    -   `BACK_OFF` The evaluator should produce facts plus a suggested signal. The future planner makes the planning decision.

## Step 4: Document fitness-state history

Create or update: `docs/design/fitness-state.md` Explain and compare:

1.  A single mutable fitness-state row
2.  Append-only weekly fitness snapshots Recommend the simplest structure that supports:

-   current fitness
-   comparison with last week
-   progress over several weeks
-   same pace/power at lower HR
-   higher pace/power at similar HR
-   later CTL/TSS activation Do not invent exact tables without checking the repository. State what information must be persisted and let implementation planning decide whether that requires new tables or extending existing ones.

## Step 5: Clarify the three planner layers

Create or update: `docs/design/planner-architecture.md` Explain these separately:

### General Planner

-   Runs once per goal or after a major re-plan.
-   Divides the goal timeline into phases.
-   Works across running, cycling, swimming and triathlon goals.
-   Phase dates and structural rules should be deterministic.
-   Phase objectives may be templated initially; do not require an LLM in v1 unless justified.

### Stage Planner

-   Runs at the beginning of each phase.
-   Creates a lightweight week-by-week skeleton.
-   Stores weekly focus, progression intent and recovery/deload intent.
-   Does not generate complete workouts.
-   Its skeleton is guidance and can be adjusted after meaningful disruption.

### Weekly Planner

-   Runs each week.
-   Receives current phase, current stage-week intent, athlete constraints, fitness state and previous evaluation.
-   Uses the LLM to design concrete sessions.
-   Deterministic code validates availability, metrics, safety and schema compliance. Clearly distinguish:
-   deterministic calculations and rules
-   LLM-based judgments
-   product decisions that remain open

## Step 6: Update decisions and roadmap

Update:

-   `docs/decisions/locked.md`
-   `docs/decisions/open.md`
-   `docs/roadmap.md` The roadmap should recommend this implementation order:

1.  Stable planned-session identifiers
2.  Workout-to-planned-session linking
3.  Per-session deterministic comparison
4.  Weekly aggregate and manual evaluation trigger
5.  Weekly outcome persistence
6.  Fitness-state snapshots/history
7.  `last_week_feedback` contract
8.  General planner phase generation
9.  Wire the ongoing weekly planner to current phase and feedback
10.  Stage planner skeleton
11.  CTL/TSS and larger adaptation only after sufficient real data exists Keep CTL, vacation recovery and phase re-cutting out of the immediate v1 implementation.

## Step 7: Create the next implementation brief

Create: `docs/briefs/backlog/first-week-evaluator.md` It should be an implementation-ready but not executed brief containing:

-   Goal and non-goals
-   Current implementation discovered from the repo
-   Locked decisions
-   Remaining product questions
-   Proposed architecture
-   Persistence requirements without unnecessary table assumptions
-   TDD task order
-   Tests and acceptance criteria
-   Documentation updates required after implementation
-   Deployment/live-verification checklist Do not resolve genuinely open product decisions without asking me.

## Open questions to present clearly

At minimum, ask me to decide:

1.  Should evaluation be triggered manually by an “Evaluate week” button, automatically, or both?
2.  How should completion rate treat missed but intentionally cancelled sessions?
3.  Should explicit workout-to-session linking happen immediately after screenshot import?
4.  What exact fields belong in `last_week_feedback`?
5.  Should the first fitness snapshot be created after the first-week evaluation or seeded at onboarding and then updated by evaluation?

## Final report

When finished, report:

-   Files created
-   Files updated
-   Stale or contradictory documentation corrected
-   Decisions that remain open
-   Recommended next implementation
-   Confirmation that no application code was changed Stop after documentation and planning. Wait for my approval before implementation.
## Current implementation status (verified against code, 2026-09-05)

Every line below was checked against the code, not assumed from earlier docs. Two corrections from earlier drafts of this file are folded in below: the fitness-snapshot table that does not exist (see NOT YET BUILT), and the ongoing-planner comparison mechanism that does (see BUILT). The one disagreement left unresolved rather than corrected is logged as question 6 in `docs/decisions/open.md`.

### BUILT

- First-week planner: generate → validate → repair (≤2) → deterministic fallback, `generation_source` persisted (`backend/app/services/weekly_planning/service.py`, `validation.py`). See `docs/design/first-week-planner.md` for the full trace.
- Baseline tiers (`UNPREPARED`/`DEVELOPING`/`TRAINED`/`WELL_TRAINED`), computed in code from self-reported volume + recent evidence (`backend/app/services/weekly_planning/tiers.py`).
- Resolved intensity zones for the first week: FTP→power, race→pace, else RPE fallback, computed in code (`backend/app/services/weekly_planning/zones.py`). HR is structurally excluded from this resolution.
- Screenshot workout capture, including average/max power, speed, cadence, and pace extraction, with an explicit ask-the-athlete fallback for HR (`backend/app/services/workout_screenshot/service.py`, `backend/app/integrations/llm/vision.py`).
- Cycling power capture on actuals (`CyclingWorkoutDetails.average_power_watts`/`max_power_watts`, migration `0051_cycling_power`).
- Pace/speed derivation from distance + duration, recomputed at read time across all import sources (`backend/app/schemas/workouts.py` validators).
- Age-estimated HR zones (Tanaka), display/reference-only, never entering plan generation (`backend/app/services/athlete_zones.py`).
- `/zones` command: read-only, plan-independent, no LLM call (`backend/app/bot/service.py:541`).
- Ongoing weekly planner: generates dated sessions from current constraints; validated separately from the first-week rule set (`backend/app/services/weekly_planning/service.py`, `validation.py`).
- Plan validation, repair, fallback, and `generation_source` provenance — applies to both planners (`weekly_training_plans.validation_jsonb`).
- **Weekly plan comparison for the ongoing (dated) planner only** — `compare_week()` (`backend/app/services/weekly_planning/comparison.py`) matches actual workouts to planned sessions by **nearest same-discipline date, greedily**, and persists the result via `WeeklyPlanOutcomeRepository` into the `weekly_plan_outcomes` table (migration `0050_weekly_plan_outcomes`). This is real, shipped, and tested (`backend/tests/unit/test_weekly_planning_comparison.py`) — but it is a *different* mechanism from the explicit-linking approach locked for the first-week evaluator below, and it does not run for first-week plans at all: `compare_finished_week()` returns `None` immediately when the stored plan is a `FirstWeekPlan`.

### NOT YET BUILT

- Stable identifier for an individual planned session. Plans persist as an opaque `plan_jsonb` blob on `WeeklyTrainingPlan`; `PlanSession` carries no session-level id today.
- Explicit linking of a logged workout to a first-week planned session (no UI, no data model, no service method).
- First-week evaluation of any kind — the code path exists only as a deliberate no-op (`compare_finished_week()`, first-week branch).
- Per-session deterministic comparison for the first week specifically (the existing `compare_week()` assumes scheduled dates, which first-week menu sessions do not have).
- Weekly aggregate evaluation and suggested signal (`ABSORBED_WELL`/`ON_TRACK`/`WATCH_EFFORT`/`BACK_OFF` or equivalent) for the first week.
- Fitness snapshot history. **Correction:** earlier drafts of this doc described `athlete_fitness_snapshots` as an existing, populated, append-only table. It does not exist in the schema or migrations. What exists today is `athlete_self_reported_baselines` — one mutable row per athlete (unique constraint on `athlete_id`), replaced wholesale on a goal change, with no history — plus a compute-on-read fitness calculator/service over raw `Workout` rows. There is no persisted week-over-week fitness trend anywhere yet.
- `last_week_feedback` — no such field, contract, or persistence exists yet.
- General planner (phase cutting).
- Stage planner (per-phase weekly skeleton).
- CTL/TSS activation and longer-term tiered adaptation.

### DESIGNED (locked or specified, not yet implemented)

- First-week evaluator flow, matching decision, missed-workout rule, per-session comparison spec — see `docs/design/first-week-evaluator.md`.
- Fitness-state history structure options — see `docs/design/fitness-state.md`.
- Three-planner-layer architecture and division of deterministic vs. LLM responsibility — see `docs/design/planner-architecture.md`.

### PROPOSED / OPEN DECISION

See `docs/decisions/open.md` for the full list, including the reconciliation question between the new first-week evaluator's locked explicit-linking rule and the already-shipped greedy nearest-date matching used by the ongoing planner's `compare_week()`.
