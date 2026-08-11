# Current Product Flow

## Supported onboarding boundary

The English Telegram onboarding collects the athlete profile, confirmed goal,
literal availability, deterministic equipment access, and literal training
limitations:

```text
Start -> consent -> basic profile -> conversational goal -> availability
      -> catalog equipment review -> training limitations -> complete
```

The milestone does not generate a training plan, calculate feasibility, or
provide medical diagnosis.

## Durable sequence

```text
CONSENT
  -> SETUP_INTRODUCTION
  -> PROFILE_BIRTH_YEAR_INTAKE
  -> PROFILE_GENDER_INTAKE
  -> PROFILE_WEIGHT_INTAKE
  -> PROFILE_HEIGHT_INTAKE
  -> GOAL_INTAKE -> clarification -> explicit confirmation
  -> AVAILABILITY_INTAKE
  -> EQUIPMENT_RECOMMENDATION
  -> EQUIPMENT_INTAKE -> HEALTH_LIMITATIONS_INTAKE
  -> NONE_REPORTED or free text -> COMPLETED
```

Availability and training-limitations free text use the compiled stateless
LangGraph validation boundary and are stored literally. Equipment review is a
fully deterministic database path and never invokes a model.

## Equipment flow

The resolver reads `main_goal`, `target_outcome`, and `secondary_priority`, then
loads catalog rows for the relevant disciplines. An unmatched goal displays a
short notice and advances safely to training limitations without writing
athlete-equipment rows.

The athlete checks every item or facility they can use and chooses Continue.
The application replaces `athlete_equipment` only for the reviewed disciplines,
then displays:

- whether the selected access can satisfy each discipline's essentials;
- missing essentials and their valid alternatives;
- missing recommended items.

Gaps are advisory and never block completion. Optional gaps are omitted. A goal
change reopens the relevant review with matching global equipment preselected;
ownership is not invalidated or revision-scoped. Old catalog/resource callback
IDs rerender the current durable state rather than applying a stale selection.

Equipment reviews and gap summaries are escaped HTML `<pre>` tables. Reviews
are grouped by discipline and show selection, equipment, importance, and valid
alternatives. Gap tables preserve the ability-to-start statement and show only
missing essential and recommended items. `/profile` renders selected equipment
with discipline and importance. Table cells are bounded with an ellipsis and
every resulting Telegram message remains within the 4,096-character limit.

## Persistence

`athlete_profiles.availability_text` and
`athlete_profiles.health_limitations_text` retain the two literal context
answers. `equipment_catalog` is static system knowledge and
`athlete_equipment` is current athlete access. Every personal-data read and
write is constrained by the athlete's `user_id`.

Revision `0018_remove_obsolete_equipment` removed the obsolete equipment tables,
goal revision, and raw equipment profile columns after the final guarded
backfill. The pre-cleanup PostgreSQL backup is required to recover discarded raw
text or interpretation history.

## Post-onboarding changes and privacy

`Change profile` provides deterministic mini-flows for goal, availability,
equipment, health, and personal details. Equipment callbacks never call the
global agent or context workflow. Changing a goal opens the new relevant catalog
review while preserving global access.

`Profile` also displays the current training goal's main goal, target outcome,
event date, secondary priority, and confirmed status.
The deterministic Goal editor displays an explicit submenu for main goal,
target outcome, event date, and secondary priority. Each button edits only its
named field and shows the saved value before replacement. A secondary priority
can be cleared with `None`. The original description remains internal onboarding
provenance and is neither displayed nor editable. Goal status, record IDs,
ownership, and timestamps are also system-managed. Revisions `0019` through
`0021` evolve the durable profile-settings checkpoints for these controls.

The persistent reply keyboard follows account lifecycle state:

- no account: `Start`;
- active or cancelled onboarding: `Resume`, `Delete`;
- completed profile: `Profile`, `Change profile`, then `Delete`.

Those exact labels route directly to existing deterministic actions without a
model call. A successful deletion replaces the keyboard with `Start`. Profile
edit prompts display the saved value for goals, outcome, event date,
availability, training limitations, birth year, category, weight, and height.
Equipment uses its selected-state table. Values are escaped and user-facing;
long health or availability text is truncated only in the Telegram presentation
with an explicit marker, while the stored value remains unchanged.

Profile-edit text prompts use `Back / Done` with the `ps:v1:` settings contract.
They never reuse onboarding's `Cancel` callback. Closing an edit clears pending
settings state, confirms that profile settings are closed, and leaves onboarding
completed.

Raw health and availability text is not logged or retained in global-agent
checkpoints. Tool confirmations name the updated field without echoing private
content. Cancellation retains saved data; restart clears only the onboarding
session.
