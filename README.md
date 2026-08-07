# Adaptive Endurance Coach

Adaptive Endurance Coach is a modular Python application for a Telegram-based
endurance coach. The currently supported onboarding slice deterministically
collects the athlete's birth year, competition category / biological sex,
weight, and height, then records a confirmed athlete goal, raw weekly
availability, equipment context, and training limitations. It stores that new
context as literal profile text; deriving a normalized, updateable profile,
baseline selection, feasibility, and plan generation remain outside this phase.

The interface is English-only. Goal answers may be written naturally in any
language supported by the configured model. The application does not provide
medical advice and must not be used for emergencies.

## Current Telegram onboarding

The only supported journey is:

1. Telegram **Start** opens Welcome, Help, and Privacy & safety.
2. **Let's go** opens explicit consent.
3. Consent opens the setup introduction.
4. **Let's build my profile** asks for a four-digit birth year from 1940
   through 2008.
5. An inline keyboard records Male, Female, or Other / Unspecified.
6. The bot validates weight from 40.0 through 200.0 kg.
7. The bot validates integer height from 120 through 230 cm and saves the
   owned athlete profile.
8. A free-text goal question opens after the profile is saved.
9. A focused LangGraph/LangChain operation extracts a four-field draft.
10. The bot asks one clarification at a time until the goal is clear.
11. The user can confirm, add or change information, start again, or cancel.
12. **No, that's right** writes the canonical goal and asks for weekly
    availability in free text (days and approximate time).
13. A focused LangGraph validates the answer, stores it literally, and creates
    a short goal-based essential-equipment recommendation.
14. The athlete chooses **I have all the recommended equipment** or **Other /
    I have limitations**. The latter is followed by a literal free-text answer.
15. The athlete chooses **None** or **Describe limitations**. A literal answer
    is required when they describe limitations; only then does onboarding move
    to `ONBOARDING_COMPLETED`.

The first mandatory profile prompt is:

```text
First, let\u2019s build your athlete profile.

What year were you born? Send the four-digit year (1940 to 2008).
```

Profile completion does not start an import, select a baseline, calculate
feasibility, or generate a plan.

Commands, callbacks, and completed-profile chat routing use one global
tool-calling LangGraph workspace. Active onboarding events bypass that workspace
and go directly to the focused onboarding service, so raw availability,
equipment, and limitation text is never written to global chat checkpoints. The
sparse update tool supports `main_goal`, `target_outcome`, `event_date`, `age`,
`birth_year`, `gender`, `weight_kg`, `height_cm`, `availability_text`,
`equipment_text`, and `health_limitations_text` while preserving omitted values.
A goal change regenerates the equipment recommendation and requires equipment
review again.

The durable onboarding states are:

- `CONSENT`
- `SETUP_INTRODUCTION`
- `GOAL_INTAKE`
- `GOAL_CONFIRMED`
- `PROFILE_BIRTH_YEAR_INTAKE`
- `PROFILE_GENDER_INTAKE`
- `PROFILE_WEIGHT_INTAKE`
- `PROFILE_HEIGHT_INTAKE`
- `AVAILABILITY_INTAKE`
- `EQUIPMENT_RECOMMENDATION`
- `EQUIPMENT_INTAKE`
- `EQUIPMENT_DETAILS_INTAKE`
- `HEALTH_LIMITATIONS_INTAKE`

Cancellation is stored as an onboarding-session status. Restart returns only
that user's session to consent.

## Goal extraction and persistence

Callbacks and free text first pass through the global agent. Goal extraction
uses a focused nested graph with this structured contract:

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

`message_status` is `COMPLETE`, `NEEDS_CLARIFICATION`, or `OFF_TOPIC`. The
application revalidates model output and independently enforces goal readiness.
`secondary_priority` is optional. An unknown or inapplicable event date is
valid; an ambiguous date is not converted into an invented exact date.
Each extraction receives the current local calendar date. A month-and-day date
without a year is interpreted as the next strictly future occurrence; an
explicit past year is rejected for clarification rather than silently changed.

During intake, `onboarding_sessions.answers` holds only the relevant temporary
state: consent, the first raw goal message, goal messages from this step, the
structured draft, phase, and optional clarification metadata. An off-topic
answer is removed from the retained goal messages and cannot alter the draft.

The extraction path cannot write canonical data. Only explicit confirmation
calls the canonical writer in `ProfileRepository`, which upserts one
`training_goals` row containing:

- `main_goal`
- `event_date`
- `target_outcome`
- `secondary_priority`
- `original_description`
- `status=CONFIRMED`

After goal confirmation, temporary draft and clarification state are removed.
The original goal text and the relevant goal-message audit trail remain in the
onboarding session. The global agent routes the four mandatory profile answers;
application services deterministically revalidate every value. Before goal
intake, the final height submission
`ProfileRepository.upsert_mandatory_athlete_profile` writes `birth_year`,
`gender`, `weight_kg`, and `height_cm` for the owning `user_id`.

The next three required onboarding responses are retained directly on that
profile as nullable `TEXT` columns: `availability_text`,
`equipment_recommendation_text`, `equipment_text`, and
`health_limitations_text`. Raw availability, equipment, and limitation text is
validated with a compiled LangGraph but is persisted verbatim. Buttons persist
the stable markers `ALL_RECOMMENDED` and `NONE_REPORTED`. The legacy normalized
availability, equipment, and health tables are intentionally not written by
this flow.
For a completed athlete, `update_onboarding_data` delegates through
`OnboardingService`, which validates the sparse payload and routes profile
fields to `athlete_profiles` and goal fields to `training_goals`. Each
repository update is constrained by `user_id` and dynamically includes only
the allowlisted supplied columns, preserving every omitted value and advancing
`updated_at` only on the affected table. Availability, equipment, and
limitations updates affect only their matching raw column. A changed goal clears
the old equipment answer, regenerates the recommendation, and reopens the
equipment-review checkpoint.

## Architecture

The project is a modular monolith with two processes:

1. FastAPI serves health/readiness plus optional Strava OAuth and webhook
   routes.
2. `python-telegram-bot` runs Telegram long polling.

Both use the same application-service and repository boundaries. PostgreSQL is
the source of truth. The global Telegram graph uses native
`AsyncPostgresSaver` checkpoints keyed by a stable Telegram thread ID and backed
by one process-owned `AsyncConnectionPool`. The graph is compiled once during
bot startup and reused for every update. Strict callbacks, known commands, and
contextual numeric profile answers take a deterministic graph branch that skips
the LLM and writes one final checkpoint at graph exit. Exact mandatory-intake
prompts dispatch to the onboarding transition service; numeric answers to
post-onboarding clarification prompts generate typed sparse-update tool calls
instead, preserving conversational context without another model request.
Service callables remain invocation context and are never serialized. Account
deletion also removes the corresponding agent thread.

The PostgreSQL checkpoint retains only safe global-agent history. Active
onboarding events never enter it, and a successful post-onboarding update
removes its raw human input and raw tool-call arguments before checkpoint exit.
The provider context is bounded to the latest three safe conversation messages;
historical tool traffic is omitted, while an active assistant tool request and
its matching `ToolMessage` are retained together when a provider turn is still
needed. Future planner context will be injected separately as structured athlete
data rather than expanding the chat window.

```text
Telegram events ---- persistent global LangGraph ---- application tools
                              |                         |
                              |                         +-- services/repositories
                              |                                     |
                              +---- PostgreSQL checkpoints           +-- PostgreSQL
```

Messages are centralized in `backend/app/bot/messages.py`; keyboard labels and
callbacks are centralized in `backend/app/bot/keyboards.py`. Personal-data
repository operations include the owning internal user ID.

## Retained features outside onboarding

Existing athletes whose normalized profiles were completed before this
onboarding reduction retain their data and can continue to use:

- normalized profile reads;
- Apple Health ZIP and TCX daily file imports;
- deterministic baseline recalculation;
- durable post-workout feedback;
- optional Strava OAuth, synchronization, disconnect, and webhook ingestion.

Daily file imports are not an onboarding path. A user must already have a
completed-profile lifecycle status and begin through **Add workout**. Import
jobs retain their outcomes and workout provenance, but no longer contain an
onboarding session ID or onboarding/daily context flag.

The historical normalized profile tables remain because current profile reads
for existing athletes still depend on them. The new onboarding writes only the
basic demographics, canonical goal, and four raw context columns; it does not
write normalized availability, equipment, health-constraint, coach-preference,
or baseline-preference records.

## Intentional non-goals

This slice does not implement:

- derived normalization of availability, equipment, or injury context;
- baseline selection during onboarding;
- goal feasibility or safety assessment;
- roadmap, weekly plan, or adaptive replanning;
- RAG, embeddings, a vector database, or multiple agents;
- dashboards, payments, nutrition planning, or medical diagnosis.

## Repository layout

```text
.
|-- AGENTS.md
|-- .agent/execplans/onboarding-strava-vertical-slice.md
|-- backend/
|   |-- app/
|   |   |-- api/
|   |   |-- bot/
|   |   |-- db/
|   |   |-- integrations/
|   |   |-- repositories/
|   |   |-- schemas/
|   |   |-- services/
|   |   `-- workflows/onboarding_goal/
|   |-- alembic/
|   `-- tests/
|-- docs/current-product-flow.md
|-- .env.example
`-- docker-compose.yml
```

## Local setup on Windows

Requirements:

- Windows 10 or 11 with PowerShell
- Python 3.12+
- Docker Desktop with Linux containers
- a Telegram bot token for live Telegram use
- optionally, Strava credentials and a public HTTPS callback
- optionally, an OpenAI-compatible model key for `LLM_MODE=live`

Install from the repository root:

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"

cd ..
Copy-Item .env.example .env
```

Do not overwrite an existing ignored `.env` that contains local credentials.
For Strava token encryption, generate a Fernet key:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put the key in `APP_ENCRYPTION_KEY`. Never commit `.env`.

## Run with Docker Compose

Set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, and
`APP_ENCRYPTION_KEY`, then run:

```powershell
docker compose up --build
```

Compose starts PostgreSQL, runs `alembic upgrade head`, then starts FastAPI and
the Telegram bot. The API is available at `http://localhost:8000`; the local
PostgreSQL port is `55432`.

Useful checks:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/ready
docker compose ps
docker compose logs migrate
```

`docker compose down` preserves the database volume. `docker compose down -v`
permanently deletes local application data.

## LLM configuration

The default `LLM_MODE=mock` requires no provider key and traverses the same
compiled goal graph used in live mode. For an OpenAI-compatible provider, set:

```text
LLM_MODE=live
LLM_API_KEY=...
LLM_BASE_URL=...
LLM_MODEL=...
```

Model calls use structured Pydantic output. Safe usage metadata is written to
`llm_usage`; raw goal text, prompts, profiles, secrets, and OAuth tokens are not
logged there. Bot startup emits only `goal_llm_mode=mock` or
`goal_llm_mode=live model=<model-name>` so the active provider path is visible
without exposing credentials or goal text.

Goal extraction uses `CREATE_GOAL` for the first message and
`UPDATE_EXISTING_GOAL` once a persisted draft exists. The graph receives the
current draft and latest message separately. Its structured model result is a
field patch; application code preserves existing values for null patch fields,
applies explicit corrections, and ignores every semantic field when the status
is `OFF_TOPIC`.

## Database migration

Migration `0007_conversational_goal` adds the canonical conversational goal
fields. Migration `0008_remove_legacy_onboarding`:

- maps saved sessions to the four retained checkpoints;
- maps old completed onboarding sessions to active retained checkpoints;
- preserves cancelled status;
- removes generic parse, summary-return, completion timestamp, onboarding
  import provenance, and return-to-onboarding feedback columns;
- preserves users, canonical goals, historical normalized profiles, workouts,
  import jobs, feedback, baselines, and Strava data.

Migration `0013_add_athlete_profile_context` adds the four nullable raw-context
`TEXT` columns to `athlete_profiles` and extends the persisted onboarding-step
checks for the availability, recommendation, equipment, and limitations
checkpoints.

Migration `0009_mandatory_profile` adds the four deterministic profile states,
completed lifecycle/status values, and the owned athlete-profile `birth_year`
and `gender` columns. Existing normalized profiles remain readable.

Migration `0010_remove_legacy_goal_fields` backfills legacy-only goal rows into
the canonical representation, removes redundant `goal_type`, `event_name`, and
`goal_priority` columns, and makes `main_goal`, `target_outcome`, and
`original_description` required.

Migration `0012_remove_fitness_level` removes the unused transient
`fitness_level` column introduced by revision `0011`.

The migration supports upgrade, downgrade, and re-upgrade in the portable
SQLite migration tests and is also validated against Compose PostgreSQL.

## Validation

Run from `backend`:

```powershell
pytest
ruff check .
ruff format --check .
mypy app
```

Validate PostgreSQL from the repository root:

```powershell
docker compose up -d db
cd backend
alembic upgrade head
alembic current
alembic check
```

Automated tests use deterministic model doubles, mocked provider transports,
and synthetic Apple Health/TCX files. They do not prove a live Telegram, live
LLM, or live Strava journey.

## Privacy and safety

- Secrets, OAuth tokens, full profiles, and unredacted free text are not logged.
- Goal raw text is retained only in the owned onboarding session for audit and
  future re-extraction.
- The full Telegram conversation is not stored.
- Apple Health ZIP/XML and TCX uploads are deleted from generated temporary
  paths after processing; interrupted cleanup is recovered from import-job
  metadata.
- Strava tokens are encrypted at rest and account deletion requires explicit
  confirmation.
- Goal intake records intent only. It does not decide whether a goal is safe or
  realistic.

See [Current product flow](docs/current-product-flow.md) for the exact retained
state, callback, LLM, and persistence flow. Historical implementation decisions
and validation evidence are recorded in the
[active ExecPlan](.agent/execplans/onboarding-strava-vertical-slice.md).
