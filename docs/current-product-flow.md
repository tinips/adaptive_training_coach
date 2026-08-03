# Current Product Flow

This document describes the supported Telegram onboarding with the focused
goal flow and mandatory deterministic athlete-profile phase.

## Supported boundary

Onboarding begins at Telegram Start and ends after the mandatory birth year,
category, weight, and height values have been validated and persisted.

```text
Start
  -> Welcome / Help / Privacy
  -> Consent
  -> Setup introduction
  -> Conversational goal intake
       -> clarification (zero or more turns)
       -> confirmation
          -> add/change -> extraction -> confirmation
          -> start again -> goal intake
          -> cancel -> cancelled session
          -> confirm -> PROFILE_BIRTH_YEAR_INTAKE
  -> PROFILE_GENDER_INTAKE
  -> PROFILE_WEIGHT_INTAKE
  -> PROFILE_HEIGHT_INTAKE
  -> lifecycle ONBOARDING_COMPLETED
```

The broader removed flow still includes sport, event configuration,
availability, equipment, health limitations, coach style, baseline choice,
onboarding imports, and summary editing. Only the four mandatory demographic
fields described here have been restored.

## Screens and callbacks

| Screen | Message/action | Buttons and callbacks | Durable effect |
| --- | --- | --- | --- |
| Welcome | Product introduction | Let's go `nav:v1:consent`; Help `nav:v1:help`; Privacy `nav:v1:privacy` | New user/session creation only |
| Help | Product explanation | Let's go; Back `nav:v1:welcome` | None |
| Privacy | Privacy and safety explanation | Let's go; Back | None |
| Consent | Explicit storage and non-medical boundary | I understand - continue `ob:v1:consent`; Back; Cancel | `answers.consent=true`; step `SETUP_INTRODUCTION` |
| Setup introduction | Explains profile construction | Let's build my profile `ob:v1:profile`; Cancel | Step `GOAL_INTAKE`; phase `COLLECTING` |
| Goal intake | Initial free-text question | Cancel | Raw goal text and focused draft are staged |
| Goal clarification | One missing/ambiguous detail | Optional focused choices plus Cancel; free text always allowed | Existing draft is merged, not replaced |
| Goal confirmation | Concise four-field summary | No, that's right `ob:v1:goal:confirm`; Yes, add something `ob:v1:goal:add`; Start again `ob:v1:goal:restart`; Cancel | No canonical write until confirm |
| Goal addition | Requests one free-text change | Cancel | Next extraction receives the existing draft |
| Goal saved / birth year | Requests four-digit text | Cancel | Canonical goal upsert; step `PROFILE_BIRTH_YEAR_INTAKE` |
| Category | Male, Female, Other / Unspecified | `ob:v1:profile:gender:<value>`; Cancel | Validated category; step `PROFILE_WEIGHT_INTAKE` |
| Weight | Requests kilograms as text | Cancel | Numeric 40.0-200.0; step `PROFILE_HEIGHT_INTAKE` |
| Height | Requests centimeters as text | Cancel | Integer 120-230; atomic profile upsert and lifecycle completion |
| Cancel confirmation | Two-step cancellation | Yes, cancel `ob:v1:cancel:confirm`; Keep onboarding `ob:v1:cancel:keep` | Status becomes `CANCELLED` only after confirmation |
| Cancelled | Resume affordance | Restart onboarding `ob:v1:restart`; Back to welcome | Restart clears only the onboarding session and returns to consent |

Every callback is under Telegram's 64-byte callback-data limit. Deterministic
callbacks never invoke the LLM.

## Initial goal question

After **Let's build my profile**, the bot sends:

```text
Let's start with your goal.

What are you training for, and what would success look like to you?

Tell me in your own words. You can include a race or challenge, when you want to do it, the result you are aiming for, and anything important you want to preserve while training.

For example:
"I want to complete my first Ironman 70.3 next July, finish safely and maintain muscle."
```

The Telegram rendering uses typographic punctuation, but the meaning and line
structure are unchanged. A visible Cancel button is always present on free-text
goal screens.

## Goal draft contract

The focused structured model output contains exactly:

```json
{
  "main_goal": null,
  "event_date": null,
  "target_outcome": null,
  "secondary_priority": null,
  "missing_fields": [],
  "ambiguous_fields": [],
  "message_status": "COMPLETE"
}
```

Allowed statuses:

- `COMPLETE`
- `NEEDS_CLARIFICATION`
- `OFF_TOPIC`

The four goal fields are the only extracted product fields. Completeness and
ambiguity lists may name only those fields. `secondary_priority` never blocks
confirmation.

The model receives the current user message and the current structured draft.
It does not receive the full Telegram conversation, repositories, or database
credentials. The graph is compiled once at application construction and has no
checkpointer.

## Application validation

Pydantic rejects extra keys, malformed dates, invalid status values, oversized
text, and unknown missing/ambiguous field names. Application code then enforces:

- `main_goal` must be present and specific enough to influence training;
- `target_outcome` must be present;
- an event date may be exact, explicitly unknown/not applicable, or absent for
  a non-event goal;
- a month and day without a year resolves to the next strictly future calendar
  occurrence relative to the date captured by `OnboardingService`;
- an explicitly supplied past date is not rewritten and returns to date
  clarification;
- an ambiguous date must be clarified;
- `secondary_priority` is optional;
- vague phrases such as "train to run" do not pass readiness.

The model's `COMPLETE` label alone cannot force confirmation if these checks
fail.

## Clarification and merging

Only one clarification is shown at a time, prioritized as:

1. main goal;
2. target outcome;
3. event date.

For a vague running goal, the bot offers race, distance, pace, consistency, and
something-else choices while still accepting free text. For a missing date,
**Not yet** marks a null date as explicitly acceptable without calling the LLM.

Each extraction receives the previous draft. A non-null newly extracted field
updates the draft; valid prior values remain when the new message addresses a
different field. An explicit no-date answer can clear a prior date.

An `OFF_TOPIC` result does not merge any proposed fields. The latest unrelated
message is removed from the retained goal-message audit list, and the user is
redirected to the goal question.

## Confirmation

The confirmation screen renders:

```text
Here's what I understood:

Main goal
...

Event date
...

Target outcome
...

Secondary priority
...

Is there anything else you want me to know about this goal?
```

**Yes, add something** changes the temporary phase to `ADDING`; the next free
text is extracted and merged. **Start again** removes only goal draft, goal raw
text, goal messages, clarification metadata, and parse-in-flight state. Consent
and unrelated retained session keys are not deleted.

**No, that's right** performs the canonical goal write and then shows:

```text
Your goal has been saved.

What year were you born? Send the four-digit year (1940 to 2008).
```

The next four steps are ordinary deterministic Python. No LangGraph or model is
invoked. Invalid text keeps the current state and produces a centralized error
prompt. The final valid height upserts the four values through
`ProfileRepository`, marks the onboarding session `COMPLETED`, and changes the
user lifecycle to `ONBOARDING_COMPLETED` in the same transaction.

## Temporary and canonical persistence

### During intake

`onboarding_sessions` stores:

- `status`: `ACTIVE`, `COMPLETED`, or `CANCELLED`;
- `current_step`: one of the goal or mandatory-profile steps;
- `answers.consent`;
- `answers.raw_goal_text`: exact first relevant goal message;
- `answers.goal_messages`: relevant goal-step messages only;
- `answers.goal_draft`: validated structured draft;
- `answers._goal_intake_phase`;
- optional clarification metadata;
- a short-lived parse-run ownership marker while extraction is in flight.
- staged `birth_year`, `gender`, `weight_kg`, and `height_cm` values during the
  deterministic profile phase.

No unconfirmed data is written to `training_goals`.

The extraction graph is invoked with `CREATE_GOAL` when no draft exists and
`UPDATE_EXISTING_GOAL` otherwise. The current persisted draft is context, while
the newest user message is the only answer being extracted. The model returns
a validated patch for the four goal fields plus completeness metadata; it does
not return a replacement draft. The onboarding service merges that patch
deterministically. Null semantic fields preserve prior values, explicit non-null
fields update them, and `OFF_TOPIC` preserves the whole prior draft.

### After confirmation

`ProfileRepository.upsert_conversational_training_goal` is the one canonical
writer. The owned `training_goals` row represents:

| Field | Value |
| --- | --- |
| `main_goal` | Confirmed concise goal |
| `event_date` | Exact date or null |
| `target_outcome` | Confirmed success definition |
| `secondary_priority` | Explicit optional priority or null |
| `original_description` | Exact first raw goal message |
| `status` | `CONFIRMED` |

`main_goal`, `target_outcome`, and `original_description` are required canonical
columns. Migration `0010_remove_legacy_goal_fields` maps legacy-only rows into
that representation before removing the redundant `goal_type`, `event_name`,
and `goal_priority` columns.

The owned `athlete_profiles` row additionally stores the mandatory
`birth_year`, `gender`, `weight_kg`, and `height_cm` values. Repository reads and
writes always include the authenticated `user_id`.

## Error and concurrency behavior

- A database-backed rolling-hour limit applies only in live LLM mode.
- A durable parse-run ID prevents concurrent goal text from overwriting the
  result currently being processed.
- Stale parse results are rejected by ownership and run ID.
- Provider failure, timeout, or malformed structured output saves no draft
  changes and renders a retry-safe goal screen.
- Raw goal text is never emitted in application logs or LLM usage rows.
- All session and goal repository operations include the owning user ID.
- Profile validation is deterministic and profile values are never logged.

## Legacy-data migration

Migration `0008_remove_legacy_onboarding` first widens enum checks, normalizes
data, then narrows constraints to retained values.

Session mapping:

| Legacy data | Retained checkpoint |
| --- | --- |
| Missing consent | `CONSENT`, empty answers |
| Consent plus pending setup marker | `SETUP_INTRODUCTION` |
| Consent plus raw goal or draft | `GOAL_INTAKE` |
| Confirmed conversational canonical goal | `GOAL_CONFIRMED` |
| Consent but legacy-only later answers | `GOAL_INTAKE`, fresh collecting phase |

Legacy `COMPLETED` onboarding status becomes `ACTIVE`; this does not alter an
existing user's profile lifecycle status. `CANCELLED` remains cancelled. Legacy
LLM step values map to `GOAL_INTAKE`.

The migration drops only fields with no retained consumer:

- pending generic free-text step/value;
- summary return flag;
- onboarding completion timestamp;
- import-job onboarding session/context provenance;
- workout-feedback return-to-onboarding flag.

It preserves canonical goals, users, historical normalized profile records,
workouts and detail tables, source links, import jobs and their outcomes,
feedback, baselines, OAuth connections, sync jobs, and webhook events.

Migration `0009_mandatory_profile` expands the state and lifecycle constraints,
adds `birth_year` and `gender` to `athlete_profiles`, and preserves legacy rows
by keeping the new columns nullable at the database compatibility boundary.

Migration `0010_remove_legacy_goal_fields` preserves meaningful legacy goal
content through deterministic backfill, removes the three superseded columns,
and enforces non-null canonical goal text.

## Existing-athlete features outside onboarding

Existing completed profiles may still use profile reads, account menus, daily
Apple Health ZIP/TCX imports, feedback, deterministic baselines, and optional
Strava features. These paths do not transition onboarding and cannot return to
an onboarding import or summary state.

## Validation coverage

Focused tests cover:

- Welcome through the free-text goal prompt;
- exact raw message retention;
- complete, vague, ambiguous, explicitly unknown-date, and off-topic outcomes;
- multi-turn draft merging;
- optional secondary priority;
- add/change, start again, cancel, and confirm behavior;
- absence of a canonical goal before confirmation;
- deterministic birth year, category, weight, and height validation;
- state preservation after invalid values and no model calls during profile intake;
- atomic owned profile upsert and `ONBOARDING_COMPLETED` lifecycle transition;
- migration mapping, column removal, upgrade, downgrade, and re-upgrade;
- retained daily imports, workout feedback, historical profile reads, Strava,
  and deterministic baseline behavior.

Live Telegram, live LLM, and live Strava validation require credentials and are
not implied by automated tests.
