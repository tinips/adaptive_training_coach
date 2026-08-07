# Current Product Flow

## Supported onboarding boundary

The English-only Telegram onboarding collects an athlete's basic profile before
their confirmed goal, then collects the raw training context needed for a
future adaptive profile:

```text
Start -> consent -> basic profile -> conversational goal -> availability
      -> equipment recommendation -> equipment -> training limitations -> complete
```

The current milestone does not generate a training plan, normalize the new
context into the historical availability/equipment/health tables, or provide
medical diagnosis.

## Durable sequence

```text
CONSENT
  -> SETUP_INTRODUCTION
  -> PROFILE_BIRTH_YEAR_INTAKE
  -> PROFILE_GENDER_INTAKE
  -> PROFILE_WEIGHT_INTAKE
  -> PROFILE_HEIGHT_INTAKE
  -> GOAL_INTAKE
       -> clarification or addition (zero or more turns)
       -> explicit goal confirmation
  -> AVAILABILITY_INTAKE
  -> EQUIPMENT_RECOMMENDATION
  -> EQUIPMENT_INTAKE
       -> ALL_RECOMMENDED -> HEALTH_LIMITATIONS_INTAKE
       -> Other -> EQUIPMENT_DETAILS_INTAKE -> HEALTH_LIMITATIONS_INTAKE
  -> HEALTH_LIMITATIONS_INTAKE
       -> NONE_REPORTED or free text -> COMPLETED
```

The four basic profile values are deterministic: birth year, category, weight,
and height. The goal is extracted into a structured draft by the focused goal
LangGraph and is persisted only after the athlete confirms it.

Availability, equipment details, and training limitations are required raw-text
answers. Each goes through a separate compiled LangGraph validator with a
structured accept/retry result. The application stores the original text, not a
model-derived interpretation.

## Screens and deterministic callbacks

| Screen | Athlete action | Durable effect |
| --- | --- | --- |
| Basic profile | Birth year text, category button, weight text, height text | Owned `athlete_profiles` row is upserted before goal intake. |
| Goal confirmation | **No, that's right** | Upserts the canonical goal and moves to `AVAILABILITY_INTAKE`; onboarding is still active. |
| Availability | Free text with days and approximate time | Saves literal `availability_text`, then requests an equipment recommendation. |
| Equipment recommendation | Generated short essential list | Saves `equipment_recommendation_text` and shows the equipment choices. |
| Equipment | **I have all the recommended equipment** | Saves the stable marker `ALL_RECOMMENDED`. |
| Equipment | **Other / I have limitations** then free text | Saves the literal `equipment_text`. |
| Training limitations | **None** | Saves the stable marker `NONE_REPORTED` and completes onboarding. |
| Training limitations | **Describe limitations** then free text | Saves the literal `health_limitations_text` and completes onboarding. |

The equipment and health callbacks only transition/checkpoint data. They never
invoke a model. Callback data is state-bound, so an obsolete callback renders
the athlete's current durable checkpoint instead of applying an old action.

## Recommendation retry behavior

Availability is committed before the equipment-recommendation workflow runs.
If recommendation generation fails, the session remains at
`EQUIPMENT_RECOMMENDATION`, the availability text remains saved, and the bot
asks the athlete to send any message to retry. No equipment answer is accepted
until a recommendation is available.

## Persistence

`athlete_profiles` is the source of truth for the new raw context:

| Column | Meaning |
| --- | --- |
| `availability_text` | Athlete's literal weekly availability answer. |
| `equipment_recommendation_text` | Latest short goal-based equipment suggestion. |
| `equipment_text` | Literal equipment answer or `ALL_RECOMMENDED`. |
| `health_limitations_text` | Literal limitations answer or `NONE_REPORTED`. |

All columns are nullable so existing athlete profiles remain compatible. The
onboarding path does not write `availability_rules`, `equipment_access`, or
`health_constraints`; deriving an updateable structured profile remains a later
phase. Every read and update is constrained by the authenticated `user_id`.

The Alembic migration also extends the persisted onboarding-step checks on
`onboarding_sessions` and `llm_usage`.

## Conversational changes after completion

The global chat tool recognizes explicit changes to:

- goal, target outcome, and event date;
- age, birth year, category, weight, and height;
- availability;
- available equipment or equipment limitations;
- injuries and physical/training limitations.

Availability, equipment, and limitation changes update only their matching raw
text column. They do not normalize data or alter unmentioned fields. A goal
change invalidates the old equipment answer, regenerates the equipment
recommendation, reopens the session at `EQUIPMENT_INTAKE`, and immediately asks
the athlete to review and re-answer their equipment context.

Tool confirmations expose field names only. They never echo raw health text.

## LLM and privacy boundary

Free-text goal extraction, raw-context validation, and equipment
recommendation each use compiled stateless LangGraphs with Pydantic structured
output. The raw health/limitation answer is never placed in service errors,
LLM-usage records, workflow-observer metadata, or the global chat checkpoint.
Active onboarding bypasses that persistent chat workspace entirely. A successful
post-onboarding update removes its raw human input and raw tool-call arguments
before the workspace checkpoint is saved, and it ends without a second provider
turn. The recommendation workflow only receives confirmed goal fields and is
limited by prompt and deterministic output checks to a short essential list; it
cannot make a diagnosis or generate a plan.

## Compatibility and cancellation

Existing profiles continue to render with nullable raw-context values. A legacy
session that already has a confirmed goal but still needs its mandatory profile
continues to availability after the final height value. Cancellation and restart
remain ownership-scoped: cancellation retains saved data, while restart clears
only the onboarding session and returns it to consent.
