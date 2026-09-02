# Project State

What is actually built, what is deliberately out of scope right now, why the
architecture looks the way it does, and where the live engineering plan
stands. This file is meant to answer "what's the current state of this
thing" without reading the whole codebase. It summarizes; it is not the
source of truth for either the code or the active plan.

Written 2026-09-02 by cross-checking the codebase directly (not the older
docs it replaces — see the doc-consolidation summary in the conversation
that introduced this file for specifics on what was stale and why).

## What's built

**Telegram onboarding** is deterministic end to end except for two narrow
LLM calls (availability extraction, screenshot reading). The wired sequence
today:

1. `CONSENT` → `SETUP_INTRODUCTION`.
2. `GOAL_INTAKE`: pick a sport (running / cycling / swimming / triathlon),
   then a specific goal template within it. Swimming detours through
   `GOAL_SWIMMING_TYPE` (pool vs. open water) first.
3. `GOAL_METRIC_INTAKE`: one field at a time, whatever the chosen goal
   template requires (e.g. target distance).
4. `GOAL_EVENT_DATE` (optional, skippable) → `GOAL_CONFIRMED`.
5. Mandatory profile: `PROFILE_BIRTH_YEAR_INTAKE` → `PROFILE_GENDER_INTAKE` →
   `PROFILE_WEIGHT_INTAKE` → `PROFILE_HEIGHT_INTAKE` → `PROFILE_TIMEZONE_INTAKE`.
6. `AVAILABILITY_INTAKE`: free text, parsed by an LLM into a structured
   weekly-availability draft → `AVAILABILITY_REVIEW` to confirm or discard and
   redo. Confirming persists a schema-versioned `ConfirmedWeeklyAvailability`
   object (days, disciplines, time-of-day windows) — there is no raw
   availability text column any more.
7. `EQUIPMENT_RECOMMENDATION` → `EQUIPMENT_INTAKE`: checklist against a
   pruned, 9-capability equipment/facility catalog, scoped to the athlete's
   goal.
8. `HEALTH_LIMITATIONS_INTAKE`: free text or "None".
9. `BASELINE_INTAKE`: a Telegram Mini App form (not a chat prompt) for
   self-reported baseline numbers (e.g. threshold pace, FTP), scoped to the
   goal's disciplines. Submitting it marks onboarding **completed
   immediately** — there is no further step in the live path.

**Weekly planning** is real and implemented, not aspirational:
`WeeklyPlanningService.generate_next_week` gathers deterministic evidence,
gates on a whole-athlete readiness floor, calls an LLM
(`method="function_calling"`, so the schema is actually sent) to produce a
seven-day plan, validates the reply, checks it against stated availability,
and persists it with optimistic concurrency. It is reachable in the bot only
through the "Plan next week" reply-keyboard button — see Known gaps below.

**Also built:** post-onboarding screenshot-based workout logging (a vision
model reads a workout-summary photo; the athlete confirms before it saves,
deduplicated by a fingerprint of discipline/start time/duration/distance);
Apple Health ZIP and TCX workout-history import; a workout-history Mini App
chart; optional self-hosted Langfuse tracing (metadata-only, off by
default); profile editing mini-flows post-onboarding.

## Explicitly out of scope right now

- **No adaptive replanning, mid-week plan changes, or feedback collection.**
  The weekly planner writes a plan; nothing reads effort/discomfort feedback
  back into it, and a generated plan cannot be edited session-by-session.
  This is designed (see `docs/superpowers/specs/2026-08-28-adaptive-planning-design.md`
  sections 6–8) but not built.
- **No Strava integration.** All Strava application code was deleted; see
  Known gaps for why this isn't as clean as it sounds.
- **No mobile/companion app.** A prior iPhone HealthKit sync companion
  (`ios/CoachHealthSync/`) was built and then fully removed, credentials
  table dropped by migration `0045_remove_mobile_sync`. Workout history now
  reaches the system only via Apple Health export, TCX file, or screenshot.
- **No dynamic/LLM-driven catalog expansion.** The goal catalog (14 primary
  goals across running/cycling/swimming/triathlon, 5 supporting goals, 9
  equipment capabilities) is a fixed seed. An LLM-driven catalog-expansion
  system existed, was evaluated (30/30 test scenarios passed), and was then
  deliberately deleted because every row in every environment had always
  been `SEEDED` — the LLM had never actually generated anything with it.
- **No RAG, embeddings, vector database, or multi-agent orchestration.**
- **No dashboards, payments, or medical diagnosis.**
- **`TRAINING_HISTORY_IMPORT` as an onboarding step is dead code.** The enum
  member and its handlers still exist, but nothing in the live flow
  transitions a real athlete into it — onboarding now ends at
  `BASELINE_INTAKE`. It's only reachable through a dev-only seeding helper.

## Key architectural decisions and why

- **Deterministic-first: the weekly planner is the only LLM call that
  decides anything material.** Onboarding previously used a LangGraph
  conversational layer with free-text goal extraction, per-answer LLM
  validation of availability/health text, and LLM-driven catalog expansion.
  All of it was deleted (commit `aa847df`, "on boarding deterministic") in
  favor of deterministic menus and length checks, because none of it was
  pulling its weight: the catalog expansion had never generated anything,
  and the conversation layer and context-validation workflow were judged to
  add failure modes and LLM cost without changing what got stored. The two
  LLM calls that remain outside the planner (availability free-text parsing,
  screenshot reading) exist because there's no deterministic way to do that
  extraction.
- **`function_calling`, not `json_mode`, for structured LLM output.** Tested
  live against DeepSeek: `json_mode` never sends the schema to the model, so
  the model has to guess field names — measured at 21 validation errors on
  every single weekly-plan request before this was fixed. `function_calling`
  sends the schema as a tool definition and produced a valid plan.
- **Planner readiness is judged on the athlete as a whole, not per sport.**
  The gate used to require 3 sessions / 2 active days in *every* target
  sport (an `all()` veto), so a triathlete with a weak swim got no plan at
  all, including for the running and cycling that were ready. It now
  requires the floor across all target sports combined, and classifies each
  sport `WELL_EVIDENCED` / `THIN` / `NONE` instead of vetoing on it — a thin
  sport still gets planned, just gently.
- **Structured, schema-versioned availability, not raw free text.** Athlete
  availability is now a `ConfirmedWeeklyAvailability` object (per-day
  disciplines and time windows), confirmed or discarded before it's
  persisted. `athlete_profiles.availability_text` was dropped by migration
  `0046`, deliberately with no downgrade path.
- **Equipment/facility catalog pruned to 9 capabilities.** 21 rarely-useful
  or redundant capability codes were removed (migration `0037`) alongside
  their dependent execution-option and athlete-selection rows.
- **Screenshot-based workout logging is deliberately outside the main bot
  facade.** Confirmation is two plain buttons on an in-memory draft (30
  minute TTL), not a multi-turn conversation state, because that's all it
  needs to be.

## Known gaps / tech debt worth knowing about

These are real discrepancies between what the code plainly intends and what
it actually does — surfaced while cross-checking docs against the codebase,
not fixed here since fixing them wasn't in scope for a docs pass.

- **Strava tables were never migrated away.** All Strava application code
  (routes, repositories, schemas, activity adapters) has been deleted, and
  `app/config.py` has no Strava settings at all — but `strava_connections`,
  `strava_sync_jobs`, and `strava_webhook_events` are still created by
  `0001_initial.py` and no later migration ever drops them. They're inert
  but still part of the live schema.
- **`/plan_next_week` and `/view_weekly_plan` are not registered Telegram
  commands.** They're only reachable by tapping the matching reply-keyboard
  button, which the bot maps to the same internal dispatch as the slash
  command. An athlete who types the slash command manually is not caught by
  any handler (`~filters.COMMAND` excludes it, and there's no
  `CommandHandler` for it either) — it silently goes nowhere.
- **Orphaned dependencies.** `langgraph` and `langgraph-checkpoint-postgres`
  are still declared in `pyproject.toml` even though zero code imports
  `langgraph` anywhere in `app/` — leftover from the deleted conversation
  layer. `langchain-core`/`langchain-openai` are still genuinely used and
  should stay.
- **A known pre-existing test mismatch**, per the active ExecPlan's own last
  entry: the full suite carries an Apple Health fixture-count mismatch (the
  test archive contains 42 workouts, an assertion expects 28).

## Active ExecPlan snapshot

`.agent/PLANS.md` (untouched by this file, and the actual live source of
truth) points to the single active ExecPlan:
`.agent/execplans/onboarding-strava-vertical-slice.md` (2,600+ lines).

Its name and its original objective section are both stale relative to what
it now covers: it was scoped as a Strava-inclusive "vertical slice" with
training-plan generation explicitly excluded, and has since organically grown
to cover (via dated "Follow-up" sections) a mobile HealthKit sync path that
was later fully removed, the weekly planner described above, and optional
Langfuse tracing. Despite the name, it is the record for essentially
everything in this repo's history, and it is genuinely current — its own
last dated entries are the most accurate description of what changed most
recently.

**As of its most recent entry** ("Follow-up: optional self-hosted Langfuse
tracing", 2026-09-02, the file's last section): Langfuse wiring is
implemented and lazily no-ops when unconfigured, but has not yet been
validated end-to-end against a real running Langfuse stack — that checklist
item is unchecked. The file's final sentence also flags the fixture-count
test mismatch noted above as still open.
