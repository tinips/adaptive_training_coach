# Adaptive Endurance Coach

Adaptive Endurance Coach is a modular Python application for a Telegram-based
endurance coach. The currently supported onboarding slice records and confirms
one athlete goal. It deliberately stops there; availability, injuries,
baseline selection, feasibility, and plan generation are future onboarding
steps.

The interface is English-only. Goal answers may be written naturally in any
language supported by the configured model. The application does not provide
medical advice and must not be used for emergencies.

## Current Telegram onboarding

The only supported journey is:

1. Telegram **Start** opens Welcome, Help, and Privacy & safety.
2. **Let's go** opens explicit consent.
3. Consent opens the setup introduction.
4. **Let's build my profile** opens a free-text goal question.
5. A focused LangGraph/LangChain operation extracts a four-field draft.
6. The bot asks one clarification at a time until the goal is clear.
7. The user can confirm, add or change information, start again, or cancel.
8. **No, that's right** writes the canonical goal and moves the session to
   `GOAL_CONFIRMED`.
9. The bot shows the terminal saved-goal checkpoint and **Back to welcome**.

The terminal copy is:

```text
Your goal has been saved.

This is the first part of your athlete profile. We'll continue building the rest of your profile step by step.
```

Confirmation does not mark the athlete profile complete, start an import,
select a baseline, calculate feasibility, or generate a plan.

The durable onboarding states are:

- `CONSENT`
- `SETUP_INTRODUCTION`
- `GOAL_INTAKE`
- `GOAL_CONFIRMED`

Cancellation is stored as an onboarding-session status. Restart returns only
that user's session to consent.

## Goal extraction and persistence

Deterministic callbacks do not invoke a model. Free-text goal messages use one
compiled, stateless goal-extraction graph with this structured contract:

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

During intake, `onboarding_sessions.answers` holds only the relevant temporary
state: consent, the first raw goal message, goal messages from this step, the
structured draft, phase, and optional clarification metadata. An off-topic
answer is removed from the retained goal messages and cannot alter the draft.

The graph cannot write canonical data. Only explicit confirmation calls the
single canonical writer in `ProfileRepository`, which upserts one
`training_goals` row containing:

- `main_goal`
- `event_date`
- `target_outcome`
- `secondary_priority`
- `original_description`
- `status=CONFIRMED`

After confirmation, temporary draft and clarification state are removed. The
original goal text and the relevant goal-message audit trail remain in the
onboarding session.

## Architecture

The project is a modular monolith with two processes:

1. FastAPI serves health/readiness plus optional Strava OAuth and webhook
   routes.
2. `python-telegram-bot` runs Telegram long polling.

Both use the same application-service and repository boundaries. PostgreSQL is
the source of truth; LangGraph has no database checkpointing.

```text
Telegram handlers ---- application services ---- repositories ---- PostgreSQL
                              |
                              +-- focused goal LangGraph / LangChain model
                              +-- Apple Health ZIP and TCX parsers
                              +-- workout-feedback state service
                              +-- Strava client and sync orchestration
                              +-- deterministic baseline engine
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
for existing athletes still depend on them. Removed onboarding code no longer
writes athlete demographics, availability, equipment, health constraints,
coach preferences, or baseline preferences.

## Intentional non-goals

This slice does not implement:

- the rest of athlete-profile onboarding;
- availability or injury collection;
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
- makes legacy `goal_type` and `goal_priority` nullable for newly confirmed
  conversational goals;
- preserves users, canonical goals, historical normalized profiles, workouts,
  import jobs, feedback, baselines, and Strava data.

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
