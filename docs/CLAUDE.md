> **Historical sprint input / cleanup candidate.** This file is not a canonical
> architecture or status source. Use `docs/README.md` for the documentation map
> and the linked design/decision documents for current behavior. Retain this
> file until a separately approved documentation cleanup archives or deletes it.

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
    
    ```
    

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
## Current implementation status (verified against code, 2026-09-10)

`BUILT` means code exists; reachability is stated separately. `DESIGNED` is a
locked target with no implementation. `PROPOSED` is a recommendation, and
`OPEN DECISION` needs sign-off.

### BUILT and production-wired

- `FirstWeekPlanner`: goal-excluded menu generation, deterministic tiers/zones,
  validation, at most two model repair calls, deterministic fallback,
  persistence, `generation_source`, and Telegram rendering.
- Workout capture through settings-gated screenshot and TCX paths (screenshot
  defaults on; TCX is optional and defaults off). Discipline detail rows can
  store canonical pace/speed, cycling power/cadence, and average/max HR.
  Pace/speed is recomputed by Pydantic validators at construction before
  persistence, not “at read time.” Apple Health job/repository remnants are
  not a live upload path.
- Age-estimated, display-only HR zones and the `/zones` command.
- One mutable self-reported baseline per athlete and deterministic workout
  evidence calculation over a configured recent window (`planner_window_days`,
  default 30 days) on demand. Neither is fitness-state history.

### BUILT but not production-wired

- `OngoingWeeklyPlanner` can generate and validate dated plans in service code,
  but `backend/app/bot/main.py` composes only `FirstWeekPlanner`; there is no
  production transition to ongoing mode.
- `compare_week()`/`compare_finished_week()` (the dated-plan comparator) were
  removed 2026-09-08 as superseded by the first-week evaluator's athlete-
  explicit matching decision below; nothing in current code performs
  nearest-date matching.
- **(2026-09-10)** The first-week evaluator's deterministic core: stable
  session identity (`PlanSession.id`), the athlete-confirmed link
  service/repository (`planned_session_links`), the evaluator evidence
  projection, per-session comparison, missed classification, weekly
  aggregation and signal, and immutable versioned outcome persistence
  (`first_week_evaluation_outcomes`) all exist in
  `backend/app/services/weekly_evaluation/` and are unit-tested and
  live-migration-verified. No Telegram link/evaluate/review UI calls any of
  it yet.

### Important current data gaps

- `FitnessWorkoutEvidence` omits stored cycling power/speed and numeric summary
  HR (unchanged; the evaluator's own `EvaluatorWorkoutEvidence` projection,
  added 2026-09-10, does not have this gap).
- The first-week instructions ask athletes to record RPE and how the session
  felt, but no write model persists either value.
- `ActivitySource.FIT` remains an enum value, but no FIT adapter/import path is
  present in the current repository.
- The no-HR-prescription invariant is enforced at both model-facing weekly
  boundaries: HR intensity and HR target fields are excluded. Wider persisted
  types retain legacy compatibility, and completed-workout HR remains evidence.

### DESIGNED / PROPOSED / OPEN DECISION — no implementation

- The first-week evaluator's Telegram link/evaluate/review UI, its capture-
  confirm block wiring, an integration test, and a regression test (the
  deterministic core itself is built as of 2026-09-10, see above).
- Fitness-state snapshot/history, correction policy, latest/history reads, or
  `last_week_feedback` contract/consumer.
- A production multi-week transition, General Planner, or Stage Planner.
- CTL/TSS activation and longer-term adaptation.

### DESIGNED, PROPOSED, and OPEN DECISION

- `BUILT, DORMANT` (2026-09-10): explicit first-week linking, missed-workout
  classification, and deterministic comparison/aggregation. `DESIGNED`:
  separation of fitness-state roles and the three planner responsibilities.
- `PROPOSED`: hybrid versioned fitness history plus a current derived view/cache;
  the storage approach remains an `OPEN DECISION`.
- `PROPOSED`: specific identifiers, other persistence shapes, result vocabulary, and
  technical decomposition in the evaluator brief.
- `OPEN DECISION`: the authoritative blocking questions are in
  `docs/decisions/open.md`.
