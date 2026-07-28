# Adaptive Endurance Coach

Adaptive Endurance Coach is a modular Python application that creates a
persisted athlete profile through Telegram and establishes an activity-data
baseline from an Apple Health export or, when enabled, Strava. This repository
contains the first complete product vertical slice: onboarding and baseline
measurement.

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
- Secure Apple Health ZIP import with a privacy notice, persisted progress,
  streaming XML parsing, activity/heart-rate deduplication, and automatic
  baseline recalculation.
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

Manual baseline collection is an honest persisted extension point. It does not
fabricate baseline values in this milestone. A calibration-period button is
not shown because no real calibration workflow exists yet.

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
                              ├─ Apple Health ZIP/XML parser
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
- Apple Health import jobs, normalized heart-rate observations, OAuth states,
  encrypted Strava connections, normalized activities, sync jobs, and webhook
  events;
- versioned athlete and discipline baselines;
- safe LLM usage records.

Personal-data repository methods always include the owning internal user. OAuth
states are hashed, time-limited, and single-use. Imported activities are unique
by owner, provider, and stable source key. Active Apple Health imports and
Strava sync jobs are serialized per user.

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
- Optionally, a Strava developer application and public HTTPS URL for live
  Strava OAuth/webhook testing
- Optionally, a DeepSeek API key for `LLM_MODE=live`

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
4. Put the public bot username, without the leading `@`, in
   `TELEGRAM_BOT_USERNAME`. The OAuth success page uses it for its
   **Open Telegram** link.
5. Optionally use `/setcommands` with:

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

## Apple Health export import

Apple Health import is enabled by default. During the baseline-source step,
choose **Import Apple Health data**, review the privacy notice, choose
**Continue**, then send the export as a Telegram document. A ZIP sent before
the notice is accepted is rejected without parsing.

On iPhone, create the file in:

```text
Health → profile picture → Export All Health Data
```

The import reads only `Workout`, child `WorkoutStatistics`, and heart-rate
`Record` elements needed for training analysis. Clinical records and unrelated
health categories are ignored. It normalizes run, ride, swim, walk/hike,
strength, and other workouts; preserves the Apple workout type; and converts
documented duration, distance, energy, and heart-rate units deterministically.
Unsupported units are omitted with a warning or fail safely when the field is
required.

Before XML parsing, the service verifies ZIP magic and configured limits,
rejects traversal/absolute/drive paths, links, encryption, nested archives,
conflicting duplicate names, and excessive compression. It discovers the
primary XML by its `HealthData` root, so localized and Unicode paths work.
Parsing uses two bounded streaming passes on a worker thread. External DTDs,
entity declarations, network resolution, malformed XML, and unsafe expansion
are rejected; ordinary internal Apple-style DTD declarations are tolerated.

Heart-rate records are matched by workout overlap with same-source preference.
Exact and short observations may contribute to averages. Coarse intervals are
preserved and flagged, may contribute only to a maximum, and never become
invented samples or exact averages.

Uploaded files use generated temporary paths, never the Telegram filename, and
are deleted after success or failure by default. Raw ZIP/XML content is not
stored in PostgreSQL or application logs. Normalized activities, relevant
heart-rate observations, safe import counters, and baseline versions are
persisted. Re-importing cumulative exports adds or enriches records without
duplicating or deleting earlier activities.

Resource ceilings can be adjusted without adding secrets:

```dotenv
APPLE_HEALTH_IMPORT_ENABLED=true
APPLE_HEALTH_IMPORT_MAX_COMPRESSED_SIZE_MB=100
APPLE_HEALTH_IMPORT_MAX_UNCOMPRESSED_SIZE_MB=1024
APPLE_HEALTH_IMPORT_MAX_ZIP_MEMBERS=100
APPLE_HEALTH_IMPORT_MAX_COMPRESSION_RATIO=200
APPLE_HEALTH_IMPORT_TEMP_DIR=
APPLE_HEALTH_IMPORT_KEEP_ORIGINAL_FILES=false
```

## Strava developer application setup

Strava is optional and disabled by default. The application and Telegram bot
start without Strava credentials. To enable it, set:

```dotenv
STRAVA_ENABLED=true
```

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
The application requests exactly `read` and `activity:read_all`. This provides
public profile access and read-only access to activity summaries, including
activities whose visibility is Only You. It does not request profile or
activity write access. The callback validates both the callback grant and the
token response before retaining a connection.

Users never provide a Strava username or password to this application. The
Telegram button opens the backend's one-time connect endpoint, which redirects
the browser to Strava. Authentication and consent happen directly on Strava.
The application stores only a digest of each expiring state, consumes it once,
and encrypts the returned access and refresh tokens before persistence.

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

Live mode uses DeepSeek's OpenAI-compatible API:

```dotenv
LLM_MODE=live
LLM_API_KEY=replace-locally
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_MIN_CONFIDENCE=0.75
LLM_OTHER_REQUESTS_PER_HOUR=10
```

The key is required only when a live free-text parse is attempted. Onboarding
extraction uses LangChain JSON-mode structured output through the same compiled
LangGraph used by mock mode. DeepSeek thinking is explicitly disabled for these
requests with `{"thinking": {"type": "disabled"}}`. A provider failure never
confirms or overwrites an onboarding answer. See DeepSeek's official
[model](https://api-docs.deepseek.com/quick_start/pricing/) and
[thinking-mode](https://api-docs.deepseek.com/guides/thinking_mode)
documentation.

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

The API and bot must use the same `DATABASE_URL`, `APP_ENCRYPTION_KEY`,
`TELEGRAM_BOT_TOKEN`, and `TELEGRAM_BOT_USERNAME`. The API uses the Telegram
token only to notify the OAuth owner after a successful initial import.

## Product journey

1. Send `/start`.
2. Accept the safety and privacy notice.
3. Complete button-first onboarding.
4. For an `Other`/`Write answer` path, review the structured interpretation and
   choose `Correct` before it is staged.
5. Choose Apple Health import, manual entry, decide later, or Strava when
   `STRAVA_ENABLED=true`.
6. For Apple Health, accept the privacy notice and upload the ZIP document.
   Review the real import counters, then continue to the profile summary.
7. Review and confirm the full profile.
8. If using Strava, open the opaque one-time connection link and authenticate
   directly on Strava. Never send Strava credentials in Telegram.
9. After the callback, use **Open Telegram** on the success page. The bot sends
   the correct Telegram user an English confirmation after the initial import
   completes.
10. Return to Telegram to view sync state, the imported-data summary, and
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
Apple Health import also generates a deterministic baseline version after its
normalized activity transaction succeeds. Insufficient data remains visibly
unknown rather than being inferred.

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
- Apple Health ZIPs and XML are deleted after processing and are never stored
  as raw database payloads; only matched training observations are retained.
- Strava usernames and passwords are never requested or accepted in Telegram;
  credentials are entered only on Strava's authorization page.
- Disconnect and account deletion both require explicit confirmation.

Review retention, consent, encryption-key management, database backups, tunnel
security, and webhook operation before any production deployment. Production
cloud deployment is outside this milestone.

## Known limitations

- Live Telegram polling requires a BotFather token and a manual chat
  interaction; automated tests do not contact Telegram.
- Live Strava OAuth and webhook validation require a developer application,
  public HTTPS callback, subscription, and real athlete authorization.
- `activity:read_all` includes activities with Only You visibility and should
  be enabled only for athletes who consent to that read-only import.
- Manual-baseline measurement is a next-milestone extension point and creates
  no fabricated metrics. Calibration is hidden until a real workflow exists.
- Automated tests exercise Apple Health document delivery with local fixtures.
  A real Telegram bot receiving a real Apple Health export was not validated
  in this environment.
- Background sync and webhook work uses a durable PostgreSQL inbox/lease with
  startup recovery, not a distributed task queue. Running multiple production
  worker processes would require an explicit deployment review.
- Baseline labels are deterministic provisional heuristics, not scientific
  fitness classifications.

## Implementation record

The living execution record, discoveries, validation failures/fixes, and final
evidence are maintained in
[the active ExecPlan](.agent/execplans/onboarding-strava-vertical-slice.md).
