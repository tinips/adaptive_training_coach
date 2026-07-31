# Adaptive Endurance Coach

Adaptive Endurance Coach is a modular Python application that creates a
persisted athlete profile through Telegram and establishes a workout-data
baseline from Apple Health ZIP or TCX files. After onboarding, athletes can
continue their workout history with the same file-import path. Strava remains
an optional source when explicitly enabled. This repository contains the first
complete product vertical slice: onboarding, file-based workout logging, and
deterministic baseline measurement.

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
- Unified Apple Health ZIP and TCX import for resumable onboarding, multiple
  sequential files, daily workout logging, content-based format detection,
  workout enrichment, and deterministic baseline recalculation.
- Durable optional post-workout feedback for manual average heart rate,
  perceived effort, mobility or stretching, and non-diagnostic discomfort
  details.
- Strava OAuth 2.0 with random expiring one-time state, scope validation, and
  Fernet-encrypted tokens.
- Token refresh and rotation, paginated activity import, deduplication, manual
  synchronization, disconnect, rate-limit handling, and webhook ingestion.
- Deterministic discipline baselines with visible confidence and data freshness.
- FastAPI liveness, readiness, OAuth, callback, and webhook routes.
- Telegram `/start`, `/help`, `/profile`, `/baseline`, `/add_workout`,
  `/strava`, `/cancel`, and `/delete_me` commands.
- Async SQLAlchemy persistence, PostgreSQL, Alembic, Docker Compose, tests, Ruff,
  and mypy.

Manual baseline collection is an honest persisted extension point. It does not
fabricate baseline values in this milestone. A calibration-period button is
not shown because no real calibration workflow exists yet.

## Intentional non-goals

This version does not implement training-plan generation, adaptive replanning,
daily workout advice, a conversational coaching agent, RAG, embeddings, a
vector database, dashboards, Telegram Mini Apps, payments, nutrition planning,
medical diagnosis, or live Garmin/Coros/Xiaomi sync integrations.

Planning and the adaptive coach are the next product milestone.

See [Current product flow](docs/current-product-flow.md) for the editable,
as-implemented Telegram, import, feedback, and persistence flows.

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
                              ├─ Apple Health ZIP/XML and TCX parsers
                              ├─ deterministic workout-feedback flow
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

The Alembic migrations create:

- users and resumable onboarding sessions;
- athlete profiles, training goals, availability, equipment, non-diagnostic
  health constraints, coaching preferences, and baseline preferences;
- training-file import jobs, OAuth states, encrypted Strava connections,
  universal workouts, discipline-specific workout details, source-provenance
  links, optional workout feedback, resumable workout-flow state, sync jobs,
  and webhook events;
- versioned athlete and discipline baselines;
- safe LLM usage records.

Personal-data repository methods always include the owning internal user. OAuth
states are hashed, time-limited, and single-use. Imported workouts and source
links are scoped by owner and stable source key. Active training-file imports
and Strava sync jobs are serialized per user.

### Workout persistence model

`workouts` contains only fields shared by every supported discipline:

| Field | Meaning |
| --- | --- |
| `id` | Stable workout UUID |
| `athlete_id` | Owning `users.id`; every owned read/write checks this boundary |
| `discipline` | `RUNNING`, `CYCLING`, `HIKING`, `SWIMMING`, `STRENGTH`, or `OTHER` |
| `started_at` | Timezone-aware workout start |
| `duration_seconds` | Positive elapsed duration |
| `source` | `MANUAL`, `STRAVA`, `APPLE_HEALTH`, `TCX`, `FIT`, or `OTHER_IMPORT` |
| `external_id` | Nullable for manual workouts; required for imported source identities |
| `title`, `notes` | Optional user-readable text |
| `created_at`, `updated_at` | UTC audit timestamps |

Distance, moving duration, heart rate, calories, elevation, pace, speed,
cadence, pool data, strength sets, mobility, RPE, and discomfort are not generic
workout columns. Each workout has exactly one main detail record whose
`workout_id` is both its primary key and a cascading foreign key:

| Detail table | Subtype values and explicit units |
| --- | --- |
| `running_workout_details` | `OUTDOOR`, `TRAIL`, `TRACK`, `TREADMILL`; metres, seconds, seconds/km, metres of gain/loss, bpm, steps/minute |
| `cycling_workout_details` | `ROAD`, `MTB`, `GRAVEL`, `STATIONARY`, `OTHER`; metres, seconds, km/h, metres of gain/loss, bpm, revolutions/minute |
| `hiking_workout_details` | `HIKING`, `TREKKING`, `MOUNTAINEERING`, `SNOWSHOEING`, `OTHER`; metres, seconds, seconds/km, gain/loss metres, bpm, pack kg |
| `swimming_workout_details` | `POOL` or `OPEN_WATER`; metres, seconds, seconds/100 m, bpm |
| `strength_workout_details` | `GYM`, `CALISTHENICS`, `OTHER`; optional focus and validated exercise JSON |
| `other_workout_details` | Understandable activity name, optional description, raw sport/sub-sport, recognised distance/heart rate, and source-specific JSON metrics |

A `POOL` swim also requires one `pool_swimming_details` row with a positive
`pool_length_meters`; optional fields are total lengths, primary stroke
(`FREESTYLE`, `BREASTSTROKE`, `BACKSTROKE`, `BUTTERFLY`, `MIXED`, or `OTHER`),
average SWOLF, and total strokes. Its key references both `workouts.id` and the
matching swimming-detail key. `OPEN_WATER` swims must not have pool details.

Strength exercises are stored as validated JSON rather than separate tables:

```json
[
  {
    "exercise": "Pull-up",
    "sets": [
      {"reps": 10, "kg": 0},
      {"reps": 8, "kg": 10}
    ]
  }
]
```

Each exercise contains only its name and `sets`; each set contains only
non-negative `reps` and `kg`. An imported strength workout with no structured
exercise data uses an empty list and remains a valid workout.

When distance and moving duration are positive, running/hiking pace, swimming
pace per 100 metres, and cycling speed are derived from those SI values and
become canonical. A conflicting provider pace or speed does not replace the
derived value; the imported value and a normalization warning remain in source
metadata. Optional wearable metrics may remain `NULL`.

Unknown sports, plain walking, and swimming records whose pool/open-water
environment cannot be established conservatively are stored as `OTHER`, not
rejected or guessed. The original sport, sub-sport, provider summary, unsupported
metrics, and normalization fallback are retained in `activity_source_links`
and/or `other_workout_details.metrics_jsonb`. `FIT` is a supported source value,
but this milestone does not add a FIT parser.

Every source link also retains its source start, duration, raw metrics, and
file/import-job provenance. Reimporting a migrated workout keeps the complete
`0004` legacy envelope under `migration_provenance` while exposing the latest
provider snapshot at the top level, so legacy-only metrics are not overwritten.

The repository also exposes validated manual workout creation through the same
schema boundary. A manual unknown activity uses `OTHER` plus
`OtherWorkoutDetails`; it has no external ID. This persistence capability does
not add a new Telegram manual-workout conversation or public API route.

Migration `0004_discipline_workout_models` preserves workout UUIDs, ownership,
timestamps, source identities, feedback, import jobs, and baseline links while
replacing legacy `activities` with `workouts` plus detail rows. It creates
exactly one main detail per migrated workout, maps legacy discipline names to
the canonical values, retains the complete legacy row in source metadata, and
uses `OTHER` for an ambiguous swim instead of inventing an environment. A
legacy zero-second workout is retained with the minimum valid one-second
duration while its original value remains in migration provenance.
The downgrade reconstructs `0003` only while the canonical workout, detail,
pool detail, and all source-link state still match their migration snapshots;
otherwise it refuses before schema mutation instead of discarding post-upgrade
changes.

Migration `0005_remove_hr_observations` drops the obsolete
`heart_rate_observations` table without a backfill. Canonical average and
maximum heart rate already live on the matching workout discipline detail.

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

## Start the complete local application with Docker

Start Docker Desktop, then run these commands from the repository root. Run
the copy command only when `.env` does not already exist:

```powershell
Copy-Item .env.example .env
notepad .env
docker compose up --build
```

Before starting, set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, and
`APP_ENCRYPTION_KEY` in the ignored `.env`. The default `LLM_MODE=mock` needs no
live LLM credentials. Strava remains disabled, while Apple Health and TCX
imports remain enabled.

Compose starts PostgreSQL, waits for it to become healthy, runs
`alembic upgrade head` once, and only then starts FastAPI and one Telegram
long-polling bot. The API is available at `http://localhost:8000`; PostgreSQL is
also exposed at `localhost:55432` for local tools.

In another PowerShell terminal:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/ready
docker compose ps
docker compose logs migrate
```

Stop the application with `docker compose down`. PostgreSQL data survives that
command in the named volume. `docker compose down -v` permanently deletes the
local database, including all imported workouts, profiles, and onboarding
progress.

For detached startup and log following:

```powershell
docker compose up -d --build
docker compose ps
docker compose logs -f bot
docker compose logs -f api
docker compose down
```

Use the following destructive reset only when all local imported data may be
discarded:

```powershell
docker compose down -v
```

## Inspecting PostgreSQL with Adminer

Start the local Compose stack and confirm its services:

```powershell
docker compose up -d
docker compose ps
```

Open `http://localhost:8080` in a browser and use:

- System: `PostgreSQL`
- Server: `db`
- Username: `coach`
- Password: `coach`
- Database: `adaptive_coach`

The Server field must be `db`, not `localhost`, because Adminer runs inside the
Compose network.

Adminer provides direct database access. Editing or deleting rows bypasses
application validation. Prefer read-only inspection and `SELECT` queries.
Never expose port 8080 publicly. Do not use this Adminer setup as a production
admin panel.

`docker compose down` preserves PostgreSQL data in the named volume.
`docker compose down -v` deletes the PostgreSQL volume and all local data.

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
add_workout - Import a workout or training history
strava - View or manage Strava
cancel - Cancel active onboarding
delete_me - Request account deletion
```

Telegram uses long polling locally; no Telegram webhook is required.

## Training-file import

Apple Health ZIP and TCX import are enabled by default. Telegram filenames and
MIME types are treated only as hints: the service downloads to a generated
temporary path, monitors actual file growth against the configured ceiling,
inspects the actual content, calculates SHA-256, and then dispatches to the
appropriate bounded parser outside the async event loop. Unsupported, unsafe,
or oversized content fails without being interpreted as another format.

### Onboarding bulk import

At **How would you like to establish your initial training baseline?**, choose
**Import training history**. The persisted onboarding state then accepts:

- one Apple Health export ZIP;
- one or more TCX files, uploaded sequentially;
- an Apple Health ZIP followed by TCX files, or the reverse.

The other default choices are **Enter baseline manually**, **Decide later**,
and **Back**. **Connect Strava** appears only when `STRAVA_ENABLED=true`;
file import does not require Strava.

Each document is processed independently and returns concise imported, updated,
and skipped counts. The bot remains in the import state across uploads and
process restarts until **Finish import** is selected. Bulk Apple Health and
multi-TCX onboarding do not start a questionnaire for every historical
workout.

**Finish import** recalculates the deterministic baseline from owned canonical
workouts in the configured analysis window, persists `FILE_IMPORT` as the
baseline source, reports discipline coverage, and allows onboarding to continue
to the existing summary.
If the current import session contains no valid workout, the baseline remains
incomplete and the user can upload another file, choose manual entry, decide
later, or go back. When at least one workout exists, the bot may offer
**Add details to the most recent workout**; this is optional.

### Daily workout logging and feedback

After onboarding, use the **Add workout** menu action or `/add_workout`, then
send a TCX file or Apple Health export ZIP as a Telegram document. A supported
document sent directly while the profile is complete enters the same owned,
persisted import flow.

A single daily TCX import shows the normalized workout summary and then:

1. asks for manual average heart rate only when no reliable average exists;
2. asks optional perceived effort;
3. asks whether mobility or stretching was completed;
4. asks optional pain or unusual discomfort.

Apple Health ZIP is treated as historical backfill or enrichment and never
creates one questionnaire per imported workout. The deterministic baseline is
recalculated after a successful daily import and after measured data enriches
a workout. A daily file does not silently replace an existing Strava or
manual baseline-source preference.

Manual average heart rate accepts a whole number from 30 through 250 bpm and is
not persisted until the user confirms it. It is retained in the activity
feedback record and can become the canonical average only when no better metric
exists. Its provenance is `USER_REPORTED`, its temporal quality is `MANUAL`,
and it is not marked reliable. It is therefore not used for time in zones,
heart-rate distributions, maximum heart rate, cardiac drift, or other
sample-based metrics. A later reliable measured import may replace the
canonical average without deleting the reported value.

Perceived effort stores both the display label and this deterministic numeric
mapping:

| Button | Stored RPE |
| --- | ---: |
| Very easy | 2 |
| Easy | 4 |
| Moderate | 6 |
| Hard | 8 |
| Very hard | 10 |

RPE can be skipped. The mobility answer is stored in the feedback record as
`true` for **Yes**, `false` for **No**, or `NULL` for **Skip** or unknown.
Discomfort uses the same `true`/`false`/`NULL` distinction. A `Yes` discomfort
answer can include Shoulder, Back, Hip, Knee, Ankle or foot, or Other; optional
Mild, Moderate, or Severe intensity; and, for Other, a short confirmed free-text
description of at most 500 characters. These values are subjective workout
feedback, not a diagnosis. Back, skip, cancel, repeated callbacks, and
restart/resume use database-backed flow state rather than in-memory Telegram
state. Back from discomfort returns to mobility, and Back from mobility returns
to RPE. RPE, mobility, and discomfort are retained for future coaching and load
interpretation; they do not generate a diagnosis or training plan in this
milestone.

### TCX support and limits

The deterministic TCX parser accepts UTF-8 (with an optional UTF-8 BOM) Garmin
Training Center Database v1, v2, and unnamespaced documents containing exactly
one `Activity` and one or more `Lap` elements. Multiple laps and trackpoints
are supported. An empty `Track` is valid when lap summaries contain enough
information to identify and import the workout.

| TCX data | Import behavior |
| --- | --- |
| Identity and time | Uses `Activity/Id`, lap start time, and timezone-aware trackpoint timestamps. |
| Duration | Uses complete lap `TotalTimeSeconds`; otherwise derives a lap duration only from valid trackpoint timestamps. |
| Distance | Uses complete lap `DistanceMeters`; otherwise uses available cumulative trackpoint distance. |
| Heart rate | Uses timed trackpoint samples as measured data or complete lap summaries as provider data; missing HR remains `NULL`. |
| Calories | Sums complete lap summaries; incomplete totals remain `NULL`. |
| Cadence | Reads lap cadence, trackpoint cadence, and supported `RunCadence` extension values. |
| Altitude and route | Reads altitude and valid latitude/longitude trackpoints; elevation gain is derived from positive changes within contiguous altitude samples. |
| Sport | Preserves the original value and maps supported evidence to `RUNNING`, `CYCLING`, `HIKING`, `SWIMMING`, `STRENGTH`, or `OTHER`. Plain walking and swimming without explicit pool/open-water evidence use `OTHER`. A leading `YYYYMMDD` prefix is ignored for mapping. |

The importer does not parse GPX or FIT and does not support arbitrary XML,
unsupported TCX namespaces, or more than one `Activity` in a single TCX file.
`FIT` is accepted as a persisted source enum for future imports, but no FIT file
parser exists in this milestone. The importer does not fabricate missing moving
time, power, SWOLF, heart rate, cadence, elevation, or route data. When positive
distance and moving duration are both available, the workout schema derives
canonical pace or speed; otherwise those optional metrics remain `NULL`.
Malformed XML, DTD/entity declarations, unsupported roots, invalid required
identity, and files above the configured limit are rejected safely. UTF-16 and
other unsupported XML encodings are rejected rather than decoded heuristically.

### Exact import deduplication

Exact file replays are detected per user by SHA-256. Workout persistence then
uses one exact identity: `athlete_id + source + external_id`. Stable provider
identities are retained for TCX and Strava. When a source has no stable
external ID, Apple Health and ID-less TCX records receive a deterministic
`fingerprint:` identity derived from normalized source, discipline, UTC start,
duration, and distance.

Reimporting the same Apple Health workout, TCX activity, or Strava provider
activity refreshes that source record instead of creating another workout.
Different external IDs from one source remain separate, and different sources
are never merged automatically even when their timestamps and metrics are
identical. There are no time, duration, distance, ambiguity, confidence, or
metric-quality thresholds in workout deduplication.

Average and maximum heart rate are stored directly on the matching
discipline-specific workout detail. Source links retain exact identity, raw
sport/sub-sport, import traceability, provider metadata, and soft-deletion
state; they do not store heart-rate confidence, quality, sample count, source
rank, or replacement precedence.

### Apple Health ZIP details

On iPhone, create the export in:

```text
Health → profile picture → Export All Health Data
```

The import reads only `Workout`, child `WorkoutStatistics`, and heart-rate
`Record` elements needed for training analysis. Clinical records and unrelated
health categories are ignored. It maps supported evidence to the same six
canonical disciplines, preserves the Apple workout type and raw source
metadata, and converts documented duration, distance, energy, and heart-rate
units deterministically. Plain walking and swimming whose environment cannot be
established are retained as `OTHER`, including their understandable activity
name and available metrics. Unsupported units are omitted with a warning or
fail safely when the field is required.

Before XML parsing, the service verifies ZIP magic and configured limits,
rejects traversal/absolute/drive paths, links, encryption, nested archives,
conflicting duplicate names, and excessive compression. It discovers the
primary XML by its `HealthData` root, so localized and Unicode paths work.
Parsing uses two bounded streaming passes on a worker thread. External DTDs,
entity declarations, network resolution, malformed XML, and unsafe expansion
are rejected; ordinary internal Apple-style DTD declarations are tolerated.

Heart-rate records are matched transiently by workout overlap with same-source
preference. Exact and short observations may contribute to the detail model's
average and maximum. Coarse intervals are classified and counted, may
contribute only to the maximum, and never become invented samples or exact
averages. Individual heart-rate records are not persisted.

Uploaded ZIP, XML, and TCX content uses generated temporary paths, never the
Telegram filename, and is deleted after success or failure. Raw file content is
not stored in PostgreSQL or application logs. Only normalized workouts, their
discipline details (including canonical average/maximum heart rate),
source identity and provider traceability, optional feedback, safe counters,
and baseline versions are persisted. The generated path is associated with the
durable import job before document bytes are downloaded. On bot startup, prior
process jobs are failed, onboarding is restored to its waiting state, and any
recorded temporary uploads are deleted before new updates are accepted.

Resource ceilings and enablement flags can be adjusted without adding secrets.
Keep original-file retention disabled so the documented cleanup contract
remains in force:

```dotenv
APPLE_HEALTH_IMPORT_ENABLED=true
APPLE_HEALTH_IMPORT_MAX_COMPRESSED_SIZE_MB=100
APPLE_HEALTH_IMPORT_MAX_UNCOMPRESSED_SIZE_MB=1024
APPLE_HEALTH_IMPORT_MAX_ZIP_MEMBERS=100
APPLE_HEALTH_IMPORT_MAX_COMPRESSION_RATIO=200
APPLE_HEALTH_IMPORT_TEMP_DIR=
# Keep false: raw training uploads must not be retained.
APPLE_HEALTH_IMPORT_KEEP_ORIGINAL_FILES=false
TCX_IMPORT_ENABLED=true
TCX_IMPORT_MAX_SIZE_MB=25
WORKOUT_FEEDBACK_ENABLED=true
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

## Run the processes without Compose (optional)

For local Python development, first start only PostgreSQL and migrate it:

```powershell
docker compose up -d db
Set-Location backend
.\.venv\Scripts\Activate.ps1
alembic upgrade head
```

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
token only to notify the OAuth owner after a successful initial Strava import.

## First live Telegram import test

No credentialed Telegram upload is performed by the automated suite. For the
first live test, use a development bot and a non-sensitive TCX file whose
metrics you can verify. Do not commit the bot token, `.env`, Apple Health
export, or TCX file.

From the repository root, prepare configuration:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

In `.env`, set a real `TELEGRAM_BOT_TOKEN`, the BotFather username without
`@` in `TELEGRAM_BOT_USERNAME`, and a generated `APP_ENCRYPTION_KEY`. Keep:

```dotenv
LLM_MODE=mock
STRAVA_ENABLED=false
APPLE_HEALTH_IMPORT_ENABLED=true
TCX_IMPORT_ENABLED=true
WORKOUT_FEEDBACK_ENABLED=true
```

Start the complete application:

```powershell
docker compose up --build
```

Confirm runtime health and the completed migration in a second PowerShell
terminal:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/ready
docker compose ps
docker compose logs migrate
```

Then perform the live chat test:

1. Send `/start`, complete onboarding to the baseline-source question, and
   choose **Import training history**.
2. Send one TCX as a Telegram **Document**, verify the sport, date, duration,
   distance, and average-HR summary, then send a second file to verify
   sequential import.
3. Select **Finish import**, confirm that the discipline summary appears, and
   complete the existing onboarding summary.
4. Send `/add_workout`, upload a single new TCX, and verify that reliable HR
   skips manual entry while missing HR offers the confirmed 30–250 bpm path.
5. Exercise one RPE choice, one mobility choice, and one discomfort choice,
   then use `/baseline` to confirm that a new deterministic baseline is
   available.
6. With the profile complete and no `/add_workout` prompt active, send another
   supported document directly and confirm content-based dispatch.
7. Optionally upload an Apple Health export ZIP to verify historical backfill;
   use only a file whose privacy implications you have reviewed.

Record the observed bot responses and check application logs only for safe
event metadata. Successful startup alone does not validate live document
delivery; the test is complete only after the real bot receives and processes
the document.

## Product journey

1. Send `/start`.
2. Accept the safety and privacy notice.
3. Complete button-first onboarding.
4. For an `Other`/`Write answer` path, review the structured interpretation and
   choose `Correct` before it is staged.
5. Choose training-history import, manual entry, decide later, or Strava when
   `STRAVA_ENABLED=true`.
6. For file import, upload Apple Health ZIP and/or sequential TCX documents,
   review each result, then select **Finish import**.
7. Review the imported discipline summary and confirm the full profile.
8. After onboarding, use `/add_workout`, **Add workout**, or direct supported
   document delivery to update workout history.
9. For a daily TCX, complete or skip the persisted HR, RPE, mobility, and
   discomfort questions.
10. If using Strava, open the opaque one-time connection link and authenticate
   directly on Strava. Never send Strava credentials in Telegram.
11. After the callback, use **Open Telegram** on the success page. The bot sends
   the correct Telegram user an English confirmation after the initial import
   completes.
12. Return to Telegram to view sync state, the imported-data summary, and
   discipline confidence.

Progress is stored after each confirmed step and survives process restarts.
Telegram users are isolated by stable Telegram identity and internal UUID.

## Strava data behavior

- The default initial window is 56 days.
- Activity summaries are paginated until an empty page and locally checked
  against the cutoff.
- Optional distance, elevation, heart-rate, speed, and power fields may be
  absent. Provider speed and power are retained in source metadata; typed pace
  or speed is derived only when positive distance and moving duration exist.
- Supported evidence maps to `RUNNING`, `CYCLING`, `HIKING`, `SWIMMING`,
  `STRENGTH`, or `OTHER`. Plain walking and swimming with no defensible
  environment use `OTHER`; the original Strava sport and sub-sport are retained.
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

- workout count and active weeks;
- total and average weekly duration;
- total and average weekly distance where meaningful;
- longest duration and distance;
- recent 14-day sessions and data recency;
- consistency indicators;
- a visible confidence score;
- `UNKNOWN`, `BEGINNER`, `DEVELOPING`, `INTERMEDIATE`, or `ADVANCED`.

The shared default analysis window is 56 days for file and Strava sources.
Thresholds are centralized and tested. They are provisional product heuristics,
not scientifically validated fitness classifications and not physiological or
medical diagnoses. Missing data is shown rather than inferred. The LLM never
calculates numeric baseline metrics.

A new baseline version is generated after onboarding file import is finished,
a successful daily training-file import, measured activity enrichment, initial
Strava sync, successful manual Strava sync, meaningful webhook changes, or an
explicit recalculation. Manual average HR alone does not create precise zones
or thresholds. Insufficient data remains visibly partial or `UNKNOWN` rather
than being inferred.

## Testing and static analysis

From `backend` with the virtual environment active:

```powershell
pytest -q
ruff check .
ruff format --check .
mypy app
alembic check
```

Focused examples:

```powershell
pytest -q tests/unit
pytest -q tests/use_cases
pytest -q tests/integration
pytest -q tests/scenarios
```

The default suite must not make real provider calls. OAuth, token rotation,
activity pages, rate limits, webhook events, and model outcomes use test
doubles. TCX and Apple Health tests use synthetic fixtures only.

For the PostgreSQL migration and runtime checks, start from the repository root:

```powershell
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps
docker compose logs migrate
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/ready
```

Constructing the Telegram application or running unit tests does not prove that
a real bot received a document; use the explicit live procedure above for that
claim.

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
- OAuth state, callbacks, import jobs, source links, workouts, feedback,
  baselines, and profile reads are ownership-scoped.
- Apple Health ZIP/XML and TCX content is deleted after processing and is never
  stored as a raw database payload. Generated temporary paths do not use the
  Telegram filename; cleanup runs after success or failure and is retried from
  recorded import metadata after a bot-process restart.
- File contents, raw health data, complete profiles, OAuth tokens, and
  unredacted workout-feedback text are excluded from application logs.
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
- TCX supports exactly one activity per file. GPX and FIT parsing,
  multiple-activity TCX, moving-time inference, power, and SWOLF are outside
  this goal. Pace and speed are derived only from available positive distance
  and moving duration; provider conflicts are retained as warnings rather than
  overriding the derived value.
- Automated tests use synthetic Apple Health and TCX data. A real Telegram bot
  receiving a real Apple Health ZIP or TCX document was not validated in this
  environment.
- Background sync and webhook work uses a durable PostgreSQL inbox/lease with
  startup recovery, not a distributed task queue. Running multiple production
  worker processes would require an explicit deployment review.
- Training-file recovery is intentionally owned by the single Telegram delivery
  worker, not the FastAPI process. Multiple Telegram bot workers would require
  an explicit lease/ownership design before deployment.
- Baseline labels are deterministic provisional heuristics, not scientific
  fitness classifications.

## Implementation record

The living execution record, discoveries, validation failures/fixes, and final
evidence are maintained in
[the active ExecPlan](.agent/execplans/onboarding-strava-vertical-slice.md).
