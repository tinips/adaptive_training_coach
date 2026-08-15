# Current Product Flow

## Supported onboarding boundary

The English Telegram onboarding collects a mandatory athlete profile, a
confirmed and canonically classified goal, literal availability, current
equipment and access, and literal training limitations:

```text
Start -> consent -> basic profile -> conversational goal
      -> canonical goal confirmation -> availability
      -> Equipment & access -> training limitations
      -> optional workout-history import or explicit Skip -> complete
```

The product does not generate training plans yet. It prepares the deterministic
goal and execution assessment that a future planner will consume. It does not
provide medical diagnosis.

## Goal interpretation and catalog expansion

The compiled goal workflow receives the complete compact list of active goal
templates. Structured output preserves the athlete's wording while deciding:

```text
primary_template:   USE_EXISTING | CREATE
supporting_template: USE_EXISTING | CREATE | NONE | UNSUPPORTED
```

Known goals reuse the catalog without another model call. For example,
Ironman 70.3 maps to `TRIATHLON_HALF_DISTANCE` and Spartan maps to
`OBSTACLE_RACE`. A secondary priority such as maintaining muscle maps
independently to `MUSCLE_RETENTION`; it is not folded into the primary goal.

The athlete sees both their wording and the canonical interpretation before
confirming. A correction replaces the previous candidate. `UNSUPPORTED`
retains secondary-priority text without creating a reusable relationship.

After confirmation, each still-new primary or supporting template is expanded
synchronously:

```text
all new templates
  -> one structured goal-to-context mapping call
  -> one grouped context-to-capability call, only if contexts are new
  -> strict application validation
  -> PostgreSQL advisory transaction lock
  -> atomic global publication plus TrainingGoal foreign keys
```

The model proposes structured data but never writes PostgreSQL or supplies IDs.
The application rechecks the active catalog before each stage, reuses compatible
rows won by a concurrent request, and rejects incompatible code collisions.
Templates, contexts, capabilities, execution options, their relationships, and
the athlete's goal are committed together. A failed stage clears only the
in-flight marker, preserves the confirmed draft, and allows Continue to retry;
no partial catalog is published.

Generated definitions become globally `ACTIVE` immediately and are reused by
later athletes. `source`, `status`, and `definition_version` support manual
catalog control without an administrative UI in this iteration.

## Durable onboarding sequence

```text
CONSENT
  -> SETUP_INTRODUCTION
  -> PROFILE_BIRTH_YEAR_INTAKE
  -> PROFILE_GENDER_INTAKE
  -> PROFILE_WEIGHT_INTAKE
  -> PROFILE_HEIGHT_INTAKE
  -> GOAL_INTAKE -> clarification -> explicit confirmation/expansion
  -> AVAILABILITY_INTAKE
  -> EQUIPMENT_RECOMMENDATION
  -> EQUIPMENT_INTAKE -> HEALTH_LIMITATIONS_INTAKE
  -> NONE_REPORTED or free text -> TRAINING_HISTORY_IMPORT
  -> successful Apple Health/TCX import or Skip for now -> COMPLETED
```

Availability and training-limitations free text use the compiled stateless
LangGraph validation boundary and are stored literally. Equipment & access
callbacks are fully deterministic and never invoke a model.

## Equipment & access

A goal template maps to planning `training_contexts`; these contexts are more
specific than the broad `Discipline` stored on imported workouts. Each target
context has preferred or substitute execution options, and each option lists
required, recommended, or optional capabilities.

The review combines primary and supporting goal contexts, deduplicates shared
capabilities, and displays only resources relevant to the current goal. Saving
the review marks visible checked resources `AVAILABLE` and visible unchecked
resources `UNAVAILABLE`. An absent row means `UNKNOWN`. Answers belonging only
to other goals are preserved.

The application computes, but does not persist, one assessment per target
context:

- `FEASIBLE`: a preferred execution has every required capability available.
- `FEASIBLE_WITH_SUBSTITUTION`: only a substitute execution is complete.
- `UNKNOWN`: required capability answers are still unknown.
- `LIMITED`: every execution has an explicitly unavailable required capability.

The default execution prefers `PREFERRED`, then `SUBSTITUTE`, then lower
priority. Missing required/recommended resources and option limitations are
advisory and never block onboarding. The future planner consumes the typed
`GoalExecutionAssessment`; it does not infer substitutions from raw catalog
rows.

Telegram reviews are escaped, bounded HTML `<pre>` tables grouped by target
context. They show selection, resource, capability type, and whether the
resource participates in a preferred or substitute execution. Stale UUID
callbacks rerender current durable state. Messages remain within Telegram's
4,096-character limit.

## Workout-history import

The final onboarding decision is optional but explicit. The athlete sends an
Apple Health export ZIP or TCX file, or chooses **Skip for now**. Import and
Skip callbacks are deterministic and do not invoke an LLM. Failed, cancelled,
interrupted, or zero-workout imports leave the athlete at the same resumable
step.

The importer stores only objective workouts and metric types already modeled
by the workout tables. Apple Health clinical CDA, sleep, body composition,
general activity summaries, gait, and audio records are ignored. The original
ZIP/XML is always deleted after processing.

Canonical workouts retain discipline details and source provenance. Apple
workouts use a source-derived key independent of normalized discipline, so a
later classification correction cannot duplicate the workout. Swimming with
no reliable pool/open-water evidence remains `SWIMMING` with environment
`UNKNOWN`.

Timestamped Apple/TCX heart-rate observations are stored separately with
source identity and temporal quality. Exact and short-interval observations
may produce canonical average/max heart rate; coarse observations are retained
for future recomputation but do not create misleading aggregates. Reimporting
the same file is idempotent for workouts and observations.

A successful onboarding import writes workouts, source links, observations,
the successful job, and onboarding completion atomically. Completed athletes
can use the same importer through **Add workout**. Baseline calculation,
subjective feedback, and planner adaptation remain future work.

## Persistence

The reusable catalog is normalized across:

- `goal_templates`, `training_contexts`, and `goal_template_contexts`;
- `capabilities`;
- `context_execution_options` and `execution_option_capabilities`.

`training_goals` retains the athlete-facing goal fields and optionally points
to primary and supporting templates. Every newly confirmed goal has a primary
template; historical goals may remain unclassified until edited.
`athlete_capabilities` stores explicit current answers, owned by `athlete_id`.
`apple_health_import_jobs` records onboarding or post-onboarding context, while
`workout_heart_rate_observations` retains owner-scoped normalized HR facts.

Revision `0022_dynamic_training_catalog` seeds deterministic UUIDv5 catalog
data, safely classifies recognizable historical goals, backfills and merges old
equipment selections, preserves ambiguous access as non-execution
capabilities, normalizes active sessions, and removes `equipment_catalog` and
`athlete_equipment`. The migration is intentionally irreversible and requires a
pre-migration backup.

## Post-onboarding changes and privacy

`Change profile` provides focused mini-flows. Editing target outcome or event
date remains deterministic and does not touch catalog knowledge. Editing the
main goal or secondary priority invokes only the focused classification
workflow, displays the canonical candidate, and requires confirmation. Choosing
`None` for the supporting priority clears its text and foreign key without a
model call.

Confirming a new template uses the same atomic expansion workflow. Equipment &
access reopens only when either template ID changes; wording changes that retain
the same template do not force another review. A historical unclassified goal
must be classified before Equipment & access can open. No generic update path
can write `main_goal` directly.

`/profile` displays the athlete-facing goal, primary and supporting canonical
types, and currently available capabilities. Original free text remains useful
as internal provenance and future planner context.

Raw health and availability text, prompts, raw model responses, and full
profiles are not logged or placed in observability metadata. Every personal
repository operation is constrained by the athlete's user ID. Cancellation
retains saved data; account deletion remains the explicit destructive path.
