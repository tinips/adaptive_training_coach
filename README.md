# Adaptive Endurance Coach

Adaptive Endurance Coach is a modular Python application that creates a
persisted athlete profile through Telegram and establishes an activity-data
baseline from Strava. This repository contains the first complete product
vertical slice: onboarding and baseline measurement.

The interface is English-only for this milestone. Explicit free-text answers
may be written in any language, including English, Catalan, and Spanish.

This software does not provide medical advice, must not be used for
emergencies, and does not diagnose fitness or health conditions.

## Implemented vertical slice

- Multi-user Telegram onboarding with persisted progress after every confirmed
  step.
- Button-first deterministic choices, conditional step skipping, safe back/edit
  behavior, cancellation, and account-deletion confirmation.
- Structured free-text interpretation through one compiled LangGraph workflow
  and LangChain model abstraction.
- Explicit confirmation before an interpreted answer enters onboarding staging.
- Database-backed live-LLM rolling-hour rate limiting.
- Atomic, idempotent profile finalization into normalized records.
- Strava OAuth 2.0 with random expiring one-time state, scope validation, and
  Fernet-encrypted tokens.
- Token refresh and rotation, paginated activity import, deduplication, manual
  synchronization, disconnect, rate-limit handling, and webhook ingestion.
- Deterministic discipline baselines with visible confidence and data freshness.
- FastAPI liveness, readiness, OAuth, callback, and webhook routes.
- Telegram `/start`, `/help`, `/profile`, `/baseline`, `/strava`, `/cancel`, and
  `/delete_me` commands.
- Async SQLAlchemy persistence, PostgreSQL, Alembic, Docker Compose, tests, Ruff,
  and mypy.

Manual baseline collection and calibration-period measurement are honest
persisted selections and extension points. They do not fabricate baseline
values in this milestone.

## Intentional non-goals

This version does not implement training-plan generation, adaptive replanning,
daily workout advice, a conversational coaching agent, RAG, embeddings, a
vector database, dashboards, Telegram Mini Apps, payments, nutrition planning,
medical diagnosis, or Garmin/Coros/Xiaomi integrations.

Planning and the adaptive coach are the next product milestone.

## Architecture

The project is a modular monolith with two runnable processes:

1. FastAPI handles health/readiness and Strava browser/webhook traffic.
2. `python-telegram-bot` handles Telegram long polling.

Both processes use the same application-service and repository boundaries.
PostgreSQL is the only source of truth.

```text
Telegram handlers ─┐
                   ├─ application services ─ repositories ─ PostgreSQL
FastAPI routes ────┘          │
                              ├─ LangGraph ─ LangChain model
                              ├─ Strava HTTP client
                              └─ deterministic baseline engine
```

Telegram handlers only extract delivery data, call services, and render
results. They do not query SQL, transition onboarding, call a model or Strava,
or calculate baselines.

### LangChain and LangGraph

Deterministic button callbacks use ordinary application code and never invoke a
model.

An explicit `Other` or `Write answer` path invokes one stateless, compiled
LangGraph:

```text
START
  -> parse_with_model
  -> validate_structured_output
  -> route_result
       -> confirmation_required
       -> clarification_required
       -> fallback_required
       -> provider_error
  -> END
```

LangChain supplies the OpenAI-compatible chat-model abstraction and structured
Pydantic output. Both `LLM_MODE=mock` and `LLM_MODE=live` use the same graph
topology. The mock is deterministic and supports success, low-confidence,
clarification, malformed-output, timeout, and provider-failure scenarios.

The graph receives only the current step, current free text, and minimal
confirmed context. It has no database or Strava access and does not persist a
checkpoint.

### Future Langfuse integration

Langfuse is intentionally not installed or required. A vendor-neutral
`AIWorkflowObserver` protocol, no-op implementation, and centralized callback
configuration provide a later integration point. Current observability uses
safe application logs, `llm_usage` rows, and the no-op observer. Raw free text,
health descriptions, complete profiles, prompts, credentials, and OAuth tokens
are excluded from observer metadata.

### Persistence and ownership

The initial Alembic migration creates:

- users and resumable onboarding sessions;
- athlete profiles, training goals, availability, equipment, non-diagnostic
  health constraints, coaching preferences, and baseline preferences;
- OAuth states, encrypted Strava connections, normalized activities, sync jobs,
  and webhook events;
- versioned athlete and discipline baselines;
- safe LLM usage records.

Personal-data repository methods always include the owning internal user. OAuth
states are hashed, time-limited, and single-use. Imported activities are unique
by provider and external ID. Active sync jobs are serialized per user.

## Repository layout

```text
.
├── AGENTS.md
├── .agent/
│   ├── PLANS.md
│   └── execplans/onboarding-strava-vertical-slice.md
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── bot/
│   │   ├── db/
│   │   ├── integrations/
│   │   ├── observability/
│   │   ├── repositories/
│   │   ├── schemas/
│   │   ├── security/
│   │   ├── services/
│   │   └── workflows/
│   ├── alembic/
│   ├── scripts/
│   ├── tests/
│   ├── alembic.ini
│   └── pyproject.toml
├── .env.example
├── docker-compose.yml
└── README.md
```

## Requirements

- Windows 10 or 11
- PowerShell
- Python 3.12 or newer
- Docker Desktop with Linux containers
- A Telegram bot token for live Telegram testing
- A Strava developer application for live Strava testing
- Optionally, an OpenAI-compatible API key for `LLM_MODE=live`
- A public HTTPS URL for live OAuth callbacks and Strava webhooks

All automated tests use fakes or HTTP mock transports and do not call Telegram,
Strava, or an LLM provider.

## Windows PowerShell setup

From the repository root:

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"

cd ..
Copy-Item .env.example .env
```

The repository may already contain a local ignored `.env`. Do not overwrite it
if it contains credentials you want to keep; add the missing keys from
`.env.example` instead.

Generate the application encryption key:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output into `APP_ENCRYPTION_KEY` in `.env`. Never commit that file or
reuse a development key in production. Losing or changing the key makes stored
Strava tokens unreadable and requires users to reconnect.

## Start PostgreSQL and run migrations

Start Docker Desktop, then from the repository root:

```powershell
docker compose up -d db
docker compose ps

cd backend
.\.venv\Scripts\Activate.ps1
alembic upgrade head
```

To inspect migration state:

```powershell
alembic current
alembic history
```

The default local database is exposed on `localhost:55432` with the disposable
Docker development credentials shown in `docker-compose.yml`.

## Telegram BotFather setup

1. Open [@BotFather](https://t.me/BotFather) in Telegram.
2. Run `/newbot` and follow the prompts.
3. Put the returned token in `TELEGRAM_BOT_TOKEN` in the ignored `.env`.
4. Optionally use `/setcommands` with:

```text
start - Start or resume onboarding
help - Show help and safety information
profile - View the saved athlete profile
baseline - View the current activity baseline
strava - View or manage Strava
cancel - Cancel active onboarding
delete_me - Request account deletion
```

Telegram uses long polling locally; no Telegram webhook is required.

## Strava developer application setup

Create an application in the
[Strava API settings](https://www.strava.com/settings/api), then configure:

- `STRAVA_CLIENT_ID`
- `STRAVA_CLIENT_SECRET`
- `STRAVA_REDIRECT_URI`
- `PUBLIC_BASE_URL`
- `STRAVA_WEBHOOK_VERIFY_TOKEN`

The redirect URI used by this application is:

```text
https://YOUR-PUBLIC-HOST/integrations/strava/callback
```

Configure the matching callback host in the Strava developer application.
Requesting `activity:read` is sufficient for public/follower activity summaries.
Use `activity:read_all` only if importing private activities and privacy-zone
data is an intentional consent choice. The application validates the scopes
the athlete actually grants.

The implementation follows Strava's official
[authentication](https://developers.strava.com/docs/authentication/),
[activity reference](https://developers.strava.com/docs/reference/), and
[webhook](https://developers.strava.com/docs/webhooks/) documentation.

### Local callback limitation and HTTPS tunnel

Strava and a remote Telegram client cannot reach `localhost`. For live OAuth or
webhook testing, expose FastAPI through a trusted public HTTPS tunnel, for
example:

```powershell
# Example only; use a tunnel tool you trust.
cloudflared tunnel --url http://localhost:8000
```

Set `PUBLIC_BASE_URL` to the resulting HTTPS origin and
`STRAVA_REDIRECT_URI` to its callback path. Restart FastAPI after changing
configuration. Do not publish PostgreSQL through the tunnel.

### Webhook subscription

Start FastAPI at the public HTTPS URL before creating the subscription.
Strava permits one webhook subscription per application and verifies the
callback immediately.

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python scripts/create_strava_webhook.py
```

The script reads credentials from the ignored `.env`, refuses a non-HTTPS public
URL, and does not print secrets. Save the returned subscription ID as
`STRAVA_WEBHOOK_SUBSCRIPTION_ID`.

Live webhook behavior is not validated merely by running tests. It requires a
public callback, valid Strava credentials, successful subscription
verification, and a real provider event. Manual `Sync now` remains available
when webhooks are not configured.

## LLM configuration

Mock mode is the default:

```dotenv
LLM_MODE=mock
```

The API, bot, and deterministic onboarding paths start without an LLM key.
Mock-mode free text still traverses LangChain and the compiled LangGraph.

For an OpenAI-compatible provider:

```dotenv
LLM_MODE=live
LLM_API_KEY=replace-locally
LLM_BASE_URL=https://provider.example/v1
LLM_MODEL=provider-economical-model
LLM_MIN_CONFIDENCE=0.75
LLM_OTHER_REQUESTS_PER_HOUR=10
```

Leave `LLM_BASE_URL` empty for a provider that uses the integration default.
The key is required only when a live free-text parse is attempted. A provider
failure never confirms or overwrites an onboarding answer.

## Start both processes

Terminal 1 — FastAPI:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.api.main:app --reload --no-access-log
```

Check:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/ready
```

Terminal 2 — Telegram long polling:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m app.bot.main
```

The API and bot must use the same `DATABASE_URL` and
`APP_ENCRYPTION_KEY`.

## Product journey

1. Send `/start`.
2. Accept the safety and privacy notice.
3. Complete button-first onboarding.
4. For an `Other`/`Write answer` path, review the structured interpretation and
   choose `Correct` before it is staged.
5. Review and confirm the full profile.
6. Select Strava, manual baseline, calibration, or decide later.
7. If using Strava, open the opaque one-time connection link and authorize.
8. Return to Telegram to view sync state, the imported-data summary, and
   discipline confidence.

Progress is stored after each confirmed step and survives process restarts.
Telegram users are isolated by stable Telegram identity and internal UUID.

## Strava data behavior

- The default initial window is 56 days.
- Activity summaries are paginated until an empty page and locally checked
  against the cutoff.
- Optional distance, elevation, heart-rate, speed, and power fields may be
  absent.
- Run, ride, swim, strength, walk/hike, and other categories are normalized;
  the original Strava sport type is retained.
- Before an authenticated request, expiring credentials are refreshed and both
  rotated tokens are stored atomically.
- All overall and read-specific rate-limit headers are captured. `429` stops
  the job without an aggressive retry loop.
- Manual sync has a cooldown and rejects a concurrent active job.
- Durable sync leases and accepted webhook inbox items are reconciled after a
  process restart; duplicate webhook delivery remains idempotent.
- Disconnect uses Strava's current OAuth revocation endpoint, erases local
  encrypted tokens, and preserves imported summaries for baseline/audit use.
- `/delete_me` removes the user's application data after confirmation.

## Deterministic baseline

For each discipline, the engine calculates:

- activity count and active weeks;
- total and average weekly duration;
- total and average weekly distance where meaningful;
- longest duration and distance;
- recent 14-day sessions and data recency;
- consistency indicators;
- a visible confidence score;
- `UNKNOWN`, `BEGINNER`, `DEVELOPING`, `INTERMEDIATE`, or `ADVANCED`.

Thresholds are centralized and tested. They are provisional product heuristics,
not scientifically validated fitness classifications and not physiological or
medical diagnoses. Missing data is shown rather than inferred. The LLM never
calculates numeric baseline metrics.

A new baseline version is generated after initial sync, successful manual sync,
meaningful webhook changes, or an explicit recalculation.

## Testing and static analysis

From `backend` with the virtual environment active:

```powershell
pytest
ruff check .
ruff format --check .
mypy app
```

Focused examples:

```powershell
pytest tests/unit
pytest tests/use_cases
pytest tests/integration
pytest tests/scenarios
```

The default suite must not make real provider calls. OAuth, token rotation,
activity pages, rate limits, webhook events, and model outcomes use test
doubles.

## Privacy and security notes

- `.env` is ignored; `.env.example` contains no secrets.
- Telegram, LLM, Strava, encryption, and database credentials are never logged.
- The documented Uvicorn command disables access logs because OAuth callbacks
  carry short-lived bearer values in their query string. Application logging
  also redacts OAuth query values, Telegram bot URLs, and database passwords.
- OAuth access and refresh tokens are encrypted at rest.
- Health limitations are user-stated constraints; the application does not
  infer or store diagnoses.
- Raw LLM prompts and raw health descriptions are not written to `llm_usage`.
- Interpreted values require confirmation, and low-confidence values are not
  stored.
- OAuth state, callbacks, records, activities, baselines, and profile reads are
  ownership-scoped.
- Disconnect and account deletion both require explicit confirmation.

Review retention, consent, encryption-key management, database backups, tunnel
security, and webhook operation before any production deployment. Production
cloud deployment is outside this milestone.

## Known limitations

- Live Telegram polling requires a BotFather token and a manual chat
  interaction; automated tests do not contact Telegram.
- Live Strava OAuth and webhook validation require a developer application,
  public HTTPS callback, subscription, and real athlete authorization.
- `activity:read` deliberately excludes activities with Only Me visibility.
- Manual-baseline and calibration-period choices are persisted, but their
  measurement workflows are next-milestone extension points and create no
  fabricated metrics.
- Background sync and webhook work uses a durable PostgreSQL inbox/lease with
  startup recovery, not a distributed task queue. Running multiple production
  worker processes would require an explicit deployment review.
- Baseline labels are deterministic provisional heuristics, not scientific
  fitness classifications.

## Implementation record

The living execution record, discoveries, validation failures/fixes, and final
evidence are maintained in
[the active ExecPlan](.agent/execplans/onboarding-strava-vertical-slice.md).
