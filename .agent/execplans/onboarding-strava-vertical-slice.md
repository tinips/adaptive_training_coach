# Onboarding and Strava Vertical Slice

## Database cleanup (2026-08-10)

The approved development-stage cleanup removed `athlete_profiles.primary_sport`
and the obsolete baseline, normalized availability/equipment/health,
coach-preference, Strava/OAuth/webhook/sync, and activity-feedback tables from
the current ORM schema. Their dedicated application code and tests were removed
with no replacement tables. Existing Alembic history was intentionally left
unchanged at the request of the cleanup scope.

> **2026-08-03 scope amendment:** The original multi-step onboarding described
> below is retained as historical implementation context, not current product
> behavior. Current onboarding ends immediately after explicit conversational
> goal confirmation. See the final amendment section and
> `docs/current-product-flow.md` for the supported architecture.

## Onboarding refinement (2026-08-10)

- Restricted athlete sex to `MALE` and `FEMALE` in the application and database.
  Migration `0016_restrict_athlete_gender` resets the onboarding data of any
  historical `OTHER_UNSPECIFIED` profile while preserving its account and
  workouts.
- Simplified goal confirmation to Continue/Cancel with direct free-text edits,
  made the pending event-date prompt deterministic (`YYYY-MM-DD` or Not yet),
  and removed obsolete add/restart, date-presence, and health-description
  buttons.
- Focused onboarding tests, Ruff, and mypy pass. The full suite has eight
  pre-existing stale assertions for the removed LLM equipment recommender and
  its `all`/`other` callbacks, plus an outdated Telegram-handler count.

## Objective

Build and validate a runnable, multi-user adaptive endurance coaching vertical
slice. A Telegram user must be able to complete a resumable, button-first
English onboarding flow; confirm any LLM-interpreted free text; persist an
atomic normalized profile; select a baseline source; connect Strava through
OAuth 2.0; import deduplicated activities; and inspect deterministic,
discipline-specific baseline metrics with explicit confidence.

Training-plan generation, adaptive replanning, RAG, dashboards, payments, and
medical diagnosis are explicitly outside this milestone.

## Initial repository state

Observed on 2026-07-28 before implementation:

- Git branch `main` was clean and tracking `origin/main`.
- The repository contained a small Python 3.11-era Telegram echo bot:
  `app/main.py`, `app/handlers.py`, `requirements.txt`, and a short README.
- `tests` contained only `__init__.py`.
- No database, API, migrations, Docker Compose, LangChain/LangGraph workflow,
  Strava integration, baseline engine, or application services existed.
- `.env` already existed locally, contained a `TELEGRAM_BOT_TOKEN` key, and was
  ignored by Git. Its value was not printed or modified.
- Available local tooling: Python 3.13.1, Git 2.47.1, Docker 29.5.3, and Docker
  Compose 5.1.4.

The existing bot behavior was evolved into the production package under
`backend/app`. The root echo modules were removed after the production polling
entry point and its construction tests were in place, preventing ambiguous
`app` imports.

## Architecture decisions

1. Use a modular monolith with two process entry points: FastAPI and Telegram
   long polling. Both construct the same service/repository graph.
2. Keep all persistent state in PostgreSQL through SQLAlchemy 2 async sessions.
   Tests may use isolated PostgreSQL schemas or focused in-memory fakes where a
   database is not the behavior under test.
3. Stage onboarding answers in `onboarding_sessions.answers`. Only explicit
   confirmation advances a step, and final profile materialization occurs in a
   single transaction.
4. Implement deterministic onboarding transitions as ordinary Python. Compile
   one small stateless LangGraph for explicit `Other`/`Write answer` paths.
5. Construct both mock and live chat models behind one LangChain-facing
   factory. The mock still traverses the compiled LangGraph topology.
6. Store only safe pending parse output before user confirmation; confirmed
   values enter onboarding staging only after the `Correct` callback.
7. Protect live LLM use with a rolling-hour database query over `llm_usage`.
8. Persist a SHA-256 digest of a cryptographically random, expiring, one-time
   OAuth state. The raw state appears only in the short-lived browser URL.
9. Encrypt Strava access and refresh tokens with Fernet using
   `APP_ENCRYPTION_KEY`. Never expose provider errors or credentials in logs.
10. Import Strava activity summaries with direct `httpx` calls, upsert on
    `(source, external_id)`, and serialize per-user sync with a database
    uniqueness guard for active jobs.
11. Calculate baseline metrics with deterministic Python heuristics. Store a
    new baseline version on recalculation so prior results remain auditable.
12. Persist webhook events before processing and deduplicate them with a stable
    event key. Resolve the Strava owner through a connection before touching
    user data.
13. Keep Telegram messages and labels centralized in their required modules.
    Service results expose codes/data, not user-facing prose.
14. Expose a vendor-neutral no-op AI workflow observer and centralized graph
    invocation configuration so Langfuse can be added later without becoming a
    runtime dependency now.
15. Bind every mutable onboarding callback to its expected persisted step.
    Multi-select callbacks carry idempotent `add`/`remove` intent, and a durable
    parse-run nonce prevents concurrent free-text results from overwriting the
    value a user is confirming.
16. Commit OAuth state consumption before code exchange or denial handling, use
    token-response scopes as authoritative, and recover leased sync/webhook
    work after a process restart.

## Implementation phases and progress

- [x] Read the complete 2,060-line implementation brief.
- [x] Inspect the repository, Git state, ignored environment file, and local
  tool versions without exposing secrets.
- [x] Create `AGENTS.md`, `.agent/PLANS.md`, and this living ExecPlan.
- [x] Establish `backend/pyproject.toml`, configuration, logging, package
  boundaries, Docker Compose, and `.env.example`.
- [x] Implement SQLAlchemy models, async session construction, repositories,
  and the initial Alembic migration for every required entity.
- [x] Validate the migration against an empty PostgreSQL database.
- [x] Implement the deterministic onboarding state machine, conditional
  transitions, edit/resume/cancel behavior, validation, and transactional
  profile finalization.
- [x] Implement LangChain model construction, deterministic fake model,
  compiled LangGraph parsing workflow, safe outcomes, usage recording, and
  rate limiting.
- [x] Implement encryption, OAuth state handling, Strava OAuth/token refresh,
  activity import, cooldown/concurrency control, disconnect, and webhook
  ingestion.
- [x] Implement deterministic baseline calculation, versioned persistence, and
  recalculation hooks.
- [x] Implement FastAPI health/readiness and Strava routes.
- [x] Implement thin Telegram handlers, centralized messages/keyboards,
  state-aware menus, commands, confirmations, and safe global error handling.
- [x] Add focused unit, use-case, integration, and scenario tests covering the
  required invariants and edge cases without external network calls.
- [x] Replace the placeholder README with complete Windows, Docker, BotFather,
  Strava, LLM, webhook, privacy, and validation instructions.
- [x] Run and repair pytest, Ruff, formatting, mypy, migration, imports, API
  startup, database readiness, `.env` ignore, and accidental-secret checks.
- [x] Record final evidence and credential-dependent limitations below.

## Discoveries and assumptions

- The existing `.env` is user-owned and ignored. It will not be deleted,
  printed, or replaced.
- Python 3.13 is available locally and satisfies the requested Python 3.12+
  target. Dependency ranges will avoid known Python 3.13 incompatibilities.
- Editable installation completed successfully with the current compatible
  releases, including SQLAlchemy 2.0.51, asyncpg 0.31.0, Alembic 1.18.5,
  LangChain Core 1.5.1, LangGraph 1.2.9, and python-telegram-bot 22.8.
- The first local database target is Docker PostgreSQL. Unit tests will not
  require live Telegram, LLM, or Strava services.
- English is the only rendered language, while the live structured-output
  prompt and fake cases accept multilingual input.
- Baseline labels are product heuristics, not physiological or medical claims.
- Current official Strava documentation uses `/oauth/token` for exchange and
  refresh and, from 2026-06-01, recommends `/oauth/revoke` with HTTP Basic
  client authentication over the legacy deauthorize endpoint. Activity pages
  must continue until an empty page because a short page is not a documented
  end condition. Both overall and read-specific rate-limit header pairs will be
  captured.
- A separate Windows PostgreSQL service already occupied host port 5432. Docker
  PostgreSQL is therefore exposed on host port 55432 while retaining container
  port 5432; application, Alembic, example environment, and README defaults are
  aligned.
- The original slice requested the narrower `activity:read` scope. The
  2026-07-28 provider follow-up below intentionally supersedes that decision
  and requests exactly `read` plus `activity:read_all`.

## Validation commands

Planned commands:

```powershell
cd backend
python -m pip install -e ".[dev]"
pytest
ruff check .
ruff format --check .
mypy app

cd ..
docker compose up -d db
cd backend
alembic upgrade head
python -c "from app.api.main import app; print(app.title)"
```

API health/readiness will additionally be exercised through an in-process HTTP
client and, when PostgreSQL is running, a real local server smoke test.

## Failures encountered and fixes applied

- The first Ruff pass found five presentation/logging formatting violations.
  Lines were wrapped and ambiguous en-dashes in numeric validation ranges were
  replaced; the focused files then proceeded to tests.
- `pytest tests/api/test_health.py -q`: 3 passed, covering process liveness,
  database readiness, and safe database failure.
- `pytest tests/bot/test_rendering.py -q`: 6 passed, covering callback length,
  multi-select rendering, lifecycle menus, profile/baseline rendering, and
  token non-disclosure.
- The initial global-error-handler test used a duck-typed update, while the
  production handler intentionally accepts a real Telegram `Update`. The test
  was corrected to construct that boundary type; the handler suite now passes
  4 tests for command delegation, callback acknowledgement/editing, benign
  duplicate-edit replay, and neutral error delivery.
- A focused Ruff pass over the shared API, bot, configuration, and presentation
  code passes after replacing two unnecessary constant `getattr` calls in a
  test helper.
- The first `docker compose up -d db` attempt could not reach the Docker
  Desktop Linux engine because the local daemon was not running. Docker itself
  and Compose were installed. Docker Desktop was started, the image was pulled,
  and the retry succeeded. `pg_isready` now reports that the
  `adaptive_training_coach-db-1` PostgreSQL 17 container accepts connections.
- Host port 5432 resolved to an unrelated local PostgreSQL service. Compose was
  moved to `55432:5432`, the container was recreated healthy, and
  `pg_isready` confirmed that database `adaptive_coach` accepts connections for
  the Compose `coach` role.
- `alembic upgrade head`, `alembic current`, and `alembic check` pass against
  Docker PostgreSQL at revision `0001_initial`. A separately created empty
  validation database upgraded from zero to 18 public tables (17 application
  tables plus `alembic_version`) and was then removed.
- An in-process FastAPI lifespan smoke test against Docker PostgreSQL returned
  200 from both `/health` and `/ready`. Production application construction
  exposes the health and Strava route families and owns startup recovery.
- Production Telegram application construction completed without a network
  call using the ignored local token: nine update handlers, one global error
  handler, and startup/shutdown hooks were registered. The token value was not
  printed.
- Audit hardening added committed one-time OAuth-state burn semantics,
  authoritative token-response scope validation, callback replay guards,
  semantic multi-select callbacks, a per-parse in-flight nonce, startup sync
  recovery, query-string secret redaction, and complete normalized profile
  rendering. It also added canonical webhook replay, recovery of OAuth-completed
  pending initial imports, row-serialized token refresh and baseline version
  allocation, and credential reload between activity pages.
- A final disposable-database command initially used the stale role name
  `adaptive_coach` and failed before creating anything. The command was
  corrected to the Compose role `coach`; migration from an empty database then
  produced 18 public tables and the disposable database was dropped.
- The shell policy rejected a background `Start-Process` Uvicorn smoke command.
  The equivalent real-socket check was rerun with `uvicorn.Server` in a managed
  thread: startup completed, `/ready` returned 200, and shutdown completed.

## Remaining external or manual blockers

## Follow-up: profile-first onboarding order

Requested and implemented on 2026-08-07:

- [x] Reordered new onboarding sessions so birth year, category, weight, and
  height are collected and saved before the conversational training-goal flow.
- [x] Moved lifecycle completion to explicit training-goal confirmation, so an
  athlete cannot complete onboarding without both the mandatory profile and a
  confirmed goal.
- [x] Updated Telegram copy, product-flow documentation, and focused journey
  coverage to reflect the new sequence.
- [x] Ran the focused onboarding suites (13 passed), Ruff, formatting, and
  mypy under Python 3.13. The complete suite exceeded the two-minute command
  timeout without emitting a failure report, so it remains to be run in an
  unrestricted local shell.

The ignored environment supplied a Telegram token. A read-only live Bot API
initialization (`getMe`) succeeded without printing the token or bot identity;
polling was not started and no updates were consumed or messages sent. A real
chat journey remains a manual check.

Strava credentials, a public HTTPS callback, and a live LLM key were not
configured. Consequently these checks remain manual:

- authorize a real athlete, observe the callback and initial import, create the
  webhook subscription, and deliver a real Strava event;
- set `LLM_MODE=live` with an OpenAI-compatible key and confirm a real
  multilingual free-text interpretation;
- run the BotFather bot, send `/start`, and complete the journey in Telegram.

All production paths were implemented and the missing-provider paths were
exercised with deterministic model doubles and `httpx` mock transports. Exact
credential and public-HTTPS setup steps are in the README.

## Final completion evidence

Observed on 2026-07-28:

- Final tree inspection found 113 project files after excluding `.env`, Git
  internals, caches, editable-install metadata, and other ignored runtime
  artifacts. The obsolete root echo bot was removed; the runnable application
  lives under `backend/app`.
- `python -m pip install -e ".[dev]"` rebuilt and installed
  `adaptive-endurance-coach==0.1.0` successfully. Imports of the FastAPI app,
  bot composition, and compiled onboarding-text workflow succeeded under
  Python 3.13.1.
- `pytest -q`: 148 passed in 11.88 seconds. The suite covers unit, API, bot,
  use-case, PostgreSQL repository/migration, and end-to-end application-service
  scenarios without calling Strava or an LLM provider.
- `ruff check .`: passed.
- `ruff format --check .`: 101 files already formatted.
- `mypy app`: no issues in 82 source files under strict configuration.
- Docker Compose reports `postgres:17-alpine` healthy on
  `localhost:55432`; container-side `pg_isready` reports accepting connections.
- `alembic upgrade head`, `alembic current`, and `alembic check` pass at
  `0001_initial (head)` with no pending model operations.
- A separately created empty PostgreSQL database upgraded from zero to 18
  public tables (17 application tables plus `alembic_version`) and was removed
  after validation.
- A FastAPI `TestClient` lifespan check returned `200/ok` from `/health` and
  `200/ready` from `/ready`; connect, callback, and webhook Strava routes were
  mounted. A real local Uvicorn socket separately returned 200 from `/ready`
  and shut down cleanly.
- Production Telegram composition registered nine update handlers, one global
  error handler, and startup/shutdown recovery hooks. The configured token also
  passed a live read-only Bot API authentication check; no polling or messages
  were performed.
- `git check-ignore -v .env` matched `.gitignore`, `git ls-files .env` found no
  tracked file, and an exact comparison found zero local `.env` values anywhere
  else in the project. A high-risk credential-pattern scan found zero matches.
  `git diff --check` passed apart from non-failing Windows line-ending notices.
- An independent final audit found no remaining implementation blocker. Live
  Strava OAuth/webhooks/activity import, a real live-LLM parse, and an
  interactive Telegram chat journey remain correctly unclaimed and require the
  manual credentials/actions listed above.
- Langfuse is neither installed nor required. The vendor-neutral observer
  protocol, no-op observer, and centralized callback configuration are the
  documented future integration boundary.
- Training planning, adaptive replanning, RAG, embeddings/vector storage,
  dashboards, Mini Apps, payments, nutrition, and medical diagnosis were
  intentionally not implemented.

## Follow-up: DeepSeek defaults and complete Strava OAuth UX

Requested and implemented on 2026-07-28:

- [x] Retain `LLM_MODE=mock` and `LLM_MODE=live` while making DeepSeek V4 Flash
  the default live OpenAI-compatible provider.
- [x] Configure LangChain with `https://api.deepseek.com`,
  `deepseek-v4-flash`, environment-supplied `LLM_API_KEY`, and disabled
  thinking for onboarding extraction.
- [x] Keep Pydantic structured output and the existing compiled, stateless
  LangGraph; switch the live adapter to DeepSeek-supported JSON mode.
- [x] Confirm through existing use-case coverage that predefined callbacks do
  not invoke the model.
- [x] Change Strava authorization to request only `read` and
  `activity:read_all`, and validate the callback grant before code exchange as
  well as the authoritative token-response scopes afterward.
- [x] Keep the opaque backend connect ticket, separate expiring single-use
  provider state, direct Strava login, and encrypted token persistence.
- [x] Add an ownership-resolving Telegram notifier after a successful initial
  import and an **Open Telegram** link to the callback success page.
- [x] Update focused unit, API, scenario, and end-to-end OAuth tests.
- [x] Update `.env.example` and README without changing or exposing `.env`.
- [x] Run and record the complete pytest, Ruff, formatting, mypy, runtime,
  migration, and secret-review gates.

DeepSeek's current official documentation lists `deepseek-v4-flash` at the
OpenAI-compatible `https://api.deepseek.com` base URL and documents the
`extra_body={"thinking": {"type": "disabled"}}` toggle. The live adapter uses
LangChain `with_structured_output(..., method="json_mode")`; the graph topology
and deterministic mock adapter are unchanged.

Focused validation after implementation:

- 61 relevant DeepSeek, OAuth, Strava service, API, bot-scenario, and
  application-service tests passed.
- Focused Ruff passed.
- Strict mypy passed for 83 application source files.

Final follow-up validation:

- `pytest -q`: 150 passed in 18.34 seconds.
- `ruff check .`: passed.
- `ruff format --check .`: 102 files already formatted.
- `mypy app`: no issues in 83 source files.
- Docker PostgreSQL remained healthy on `localhost:55432`;
  `pg_isready` accepted connections.
- `alembic upgrade head`, `alembic current`, and `alembic check` passed at
  `0001_initial (head)` with no schema drift.
- FastAPI returned `200/ok` from `/health` and `200/ready` from `/ready`; the
  connect and callback routes remained mounted.
- Production bot construction still registered nine update handlers and one
  error handler. Runtime diagnostics showed `LLM_MODE=mock` with the new
  DeepSeek base URL/model ready for live mode.
- `.env` remained ignored and untracked. Exact local-value comparison and the
  high-risk credential-pattern scan both found zero matches outside `.env`;
  `git diff --check` passed apart from non-failing Windows line-ending notices.
- Live DeepSeek invocation was not claimed because no DeepSeek key was
  configured. Live Strava OAuth/import/notification was not claimed because
  Strava credentials and a public callback were unavailable; the complete flow
  is covered with provider transports, real repositories, encrypted-token
  assertions, and an injected Telegram sender.

## Follow-up: Apple Health export onboarding branch

Requested on 2026-07-28:

- [x] Replace the baseline menu with Apple Health import, manual entry, decide
  later, Back, and feature-flagged Strava; remove the unreal calibration
  option.
- [x] Add persisted privacy, waiting, processing, complete, and failed
  onboarding states without routing deterministic actions through the LLM.
- [x] Add generated-path Telegram document handling and resumable,
  ownership-scoped import jobs with one active import per user.
- [x] Validate ZIP magic and resource ceilings and reject unsafe paths, links,
  encryption, nested archives, conflicting names, and compression bombs.
- [x] Discover `HealthData` by XML root and implement two streaming passes for
  workouts/statistics and heart-rate records with DTD/entity/network
  protections.
- [x] Normalize supported units and sports, preserve source types, match heart
  rate by interval/source, and retain explicit temporal-quality/reliability
  flags.
- [x] Upsert canonical activities with average/maximum heart rate, deduplicate
  cumulative exports, and persist only source-quality provenance and safe job
  metadata/counters. Individual heart-rate observations are no longer stored.
- [x] Recalculate and version the deterministic baseline with source
  `APPLE_HEALTH_EXPORT` in the successful persistence transaction.
- [x] Make Strava disabled by default, hide its bot entry points, and return a
  safe disabled response without requiring provider credentials at startup.
- [x] Add focused parser, ZIP-security, onboarding, handler, persistence,
  idempotency, cleanup, recovery, migration, and regression tests.
- [x] Document configuration, privacy behavior, resource limits, and the
  absence of a live Telegram upload claim.
- [x] Run final pytest, Ruff, formatting, mypy, PostgreSQL migration/runtime,
  bot-construction, and secret/personal-file review gates.

Implementation decision: the standard-library expat-backed ElementTree
streaming parser is wrapped in a prolog scanner that rejects external DTD
identifiers and all entity declarations before bytes reach the parser. This
tolerates ordinary internal Apple-style element declarations while preventing
external retrieval and entity expansion. Both full XML passes run through
`asyncio.to_thread`; no queue, Redis, raw XML persistence, or LangGraph
checkpoint was added.

Final Apple Health follow-up validation:

- `pytest -q`: 178 passed in 35.32 seconds.
- `ruff check .`: passed.
- `ruff format --check .`: 110 files already formatted.
- `mypy app`: no issues in 89 application source files.
- The existing Docker PostgreSQL database upgraded from `0001_initial` to
  `0002_apple_health_import`; `alembic current` and `alembic check` passed.
- A disposable empty PostgreSQL database upgraded from zero to head with 20
  public tables (19 application tables plus `alembic_version`), reported no
  schema drift, and was then removed. The portable migration test also covers
  upgrade and downgrade.
- FastAPI lifespan startup with `STRAVA_ENABLED=false` and no Strava or
  encryption credentials returned `200/ok` from `/health` and `200/ready` from
  `/ready`.
- Bot runtime recovery and construction succeeded with Strava disabled and no
  Strava credentials: ten update handlers (including documents), one error
  handler, and Apple Health recovery were registered.
- `.env` remained ignored and untracked. The tracked-file high-risk key scan
  found no matches; no ZIP or XML fixtures were present in the repository;
  `git diff --check` reported only non-failing Windows line-ending notices.
- No live Telegram upload was claimed. All Apple Health delivery tests used
  locally generated ZIP/XML data and an injected Telegram download boundary.

## Follow-up: Unified training-file import and workout feedback

Requested on 2026-07-29.

### Objective and user-visible outcome

Extend the existing Apple Health vertical slice without replacing its secure
parser or canonical activity/baseline path. During onboarding, one persisted
file-import session must accept an Apple Health ZIP, sequential TCX workouts,
or both, and calculate the initial baseline only when the athlete chooses
**Finish import**. After onboarding, a completed athlete may upload a TCX for
one new workout or an Apple Health ZIP for historical backfill/enrichment.
Single daily TCX imports optionally collect confirmed manual average heart
rate, deterministic RPE, and non-diagnostic discomfort feedback.

Strava remains optional and disabled by default. This follow-up does not add a
queue, Redis, Celery, GPX/FIT support, planning, a dashboard, medical
diagnosis, or a generic importer framework.

### Repository state before this follow-up

- The only worktree item was the user-owned untracked
  `.env copy.example`; it will remain untouched.
- The active implementation already had secure Apple Health ZIP parsing,
  generated temporary paths, SHA-256 file replay protection, canonical
  activities, deterministic baselines, durable onboarding, centralized
  Telegram copy/keyboards, and migration head `0002_apple_health_import`.
- The current Apple flow was deliberately single-file and onboarding-only:
  every successful file recalculated immediately and moved onboarding to a
  terminal Apple-import state. Import jobs required an onboarding session.
- Canonical activities had no TCX source, cross-source provenance link,
  quality-aware metric merge, manual-HR provenance, subjective feedback, or
  durable post-onboarding workout flow.
- The bare `pytest` command was not on `PATH`, and the first
  `python -m pytest -q` attempt failed because dependencies were absent. The
  declared editable development dependencies were reinstalled successfully.
- A subsequent collection attempt exposed an invalid local
  `TELEGRAM_BOT_USERNAME` value from the ignored user-owned `.env`. The file
  was not read, printed, or changed. With a non-secret process-local username
  override, the verified starting suite was `178 passed in 15.31s`.

### Architecture and security decisions

1. Keep one secure document boundary: resolve the Telegram owner and durable
   flow before download, use a generated temporary path, bound the download,
   calculate SHA-256, detect the actual content, parse in a worker thread, and
   delete the temporary file after every outcome. Telegram names and MIME data
   remain hints only.
2. Reuse the Apple Health parser unchanged where possible and add one narrowly
   scoped, entity/DTD/network-safe TCX parser. TCX source keys are stable across
   metric enrichment and dated non-standard sport labels normalize
   deterministically.
3. Generalize the existing durable import job additively with detected format,
   onboarding/daily context, optional onboarding ownership, and the canonical
   activity produced by a single TCX. Exact-file replay remains scoped to
   `user_id + SHA-256`.
4. Add an ownership-scoped activity source-link table. One canonical activity
   can therefore retain Apple Health and TCX identities without double-counting
   the baseline.
5. Centralize conservative cross-source matching thresholds. Automatic merge
   requires one unambiguous owned candidate with compatible sport, close UTC
   start, similar duration, and similar distance when both distances exist.
6. Merge metrics non-destructively using the required precedence: reliable
   sensor data, reliable provider summary, derived data, user-reported data,
   then unavailable. A missing or lower-quality value never erases a better
   canonical value.
7. Store manual heart rate and subjective feedback in one owned
   `activity_feedback` record per activity. Manual HR may be the displayed
   canonical average only while no better measurement exists; it remains in
   feedback history after later sensor enrichment and is excluded from
   sample-based HR coverage.
8. Persist the post-onboarding conversation separately from completed
   onboarding. Its expected state, owned activity, staged manual HR, and staged
   discomfort description survive restarts; callbacks carry intent rather than
   personal identifiers.
9. Onboarding files are bulk imports. Each file returns to the durable waiting
   state and never creates per-workout questionnaires. **Finish import**
   verifies at least one valid session activity, appends a `FILE_IMPORT`
   baseline, presents cumulative discipline counts, then resumes the existing
   onboarding summary.
10. A post-onboarding TCX appends/recalculates the baseline before optional
    feedback. A post-onboarding Apple ZIP recalculates after successful
    backfill/enrichment and does not create per-workout questionnaires.

### Implementation phases and progress

- [x] Read the complete attached request and current ExecPlan.
- [x] Inspect repository state, importer/security boundaries, persistence,
  baseline engine, onboarding transitions, Telegram surfaces, tests, and
  documentation before editing production code.
- [x] Record the verified pre-change test baseline and environment failures.
- [x] Add secure TCX parsing and synthetic parser tests.
- [x] Add schema enums/fields, source links, feedback and durable workout-flow
  persistence, plus migration `0003`.
- [x] Add centralized activity matching and quality-aware enrichment.
- [x] Generalize the import service for onboarding and daily ZIP/TCX behavior.
- [x] Extend onboarding finish behavior and profile finalization for
  `FILE_IMPORT`.
- [x] Add `/add_workout`, direct document routing, resumable feedback, messages,
  keyboards, and optional latest-workout enrichment.
- [x] Add focused use-case, integration, scenario, handler, rendering, config,
  and regression tests using synthetic data only.
- [x] Update `.env.example` and README in English.
- [x] Pass pytest, Ruff, formatting, strict mypy, portable empty/current
  migrations, schema-drift, FastAPI health/readiness, Telegram construction,
  cleanup, and tracked-data/secret checks.
- [ ] Repeat the current and empty migration checks against PostgreSQL when a
  Docker/PostgreSQL runtime is available; the Docker CLI was absent from this
  execution environment.

### Planned validation

Run from `backend`, using module entry points because the user-level scripts
directory is not on `PATH`:

```powershell
$env:TELEGRAM_BOT_USERNAME = "adaptive_coach_bot"
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy app
python -m alembic upgrade head
python -m alembic current
python -m alembic check
```

The final evidence will separately record current-database and disposable
empty-database upgrades, API and Telegram construction, temporary-file
cleanup, and the fact that no live Telegram upload is claimed without a real
bot/file exchange.

### Follow-up discoveries and final decisions

- The ignored local `.env` contains a non-secret but invalid public Telegram
  username value. It was neither printed nor changed. Test collection now uses
  a process-local non-secret username fallback, so the repository test command
  is hermetic without mutating user configuration.
- Startup recovery originally used a 30-minute cutoff and was composed in both
  FastAPI and Telegram. That could either strand a prompt restart or let the
  API process cancel a live bot upload. Unified import recovery now runs only
  in the Telegram delivery worker and immediately fails work owned by the
  prior bot process before accepting updates.
- The generated upload path is recorded on the durable import job before
  document bytes are downloaded. Normal cleanup clears it; startup recovery
  restores onboarding and deletes recorded files left by a terminated process.
  Deletion accepts only the generated prefix/suffix inside the configured
  temporary directory.
- Actual file growth is monitored during the asynchronous Telegram download;
  metadata remains only a hint. Format-specific compressed ZIP and TCX limits
  are still enforced again after content detection.
- Cancellation and restart races are guarded transactionally. A terminal job
  is rechecked under lock before any activity write, and onboarding is restored
  from `FILE_IMPORT_PROCESSING` to a recoverable state.
- Feedback Back callbacks now include their rendered origin state. Replaying a
  stale callback returns the current durable state instead of moving backward
  twice. Leaving **Add workout** also cancels the durable waiting flow.
- Daily file recalculation preserves an existing Strava/manual baseline-source
  preference and lifecycle instead of silently replacing it with
  `FILE_IMPORT`; onboarding completion still persists `FILE_IMPORT` as
  required.
- Reliable Apple Health short-interval samples retain measured-sensor
  provenance. Unreliable provider data remains below reliable measured or
  provider summaries, while confirmed manual HR remains separately auditable.
- Completion and daily-result copy now states when the deterministic baseline
  is partial and disciplines remain `UNKNOWN`. Apple Health daily copy no
  longer tells a completed athlete to finish onboarding.

### Unified training-file follow-up validation evidence

Validated on 2026-07-29:

- `python -m pytest -q`: 288 passed in 15.94 seconds.
- `python -m ruff check .`: passed.
- `python -m ruff format --check .`: 125 files already formatted.
- `python -m mypy app`: no issues in 98 application source files.
- `python -m pytest -q tests/integration/test_persistence_migration.py
  tests/api/test_health.py tests/bot/test_main.py`: 10 passed in 7.64 seconds.
  These cover empty migration upgrade/downgrade, a data-preserving
  `0002`-to-`0003` upgrade, real FastAPI lifespan with `/health` and `/ready`,
  Strava-disabled startup without credentials, Telegram runtime recovery and
  construction, and the registered document handler.
- A disposable empty SQLite database upgraded through
  `0003_unified_training_import (head)` and `python -m alembic check` reported
  `No new upgrade operations detected`; the disposable file was removed after
  validation.
- Real PostgreSQL current/empty upgrades could not be repeated because
  `Get-Command docker` reported that the Docker CLI is unavailable. The
  migration remains covered by both portable empty and populated-`0002`
  integration tests, but this is not represented as live PostgreSQL evidence.
- Temp-file tests cover success, failure, cancellation, actual-size overflow,
  and bot-restart cleanup of a recorded file. The tracked-file scan found zero
  ZIP/TCX/FIT/GPX/export XML artifacts, zero tracked `.env` files, and zero
  files matching the high-risk token/private-key patterns.
- The local `.env` remains ignored. No real Apple Health export, personal TCX,
  credential, live provider call, or live Telegram document was used or
  claimed.

## Follow-up: complete local Docker Compose application

### Objective and decisions

The complete local application should start from the repository root with
`docker compose up --build`. One Python 3.12 backend image is reused for the
one-shot migration, FastAPI, and Telegram long-polling services. Compose
contains exactly four services:

- `db`: PostgreSQL 17 with a persistent development volume and health check.
- `migrate`: waits for PostgreSQL and runs `alembic upgrade head` once.
- `api`: waits for a successful migration and serves FastAPI on port 8000.
- `bot`: waits for a successful migration and API health, then starts exactly
  one polling process without exposing a port.

The Compose environment overrides only `DATABASE_URL` so containers use the
internal `db` hostname. Runtime configuration and local credentials continue
to come from the ignored root `.env`; no secret or environment file is copied
into the backend image. Default feature flags keep Strava disabled and Apple
Health and TCX imports enabled.

### Progress

- [x] Add one minimal `backend/Dockerfile` based on Python 3.12 slim.
- [x] Exclude local environments, tests, caches, databases, and `.env` from the
  backend build context.
- [x] Expand Compose to the exact `db`, `migrate`, `api`, and `bot` topology
  with health/completion-gated dependencies.
- [x] Document the one-command PowerShell startup, health checks, migration
  logs, persistent database behavior, and destructive `down -v` warning.
- [ ] Validate Compose parsing, image build, migrations, API readiness, and
  single bot runtime when a Docker CLI and valid local Telegram configuration
  are available.
- [x] Re-run pytest, Ruff, formatting, and strict mypy for the completed
  infrastructure follow-up.

### Validation evidence

Validated on 2026-07-29:

- A PyYAML structural check confirmed exactly `db`, `migrate`, `api`, and
  `bot`; one shared backend image/build definition; the internal PostgreSQL
  URL; `.env` inheritance; and the required database-health and
  migration-success dependency conditions. The bot is explicitly limited to
  one replica and starts only after API readiness.
- Dockerfile invariants confirmed Python 3.12 slim, `/app`, unbuffered output,
  only explicit backend source/package copies, and no `.env` copy.
- `python -m pytest -q`: 288 passed in 19.29 seconds.
- `python -m ruff check .`: passed.
- `python -m ruff format --check .`: 125 files already formatted.
- `python -m mypy app`: no issues in 98 application source files.
- `git diff --check`: passed. The root `.env` remains ignored, and a
  filename-only tracked-file scan found zero files matching high-risk token or
  private-key patterns.
- `docker compose config`, image build, live PostgreSQL migration, API
  readiness, and bot polling could not be executed because `Get-Command
  docker` reported that the Docker CLI is unavailable. No live-container,
  live-Telegram, or live-provider result is claimed.

## Follow-up: local Adminer database inspection

Requested on 2026-07-29.

### Objective and decisions

Add the official Adminer image as one local-development Compose service without
changing application code, database configuration, credentials, schema, or the
PostgreSQL volume. Adminer binds only to `127.0.0.1:8080`, waits for the
existing `db` health check, and reaches PostgreSQL through the Compose hostname
`db`.

### Progress

- [x] Inspect the existing Compose file and preserve the current `db`,
  `migrate`, `api`, and `bot` configuration.
- [x] Add exactly one `adminer` service with a loopback-only port binding and
  healthy-database dependency.
- [x] Document startup, browser access, local login fields, direct-access
  warnings, and volume-preserving versus destructive shutdown.
- [x] Validate the rendered Compose configuration and live five-service stack.

### Validation evidence

Validated on 2026-07-29:

- `docker compose config` rendered successfully. The Adminer port is published
  with host IP `127.0.0.1`, and its `db` dependency requires
  `service_healthy`.
- `docker compose config --services` listed `db`, `adminer`, `migrate`, `api`,
  and `bot`: the original four services plus exactly one new service.
- `docker compose up -d` pulled `adminer:latest`, preserved the existing
  startup gates, and started the stack successfully.
- `docker compose ps -a` showed healthy `db`, exited-zero `migrate`, healthy
  running `api`, running `bot`, and running `adminer` bound only to
  `127.0.0.1:8080`.
- `docker compose logs adminer` showed the PHP server listening on container
  port 8080 without startup errors.
- `http://localhost:8080`, `/health`, and `/ready` each returned HTTP 200.
- A PHP PDO query executed inside the Adminer container connected to
  `pgsql:host=db;dbname=adaptive_coach` as `coach` and returned `1`. This
  verifies the Adminer runtime can reach PostgreSQL with the documented local
  connection values.
- The first two inline PHP connection probes failed because PowerShell/Docker
  argument quoting removed the PHP string delimiters. Rewriting the probe with
  PowerShell double quotes and PHP single-quoted strings fixed the command; no
  repository or runtime configuration changed.
- No controllable browser was available, so a browser-form login was not
  performed or claimed.

## Follow-up: discipline-specific workout persistence

Requested on 2026-07-30.

### Objective and implemented outcome

Replace the wide canonical `activities` row with a universal `workouts` identity
and one one-to-one detail model for each supported discipline, while preserving
the existing Apple Health, TCX, Strava, baseline, summary, and workout-feedback
flows. Unknown source activities must remain usable as `OTHER`; no legacy
metric may disappear silently. This follow-up does not add a planner, redesign
onboarding, add time-series/lap models, or invent a new manual-workout UI.

The implemented universal row contains only `id`, `athlete_id`, `discipline`,
timezone-aware `started_at`, positive `duration_seconds`, `source`, optional
`external_id`, `title`, `notes`, and audit timestamps. Exactly one main detail
record carries discipline metrics:

- `running_workout_details` for `RUNNING`;
- `cycling_workout_details` for `CYCLING`;
- `hiking_workout_details` for `HIKING`;
- `swimming_workout_details` for `SWIMMING`, with a required
  `pool_swimming_details` extension for pool swims;
- `strength_workout_details` for `STRENGTH`;
- `other_workout_details` for `OTHER`.

### Important design and migration decisions

1. The canonical discipline enum is `RUNNING`, `CYCLING`, `HIKING`,
   `SWIMMING`, `STRENGTH`, and `OTHER`. Sources are `MANUAL`, `STRAVA`,
   `APPLE_HEALTH`, `TCX`, `FIT`, and `OTHER_IMPORT`. `FIT` is data-model
   compatibility only; no FIT parser was added.
2. Strict Pydantic schemas validate source/external-ID semantics,
   discipline-detail agreement, timezone-aware starts, positive duration,
   non-negative optional metrics, pool/open-water consistency, and the exact
   strength exercise/set JSON shape. Imported strength without structured sets
   uses an empty list.
3. A central workout serializer returns generic fields plus one typed detail.
   A flat internal metric projection lets baseline, import/enrichment, summary,
   and feedback code consume the new structure without moving
   discipline-specific branching into Telegram handlers.
4. Positive distance and moving duration determine canonical running/hiking
   pace, swimming pace per 100 metres, and cycling speed. A provider conflict
   does not override the derived value; the provider value and warning remain
   in source metadata.
5. Import normalization is conservative. Plain walking, an unknown sport,
   swimming without defensible pool/open-water evidence, and a pool-labelled
   swim without a positive pool length become `OTHER`. Raw sport/sub-sport,
   recognized metrics, unsupported provider metrics, and the fallback reason
   remain available through source metadata and `OtherWorkoutDetails`.
6. Repository-level manual creation uses the same validated schema. Manual
   `OTHER` uses `OtherWorkoutDetails` and has no external ID. No new Telegram
   manual-entry conversation or public API route was added.
7. `activity_feedback`, `activity_source_links`, and the existing import-job
   table names remain for compatibility, but their canonical foreign key is now
   `workout_id`. Feedback adds nullable `mobility_done`: `true` for explicit
   Yes, `false` for explicit No, and `NULL` for skipped/not asked/unknown.
8. The durable feedback sequence is now `RPE` → `MOBILITY` → `DISCOMFORT`.
   Back from `DISCOMFORT` returns to `MOBILITY`; Back from `MOBILITY` returns to
   `RPE`. Expected-state callback checks still replay stale callbacks without
   duplicate mutation.
9. Migration `0004_discipline_workout_models` maps legacy `RUN`, `RIDE`,
   `WALK_HIKE`, and explicitly classifiable `SWIM` records to their canonical
   details. Ambiguous swimming and pool evidence without a pool length become
   `OTHER`.
10. The migration preserves IDs, timestamps, owner/source identities, support
    records, and foreign keys. Every removed legacy value is copied into
    `activity_source_links.source_metadata_jsonb`; `OTHER` also carries the
    snapshot in `metrics_jsonb`. A legacy zero-second duration becomes one
    second while the original zero remains in provenance. Later source
    reimports retain that immutable envelope under `migration_provenance` while
    keeping the latest source snapshot at the top level.
11. Downgrade reconstructs the `0003` row from provenance and refuses before
    mutation when data is not representable in `0003`, including new source
    values, mobility feedback, an active `MOBILITY` flow, or missing/ambiguous
    provenance. Persisted snapshots also guard universal workout, main-detail,
    pool-detail, and every source-link field, including deletion state.

### Progress

- [x] Inspect the existing models, migrations, parsers, repositories, baseline
  service, Telegram summaries, feedback flow, and current tests.
- [x] Add canonical enums, universal workout persistence, discipline detail
  models, strict boundary schemas, central serialization, and owned manual
  creation.
- [x] Update Apple Health, TCX, and existing Strava normalization/import paths,
  conservative matching/enrichment, baseline projection, summaries, and
  feedback references.
- [x] Add nullable mobility feedback and its persisted Telegram step without
  changing unrelated onboarding.
- [x] Add the data-preserving `0004` migration and guarded downgrade.
- [x] Add focused schema, creation, persistence, migration, parser/import,
  serializer, baseline, bot, and mobility coverage.
- [x] Update the README and current-product-flow documentation.
- [x] Record the complete repository quality gates and required Docker/PostgreSQL
  runtime validation for this follow-up.

### Validation evidence recorded so far

Validated on 2026-07-30:

- `python -m pytest -q tests/unit/test_workout_schemas.py
  tests/use_cases/test_workout_creation.py
  tests/integration/test_workout_persistence.py`: 27 passed in 4.10 seconds.
  Focused Ruff, Ruff-format, mypy, and diff checks for those files also passed.
- `python -m pytest -q tests/bot tests/use_cases/test_workout_feedback.py`: 64
  passed in 20.28 seconds. Scoped Ruff, Ruff-format, and mypy checks for the bot
  and feedback implementation passed.
- `python -m pytest -q tests/integration/test_persistence_migration.py`: 3 passed
  in 16.46 seconds. This covers empty upgrade/base downgrade, populated
  `0002`-to-head, and populated all-discipline `0003`-to-head-to-`0003`
  preservation.
- After the final source-provenance guards, the combined migration,
  workout-persistence, and matching regression set passed: 16 passed in 43.06
  seconds. It includes reimport preservation of the immutable legacy envelope
  and downgrade refusal after source-link deletion-state changes.
- `python -m ruff check
  alembic/versions/0004_discipline_workout_models.py
  tests/integration/test_persistence_migration.py`: passed.
- A disposable SQLite database completed `python -m alembic upgrade head`, and
  `python -m alembic check` reported `No new upgrade operations detected`.
- The focused open-water manual creation test initially exposed an async lazy
  load (`MissingGreenlet`) when serializing an absent pool-detail relationship.
  Initializing that relationship explicitly to `None` fixed the defect; the
  27-test focused suite above then passed.
- Final `python -m pytest -q`: 336 passed in 69.14 seconds.
- `python -m ruff check .`: passed.
- `python -m ruff format --check .`: passed after formatting.
- `python -m mypy app`: passed.
- `git diff --check`: passed.
- `docker compose config | Out-Null`: exited zero.
- Final `docker compose build`: passed with the finalized application and
  migration source.
- `docker compose up -d`: exited zero after rerunning the one-shot migration and
  satisfying the API health dependency.
- `docker compose ps` showed healthy `db` and `api` services plus running `bot`
  and `adminer`; the earlier `docker compose ps -a` showed `migrate` exited
  zero.
- The live development PostgreSQL database contained zero workouts, so the
  finalized migration was safely exercised in both directions:
  `0004_discipline_workout_models` -> `0003_unified_training_import` ->
  `0004_discipline_workout_models`. Both commands exited zero, counts stayed
  zero, and `alembic check` reported `No new upgrade operations detected`.
- Live schema inspection confirmed that `activities` is absent and `workouts`
  contains exactly the universal columns documented above. All preflight and
  post-migration row counts remained zero. It also confirmed that the pool
  detail key references both `workouts.id` and
  `swimming_workout_details.workout_id`. The populated backfill path is
  therefore evidenced by the migration integration test, not by this empty
  development database.
- The migration, API, and bot services were recreated. Migration exited zero,
  PostgreSQL and the API reported healthy, the bot remained running, and
  `GET /ready` returned HTTP 200 with `{"status":"ready"}`. Reviewed service
  logs contained no migration loop or startup failure.
- Live Telegram interaction, Strava/provider calls, and live LLM calls were not
  performed or claimed.

## Follow-up: remove persisted heart-rate observations

Requested on 2026-07-30:

- [x] Inventory model, relationship, repository, migration, documentation, and
  test references to `heart_rate_observations`.
- [x] Remove the SQLAlchemy model and the Apple Health observation upsert while
  retaining direct average/maximum heart rate writes to discipline details.
- [x] Add `0005_remove_hr_observations`, which drops the table without backfill
  and recreates only its empty `0004` shape on downgrade.
- [x] Keep transient Apple matching, matched-record counters, and the
  independently used heart-rate source/quality precedence enums.
- [x] Update affected migration, model-metadata, Apple import, README, and
  current-flow expectations.
- [x] Run the focused/full test, static-analysis, Alembic, Docker build, schema,
  API, database, and bot-startup gates.

Design decision: individual Apple Health records remain transient parser input.
The canonical `average_heart_rate` and `max_heart_rate` values are stored only
on the workout's matching discipline detail. `ActivitySourceLink` continues to
hold non-sample source/reliability provenance needed by matching, feedback, and
baseline precedence. No replacement observation or time-series table is added.

### Validation evidence

Validated on 2026-07-30:

- Focused Apple Health, file-import, repository-metadata, migration, and workout
  persistence coverage passed: `35 passed in 63.53s`.
- The complete backend suite passed: `336 passed in 114.31s`.
- `ruff check .`, `ruff format --check .`, and `mypy app` all passed; mypy
  checked 99 source files.
- `docker compose build` completed successfully. Before applying the migration,
  the live PostgreSQL database was at `0004_discipline_workout_models` and the
  obsolete table contained zero rows.
- The migration upgraded PostgreSQL to `0005_remove_hr_observations`;
  `heart_rate_observations` was absent and the workout count was unchanged.
  `alembic check` reported no new upgrade operations.
- A live downgrade to `0004_discipline_workout_models` recreated the empty
  eleven-column legacy table, and a second upgrade removed it again.
- The recreated Compose stack reported healthy API and database services,
  migration exit code 0, `GET /ready` returned `{"status":"ready"}`, and the bot
  logged a successful application start. Reviewed service logs showed no
  migration loop or startup failure.
- Live Telegram interaction, Apple Health/TCX/Strava provider calls, and live
  LLM calls were not performed or claimed.

## Follow-up: exact-only workout import identity

Requested on 2026-07-30. This follow-up supersedes the earlier cross-source
matching, metric-precedence, and persisted heart-rate confidence decisions in
this plan.

### Current decisions

1. An imported workout is an exact duplicate only for the same
   `athlete_id + source + external_id`.
2. A stable provider ID is used when available. Otherwise a deterministic
   `fingerprint:` external ID is generated from normalized source, discipline,
   UTC start, duration, and distance.
3. Apple Health, TCX, and Strava records are never merged across sources.
   Similar timestamps, durations, distances, disciplines, and heart-rate
   values do not influence identity.
4. Exact same-source reimports refresh that workout and discipline detail.
   Another external ID from the same source creates another workout.
5. Average and maximum heart rate remain direct discipline-detail values.
   Source links no longer persist heart-rate source, quality, reliability, or
   sample count, and no confidence/rank/precedence selector remains.
6. `HeartRateSource` is removed. `HeartRateTemporalQuality` remains only as
   transient Apple Health parser classification.
7. `TrainingActivityRepository` remains the public persistence entry point.
   Provider adapters, contracts, normalization, detail mapping, and source-link
   persistence are split into focused modules.

### Progress

- [x] Inspect the dirty worktree and inventory matching, confidence,
  source-link, import, baseline, feedback, serialization, summary, migration,
  and test dependencies.
- [x] Remove approximate candidate matching, thresholds, ambiguous outcomes,
  cross-source merge, quality ranks, heart-rate precedence, and persisted
  confidence fields.
- [x] Implement exact provider identity and normalized-value fallback
  fingerprints for sources without a stable external ID.
- [x] Split the former oversized activity repository by responsibility while
  preserving `TrainingActivityRepository`.
- [x] Add `0006_exact_workout_identity` and reversible migration coverage.
- [x] Replace matching/confidence tests with exact replay, source isolation,
  fingerprint, direct heart-rate, baseline, and feedback coverage.
- [x] Update README and current-product-flow documentation.
- [x] Record final static-analysis, migration, Docker build, PostgreSQL schema,
  API readiness, and bot-startup evidence.

### Validation evidence recorded so far

- `pytest -q`: 331 passed in 51.31 seconds, including exact Apple, TCX, Strava,
  no-provider-ID fingerprint, cross-source separation, direct heart-rate,
  baseline, feedback, serialization, and migration coverage.
- `ruff check .`, `ruff format --check .`, `mypy app`, and `git diff --check`
  passed. Mypy checked 108 source files and Ruff reported 138 formatted files.
- `docker compose build` completed with the final application and migration
  source.
- Before the live migration, PostgreSQL was at
  `0005_remove_hr_observations` with zero workouts and zero source links.
- `0006_exact_workout_identity` removed all four source-link heart-rate
  confidence columns. `alembic check` reported
  `No new upgrade operations detected`.
- The live `0006 -> 0005 -> 0006` round trip restored all four legacy columns
  on downgrade and removed them again on upgrade; workout counts remained
  zero.
- `docker compose up -d` recreated the migration, API, and bot services.
  Migration exited zero, PostgreSQL and the API were healthy, `/ready` returned
  `{"status":"ready"}`, and the bot logged `Application started`.
- Live Telegram interaction, Apple Health/TCX/Strava provider calls, and live
  LLM calls have not been performed or claimed.

## Follow-up: Telegram welcome, consent, and setup introduction

Requested on 2026-08-01. This follow-up changes only the first visible
Telegram experience and the transition into the existing athlete-profile
questions.

### Current decisions

1. Telegram's native **Start** button is the visible entry point. Telegram
   still sends `/start` internally, and all existing command handlers remain
   technical fallbacks, but rendered product copy does not advertise slash
   commands.
2. Welcome, product-help, privacy/safety, and consent navigation uses the
   existing callback facade with short `nav:v1:*` identifiers. Callback
   responses prefer editing the current message and use the existing safe
   fallback sender when editing is unavailable.
3. Consent is persisted only through the explicit
   **I understand — continue** action. Confirmation is row-serialized and
   idempotent.
4. No database migration or new onboarding enum is needed. The private
   `_setup_introduction_pending` value in staged answers makes the
   post-consent introduction resumable while the next domain step remains
   `PRIMARY_SPORT`.
5. The private marker blocks every primary-sport answer path and is removed
   only by **Let's build my profile**. The existing athlete-profile question,
   answer, persistence, and LLM paths are otherwise unchanged.
6. Cancellation before consent stores no consent. Cancellation after consent
   preserves the confirmed consent and all existing cancellation/deletion
   semantics.

### Progress

- [x] Inspect the clean worktree, active plan, handlers, callbacks, rendering,
  state machine, persistence, cancellation, resume behavior, and existing
  tests before implementation.
- [x] Add centralized exact welcome, help, privacy, consent, and setup copy and
  exact inline-button labels.
- [x] Add minimal navigation, idempotent consent, setup-introduction gating,
  Back, Cancel, edit-in-place, and compatibility callback behavior.
- [x] Remove slash-command instructions from rendered product messages while
  retaining internal command handlers.
- [x] Update focused handler, rendering, onboarding-service, and scenario
  tests without adding a new test framework.
- [x] Update README, current product flow, and this active ExecPlan.
- [x] Run the full Python/static-analysis validation and requested Docker
  build/runtime validation; record exact evidence below, including the one
  out-of-scope pre-existing Windows cleanup failure.

### Validation evidence recorded so far

- Focused bot, rendering, scenario, and onboarding-service tests passed:
  `60 passed`.
- The narrower journey/use-case rerun after closing the direct service bypass
  passed: `35 passed`.
- Final focused flow coverage passed: `60 passed`. Five new test functions were
  added, and existing entry/resume journey assertions were updated in four
  existing test files.
- Full `pytest -q`: `335 passed, 1 failed`. The only failure is the unchanged
  `test_actual_download_size_is_bounded_and_temp_metadata_is_cleared`. It also
  fails in isolation because cancellation of an existing `asyncio.to_thread`
  write cannot stop the Windows worker thread; the immediate unchanged import
  cleanup sees a transient open handle. The exact generated file can be
  deleted moments later. Import logic was not changed because this task
  explicitly excludes it.
- The same unchanged full suite was then run from the mounted backend inside
  the built Linux application image: `336 passed in 43.48s`. This proves the
  complete suite passes in the deployed runtime and isolates the remaining
  local result to Windows thread cancellation/file locking.
- `ruff check .`, `ruff format --check .`, `mypy app`, and
  `git diff --check` passed. Ruff checked 138 formatted files and mypy checked
  108 source files.
- `docker compose config` and `docker compose build` passed. The requested
  unredacted Compose config rendering interpolated the existing Telegram token
  into command output; the token value is not repeated here and should be
  rotated.
- `docker compose up -d` recreated the stack. Migration exited 0, PostgreSQL
  and API reported healthy, `/ready` returned `{"status":"ready"}`, and the bot
  was running with zero restarts.
- No live Telegram chat journey, Strava provider flow, webhook, or live-LLM
  interaction was performed or claimed.

## Follow-up: first conversational onboarding goal

Requested on 2026-08-02. This follow-up changes only the first interaction
after **Let's build my profile** and then hands off to the unchanged existing
primary-sport question and remaining onboarding sequence.

### Current decisions

1. The goal intake is a resumable subflow over the existing `PRIMARY_SPORT`
   step. Private phase keys and `goal_draft` live in
   `onboarding_sessions.answers`; no generic conversation framework or new
   onboarding enum is introduced.
2. Each relevant free-text message is retained exactly before the focused,
   stateless compiled LangGraph invokes the existing LangChain structured-output
   model integration. The graph has no checkpointer and no database access.
3. The narrow model contract contains `main_goal`, `event_date`,
   `target_outcome`, `secondary_priority`, `missing_fields`,
   `ambiguous_fields`, and `message_status`. Application code revalidates,
   preserves valid earlier fields, rejects vague main goals, ignores
   `secondary_priority` for completeness, and never invents dates.
4. Clarification buttons are deterministic and never invoke an LLM. **Not yet**
   resolves a missing date to a valid null date. Free-text clarification and
   add/change messages invoke the same focused graph with the accumulated draft.
5. OFF_TOPIC output never mutates the goal draft or canonical goal. Start again
   clears only the temporary goal draft and retained goal messages; consent and
   unrelated staged profile data remain.
6. Explicit **No, that\u2019s right** confirmation transactionally writes the
   canonical `training_goals` fields and removes the temporary draft before
   rendering the unchanged primary-sport question.
7. Migration `0007_conversational_training_goal` is required because the
   existing table had only legacy enum goal type, event, and priority columns.
   It adds the four conversational concepts, original description, and explicit
   confirmed status while retaining all legacy columns for the untouched rest
   of onboarding.

### Progress

- [x] Inspect the dirty worktree, current post-consent transition, onboarding
  JSON staging, legacy structured graph, canonical goal table, finalization,
  callbacks, rendering, and tests before implementation.
- [x] Add the focused goal schema and compiled graph through the existing model
  adapter, safe observer boundary, rate limit, and LLM usage persistence.
- [x] Add raw-message staging, accumulated draft merging, prioritized
  clarification, off-topic handling, add/change, restart, cancellation, and
  explicit confirmation behavior.
- [x] Add canonical conversational goal columns and ownership-scoped repository
  persistence without changing the existing enum-driven remainder.
- [x] Add focused structured-result, persistence, merge, callback, rendering,
  and compatibility tests; update current-flow documentation.
- [x] Run and record final full test, Ruff, formatting, mypy, diff, migration,
  and Docker/runtime validation.

### Validation evidence recorded so far

- Focused goal graph, persistence, onboarding-service, and bot scenario suites:
  `44 passed`.
- Focused migration and conversational-goal suites after adding `0007`:
  `12 passed`.
- Full host suite: `344 passed, 1 failed`. The only failure is the unchanged
  Windows training-import cleanup race
  `test_actual_download_size_is_bounded_and_temp_metadata_is_cleared`; this
  exact out-of-scope failure was already documented before this follow-up.
- Full Linux suite in the built Compose application image: `345 passed in
  50.63s`.
- `ruff check .`, `ruff format --check .`, `mypy app`, and `git diff --check`
  passed. Ruff checked 145 formatted files and mypy checked 113 source files.
- The first PostgreSQL attempt exposed that the original revision identifier
  exceeded the existing 32-character Alembic version column. Transactional DDL
  rolled it back cleanly at `0006`; the identifier was shortened to
  `0007_conversational_goal`, and all migration tests were rerun successfully.
- Live Compose PostgreSQL upgraded from `0006_exact_workout_identity` to
  `0007_conversational_goal`; `alembic current` reports head and `alembic check`
  reports no new upgrade operations.
- The Compose API image built successfully. No live Telegram chat, Strava,
  webhook, or live-LLM request was performed or claimed.

## 2026-08-03 retained conversational-goal checkpoint amendment

### Decision

The supported onboarding boundary is now Start, welcome/help/privacy, consent,
setup introduction, conversational goal intake, clarification, confirmation,
and the terminal `GOAL_CONFIRMED` checkpoint. The old profile continuation is
removed rather than hidden: its enum states, state machine, generic onboarding
text graph, callbacks, messages, keyboards, materialization/finalization
writers, onboarding import branches, and tests no longer exist.

Existing completed athletes retain their normalized records and post-profile
features. Legacy profile tables and enum goal columns remain read-compatible
because `ProfileService.get` still serves them. Daily Apple Health/TCX imports,
workout feedback, baselines, and Strava remain available independently from
onboarding. The conversational goal repository method is the sole canonical
goal writer.

Migration `0008_remove_legacy_onboarding` normalizes legacy sessions into the
four retained checkpoints, preserves cancellation, maps old completed session
status to active without downgrading user lifecycle status, removes unused
onboarding control/provenance columns, and makes legacy goal classification
columns nullable. Canonical goals, workouts, import outcomes, historical
profiles, baselines, feedback, and Strava data are preserved.

### Progress

- [x] Inventory legacy runtime paths, persistence fields, tests, and docs.
- [x] Reduce onboarding states, application service, repository, bot routing,
  messages, and keyboards to the retained flow.
- [x] Remove the generic onboarding text graph and retain one focused compiled
  goal extraction operation.
- [x] Remove post-goal profile finalization and onboarding-only import/feedback
  coupling while preserving existing-athlete features.
- [x] Add `0008` with portable upgrade/downgrade and session normalization.
- [x] Replace legacy tests with focused goal, terminal checkpoint, migration,
  historical profile, and daily-import coverage.
- [x] Rewrite current product-flow documentation and amend the README.
- [x] Complete the final full host, Ruff, formatting, mypy, PostgreSQL, Alembic,
  and Compose validation pass.

### Evidence before final validation

- Test collection before cleanup: 345 tests.
- Test collection after cleanup: 252 tests.
- Focused retained-flow, migration, historical profile, daily import, and
  feedback selection: 76 passed.
- Portable empty-database upgrade, downgrade to `0007`, and re-upgrade to
  `0008` completed successfully.
- Final host suite: `252 passed in 77.39s`.
- `ruff check .`, `ruff format --check .`, `mypy app`, and
  `git diff --check` passed; mypy checked 105 source files and Ruff confirmed
  133 files formatted.
- Live Compose PostgreSQL upgraded transactionally from `0007` to
  `0008_remove_legacy_onboarding`; `alembic current` reports head and
  `alembic check` reports no pending schema operations.
- `docker compose up -d --build` rebuilt the application image, the migration
  container exited 0, PostgreSQL and FastAPI are healthy, the bot is running,
  and live local `/health` and `/ready` returned `ok` and `ready`.
- No live Telegram chat, live LLM call, Strava authorization, or webhook was
  performed or claimed.

## 2026-08-03 conversational-goal LLM patch amendment

### Decision

Runtime inspection found that provider selection is controlled by `LLM_MODE`.
The first inspection found an unconfigured local environment resolving to the
documented `mock` default. The runtime was subsequently configured for `live`
with `deepseek-v4-flash`; bot startup now logs only that safe mode and model
name, never credentials or goal text.

Goal extraction now distinguishes `CREATE_GOAL` from
`UPDATE_EXISTING_GOAL`. The persisted draft and newest user message enter the
focused graph separately. The structured model schema represents a field patch,
not a replacement goal. The onboarding service deterministically applies
non-null patch fields, preserves prior values for null fields, and leaves the
draft unchanged for `OFF_TOPIC`.

### Progress

- [x] Trace Compose settings and the Telegram handler, onboarding service,
  goal graph, provider factory, live adapter, and `ainvoke` boundary.
- [x] Add safe startup provider logging.
- [x] Replace replacement-style model output with a validated goal field patch.
- [x] Add explicit create/update operations and deterministic patch merging.
- [x] Add focused provider-selection, current-draft, date/secondary-priority,
  explicit-correction, and off-topic preservation coverage.
- [x] Complete final full validation and record the opt-in live evaluation
  result.
- [x] Reproduce the Telegram malformed-output fallback against the live model,
  inspect only a synthetic response, and correct its JSON-shape instructions.
- [x] Reproduce a repeated clarification for a short typo-containing answer and
  teach the update prompt to interpret fragments against the draft's current
  missing or ambiguous field.
- [x] Remove an over-strict rule that treated explicitly stated qualitative
  outcomes such as “in a good time” or “in a decent time” as inherently
  ambiguous even though target outcomes need not be numeric.

### Validation evidence

- Focused provider, graph, onboarding, and retained Telegram journey tests:
  `14 passed`.
- Final host suite after the live-output and qualitative-outcome corrections:
  `257 passed in 47.12s`.
- `ruff check .`, `ruff format --check .`, `mypy app`, and
  `git diff --check` passed; Ruff confirmed 134 files formatted and mypy checked
  105 source files. Ruff could not update one local cache file because of a
  Windows permission restriction, but analysis completed successfully.
- `docker compose config --quiet` and `docker compose up -d --build` passed.
  Migration exited 0, PostgreSQL and API are healthy, `/health` returned `ok`,
  `/ready` returned `ready`, and the bot is running. Its safe startup diagnostic
  is `goal_llm_mode=live model=deepseek-v4-flash`.
- The first real no-Telegram `CREATE_GOAL` evaluation proved that DeepSeek
  understood the synthetic Ironman goal but nested semantic fields under a
  `patch` wrapper, causing Pydantic to reject it as malformed. The prompt now
  requires the seven schema keys at the top level and prohibits wrapper keys.
- The deployed-image real-model rerun returned an extracted flat patch:
  `main_goal=complete an Ironman 70.3`, `event_date=2027-07-11`,
  `secondary_priority=maintain muscle`, and `NEEDS_CLARIFICATION` with
  `target_outcome` ambiguous because “a good time” is not specific.
- A real `UPDATE_EXISTING_GOAL` evaluation returned only the date and secondary
  priority patch while leaving `main_goal` and `target_outcome` null, proving
  incremental live behavior through the compiled graph and live `ainvoke`.
- A deployed-image live evaluation of the exact synthetic fragment
  “wihtout stopping” against a draft missing target outcome and event date
  returned `target_outcome=Complete without stopping`, preserved all other
  fields with null patch values, and left only `event_date` missing. The bot
  therefore advances to the date clarification rather than repeating the
  target-outcome question.
- A final deployed-image live evaluation of the complete typo-containing
  Ironman message returned `main_goal=Complete an Ironman 70.3`,
  `event_date=2027-07-11`, `target_outcome=Finish in a good time`,
  `secondary_priority=Maintain current muscle`, no missing or ambiguous fields,
  and `COMPLETE`. A separate live update accepted “finish in a decent time” as
  a valid qualitative target outcome.

## 2026-08-03 mandatory athlete-profile amendment

### Decision

The terminal `GOAL_CONFIRMED` product boundary is superseded. Explicit goal
confirmation now immediately starts a mandatory, deterministic four-step
profile phase: birth year, competition category / biological sex, weight, and
height. The phase does not invoke LangGraph or an LLM.

Birth year accepts four digits from 1940 through 2008. Category is selected by
an inline Male, Female, or Other / Unspecified callback. Weight accepts a
finite decimal from 40.0 through 200.0 kg, and height accepts an integer from
120 through 230 cm. Invalid input retains the current checkpoint and renders a
centralized English error prompt.

The final height submission atomically calls the ownership-scoped
`ProfileRepository` writer, marks the onboarding session `COMPLETED`, and moves
the user lifecycle to `ONBOARDING_COMPLETED`. Migration
`0009_mandatory_profile` adds the state/lifecycle constraints plus canonical
`birth_year` and `gender` profile columns while preserving historical rows. It
also advances existing in-progress `GOAL_CONFIRMED` sessions to birth-year
intake without reopening profiles that already have a completed lifecycle.

### Progress

- [x] Add the four mandatory onboarding states and completed lifecycle/status.
- [x] Add deterministic parsing, range validation, state transitions, and
  gender callbacks to `OnboardingService`.
- [x] Add centralized messages and keyboards and retain thin Telegram handlers.
- [x] Add an ownership-scoped mandatory athlete-profile upsert and compatible
  profile rendering.
- [x] Add portable migration `0009_mandatory_profile` with downgrade support.
- [x] Replace the terminal-goal scenario with a complete profile journey and
  add focused invalid-input/no-LLM coverage.
- [x] Update the README and current product-flow documentation.
- [x] Run the focused and full host validation suites.

### Validation evidence

- Focused onboarding, rendering, scenario, and migration suites: `28 passed`.
- Final full host suite: `258 passed in 54.58s`.
- `mypy app`: no issues in 105 source files.
- `ruff check .` passed and `ruff format --check .` confirmed 135 formatted
  files.
- Empty-database upgrade reached `0009_mandatory_profile`; `alembic check`
  reported no new upgrade operations.
- Docker Desktop was not running in this workspace, so live PostgreSQL,
  Telegram, Strava, webhook, and live-LLM validation is not claimed.

## 2026-08-03 future event-date inference fix

### Decision and progress

- [x] Capture `date.today().isoformat()` once in
  `OnboardingService._extract_goal` and pass it through the typed extractor
  boundary into the stateless LangGraph invocation state.
- [x] Make the extraction node use only that supplied anchor date in its prompt.
- [x] Require month-and-day inputs without a year to resolve to the next
  strictly future calendar occurrence.
- [x] Roll a model-returned nonfuture month/day without an athlete-supplied year
  to its next future occurrence; reject explicit nonfuture years to deterministic
  date clarification without changing the supplied year.
- [x] Add focused prompt, future inference, explicit-past-date, ambiguous-date,
  and service-context propagation tests.

### Validation evidence

- Focused graph, onboarding, and scenario suites: `17 passed`.
- Final full host suite: `261 passed in 53.62s`.
- `ruff check .`, `ruff format --check .`, `mypy app`, and `git diff --check`
  passed; Ruff confirmed 135 formatted files and mypy checked 105 source files.

## 2026-08-03 canonical training-goal cleanup

### Decision and progress

The active conversational representation supersedes the original categorical
goal representation. `goal_type`, `event_name`, and `goal_priority` are
therefore removed from `training_goals`; `event_date` remains because it is
already shared by both representations. `status` and `original_description`
remain intentional audit/lifecycle fields rather than duplicate semantics.

- [x] Remove legacy goal fields and their unused Python enums from the runtime
  model, profile schema, presentation, and compatibility service.
- [x] Add migration `0010_remove_legacy_goal_fields`.
- [x] Backfill legacy-only rows into readable canonical `main_goal`,
  `target_outcome`, and `original_description` values before dropping columns.
- [x] Make all three required canonical text fields non-null.
- [x] Preserve downgrade compatibility with neutral legacy classifications.
- [x] Add migration assertions for removed columns, non-null canonical fields,
  and legacy-row data preservation.

### Validation evidence

- Focused migration, profile, conversational-goal, and rendering suites:
  `29 passed`.
- Final full host suite: `261 passed in 65.43s`.
- `ruff check .`, `ruff format --check .`, `mypy app`, and `git diff --check`
  passed; Ruff confirmed 135 formatted files and mypy checked 105 source files.
- A fresh portable database upgraded to `0010_remove_legacy_goal_fields`;
  `alembic check` reported no new operations. Direct schema inspection showed
  only `user_id`, canonical goal fields, lifecycle/audit fields, and common
  identifiers/timestamps in `training_goals`.

## 2026-08-03 post-onboarding goal modification tool

### Decision and progress

- [x] Add a Pydantic-validated LangChain `update_athlete_goal` tool whose
  injected runtime delegates persistence to `OnboardingService` for the active
  `user_id`.
- [x] Extend the compiled graph with a reusable agent -> native `ToolNode` ->
  agent loop using `tools_condition`, while binding only the goal update tool.
- [x] Add an ownership-scoped canonical repository update that preserves
  `event_date`, `secondary_priority`, and `original_description` and advances
  `updated_at`.
- [x] Route completed-athlete chat through the tool-capable workflow and render
  the escaped natural confirmation through centralized Telegram messages.
- [x] Retain safe per-user LLM usage reservations/rate limiting without storing
  the raw request or updated goal data.
- [x] Add graph regression and service persistence coverage for the Ironman
  70.3 goal-change request.

### Validation evidence

- Focused graph and service suites: `17 passed`.
- Final full host suite: `263 passed in 95.58s`.
- `ruff check .`, `ruff format --check .`, `mypy app`, and `git diff --check`
  passed; Ruff confirmed 136 formatted files and mypy checked 105 source files.
- The graph/tool regression used a mocked service updater; the service use-case
  suite additionally verified the owned database update and field preservation.
- Live Telegram and live-provider LLM behavior were not exercised and are not
  claimed.

## 2026-08-03 generic onboarding-data modification refactor

### Decision and progress

- [x] Replace the goal-only tool schema with sparse `UpdateOnboardingSchema`
  fields for goal, age, weight, and fitness-level changes, each with explicit
  model-facing descriptions and room for future athlete data.
- [x] Rename the bound tool to `update_onboarding_data`, accept dynamic keyword
  arguments, and remove null values before crossing the service boundary.
- [x] Route allowlisted fields in `OnboardingService` to independent athlete
  profile and training goal sub-payloads, invoking only affected repositories.
- [x] Replace fixed assignments with ownership-filtered dynamic SQLAlchemy
  updates that preserve every omitted column.
- [x] Add nullable `athlete_profiles.fitness_level` through reversible migration
  `0011_add_athlete_fitness_level` and expose it in persisted profile output.
- [x] Expand regression coverage for schema metadata, null filtering, mixed
  multi-table persistence, field preservation, and migration head state.

### Validation evidence

- Focused graph, service, and migration suites: `22 passed`.
- Final full host suite: `264 passed in 72.48s`.
- `ruff check .`, `ruff format --check .`, `mypy app`, and `git diff --check`
  passed; Ruff confirmed 136 formatted files and mypy checked 105 source files.
- Live PostgreSQL upgraded from `0010_remove_legacy_goal_fields` to
  `0011_add_athlete_fitness_level`; `alembic check` reported no new upgrade
  operations.
- Live Telegram and live-provider LLM behavior were not exercised and are not
  claimed.

## 2026-08-03 live multi-turn modification orchestrator evaluation

### Decision and progress

- [x] Remove the mocked `test_dynamic_orchestrator_incomplete_goal_flow` unit
  regression and its message-capture scaffolding.
- [x] Add an opt-in `live` pytest suite that requires a real OpenAI-compatible
  provider credential and refuses any PostgreSQL database whose name does not
  contain `test`; it never falls back to the deterministic model or SQLite.
- [x] Exercise the compiled graph across retained multi-turn history for a
  cancelled goal request followed by a weight update and a later Ironman goal.
- [x] Require vague goals such as “something fast” to clarify without a tool
  call, then accept the concrete follow-up “a 5k race”.
- [x] Support sparse `event_date` modifications and verify one tool call can
  atomically route age and weight to `athlete_profiles` plus goal and future
  event date to `training_goals` while preserving omitted fields.
- [x] Strengthen the live system prompt with concrete-goal validity, newest-turn
  authority, abandoned-intent handling, one-call multi-field behavior, and
  strictly future yearless-date rules.
- [x] Limit the DeepSeek-specific `thinking` request extension to DeepSeek URLs
  so the same live adapter remains valid with the OpenAI API.

### Validation evidence

- A freshly migrated disposable PostgreSQL database named
  `adaptive_coach_live_test` reached revision `0011_add_athlete_fitness_level`.
- The actual configured live provider passed all three orchestrator cases:
  `3 passed in 19.59s`. No mock model or mock repository was used.
- Default full suite: `264 passed, 3 live tests skipped in 103.00s`; the skips
  are intentional unless `RUN_LIVE_AGENT_TESTS=1` is supplied.
- `ruff check .` and `ruff format --check .` passed with 137 formatted files;
  `mypy app` reported no issues in 105 source files.

## 2026-08-03 unused fitness-level removal

### Decision and progress

`fitness_level` is not part of the current product and must not appear in the
tool schema, service allowlist, normalized profile contract, Telegram output,
or final PostgreSQL schema. Revision `0011` remains in migration history for
databases that already applied it; new revision `0012_remove_fitness_level`
drops the transient column and restores it only during downgrade.

- [x] Remove `fitness_level` from the SQLAlchemy model, persisted profile
  schema/service, Telegram rendering, live tool schema/prompt, deterministic
  model, onboarding service routing, and repository allowlist.
- [x] Remove fitness-level expectations from unit and use-case tests while
  retaining mixed athlete-profile and training-goal update coverage.
- [x] Add the reversible `0012_remove_fitness_level` migration and assert the
  column is absent at migration head.
- [x] Inspect both local PostgreSQL databases before migration and confirm zero
  non-null fitness-level values.
- [x] Upgrade the Adminer-visible development database and isolated live-test
  database to `0012_remove_fitness_level`; direct schema inspection reports
  zero matching columns in both.

### Surprise

The first draft revision identifier exceeded PostgreSQL's 32-character
`alembic_version.version_num` limit. PostgreSQL rolled back the transactional
DDL and version update together. The final identifier was shortened to
`0012_remove_fitness_level`, after which both upgrades completed cleanly.

### Validation evidence

- Focused graph, onboarding, and portable migration suites: `22 passed`.
- Final full suite: `264 passed, 3 opt-in live tests skipped in 97.37s`.
- `ruff check .`, `ruff format --check .`, `mypy app`, and `git diff --check`
  passed; Ruff confirmed 137 formatted files and mypy checked 105 source files.
- PostgreSQL `alembic check` reported no new upgrade operations after the live
  development upgrade to `0012_remove_fitness_level`.

## 2026-08-03 global Telegram agent workspace

### Decision

The earlier “deterministic callbacks do not invoke an LLM” and “LangGraph has
no checkpointer” boundaries are superseded for the Telegram conversation
surface. Commands, text, and callback payloads now enter one persistent global
LangGraph workspace as `HumanMessage` values. Telegram handlers do not inspect
onboarding steps, callback namespaces, or numeric formats.

The global graph uses native `AsyncPostgresSaver` persistence with a stable
`telegram:<telegram_user_id>` thread ID. Application callables and authenticated
user context are supplied through `ToolRuntime.context`, so they are not
serialized. The existing focused goal graph remains a nested stateless workflow
for its narrow structured-extraction job.

### Progress

- [x] Add a universal `handle_agent_input` boundary and route every Telegram
  command, text message, and callback through it as a `HumanMessage`.
- [x] Add a reusable agent -> `ToolNode` -> agent graph with
  `dispatch_telegram_input` and `update_onboarding_data` tools.
- [x] Persist global message history with `AsyncPostgresSaver`; use an in-memory
  saver only for explicitly non-PostgreSQL test runtimes.
- [x] Keep injected dispatch/update callables outside checkpoint state and keep
  all personal-data writes ownership-scoped by the resolved internal user ID.
- [x] Return checkpoint-safe button metadata from tools and render it generically
  at the Telegram delivery boundary.
- [x] Extend sparse corrections to birth year, category, weight, and height;
  corrections made before profile materialization update owned onboarding
  staging without forcing a local Telegram transition.
- [x] Replace stale/invalid callback delivery with the athlete's current durable
  presentation instead of the former “button is no longer valid” response.
- [x] Delete the persistent graph thread after successful account deletion.
- [x] Add the official PostgreSQL checkpoint and psycopg binary/pool dependencies
  and make the test-image dependency mode explicit.

### Validation evidence

- Focused handler, runtime, workspace, graph, onboarding, and journey suites:
  `32 passed`; focused correction/workspace/handler suite: `11 passed`.
- `mypy app` reported no issues in 107 source files.
- A real PostgreSQL smoke test created the native checkpoint tables, resumed the
  same thread after closing and recreating the workspace, processed a callback,
  deleted the thread, and verified zero remaining checkpoints for that thread.
- The composed bot runtime started and closed successfully against the migrated
  isolated PostgreSQL test database with the persistent workspace enabled.
- Final repository validation: `266 passed, 3 opt-in live tests skipped in
  90.60s`; `ruff check .`, `ruff format --check .`, `mypy app`, and
  `git diff --check` passed. Ruff confirmed 140 formatted files and mypy
  checked 107 source files.

### Runtime correction after deployment

The first Telegram verification still returned the former expired-button text
because the running bot container was built from the pre-workspace image. After
rebuilding, a live-provider replay exposed a second issue: DeepSeek requested
both `update_onboarding_data` and `dispatch_telegram_input` for the same explicit
birth-year correction. Parallel tool commands attempted to write the same
single-value presentation channels and LangGraph raised
`InvalidConcurrentGraphUpdate`.

- [x] Require exactly one tool call in the global system prompt.
- [x] Normalize provider output before `ToolNode`: an explicit onboarding
  update is authoritative over generic dispatch, and only one call executes.
- [x] Add regression coverage reproducing the provider's parallel correction
  and dispatch calls.
- [x] Rebuild and recreate the production bot container without changing the
  database or API containers.
- [x] Replay `sorr my year birth is 2003` against the configured live provider;
  only the update tool executed with `birth_year=2003`, followed by a natural
  confirmation and the current gender prompt.
- [x] Final validation after the runtime fix: `267 passed, 3 opt-in live tests
  skipped in 99.58s`; Ruff lint/format and `mypy app` passed.

The first completed-onboarding goal edit revealed a separate contextual routing
failure. With the athlete's full durable history, the global model sent
`change my goal to a marathon` to generic dispatch instead of the sparse update
tool. The nested modification workflow then returned a transient provider error,
which surfaced as the legacy parse-failure message. The global tool prompt now
makes the latest turn authoritative, excludes explicit field modifications from
generic dispatch, and includes concrete goal and weight routing examples. A
read-only replay against the affected persistent history selected only
`update_onboarding_data(main_goal="marathon")`. Focused graph tests (`11 passed`),
Ruff, and mypy passed before the bot image was rebuilt and restarted.

## 2026-08-03 global-agent latency audit

The compiled global graph was already process-scoped and initialized from the
Telegram application's `post_init`, but steady-state `invoke` still entered the
startup lock. More importantly, `AsyncPostgresSaver.from_conn_string()` owned a
single persistent connection, serializing concurrent checkpoint work, and the
default durability wrote checkpoints after intermediate graph nodes.

- [x] Preserve exactly-once compilation and add a lock-free steady-state startup
  check, retaining the lock only for first initialization.
- [x] Replace the single checkpointer connection with one process-owned
  `AsyncConnectionPool` (`min_size=1`, `max_size=10`) configured for autocommit,
  disabled prepared statements, and dictionary rows.
- [x] Add a pre-LLM deterministic router for known commands, strict callback
  payloads, gender values, and contextual numeric birth-year/age/weight/height
  answers.
- [x] Route fast-track tool results directly to graph exit instead of returning
  to the agent node for a second model call.
- [x] Use `durability="exit"` so each complete Telegram turn writes one final
  checkpoint instead of persisting every internal node transition.
- [x] Add regressions that raise immediately if the model is reached by numeric
  or callback fast paths and assert the workspace compiler runs exactly once.

### Performance evidence

Against the real local PostgreSQL instance, six strict-callback turns with the
new router but default per-node durability measured a warm median of `78.62 ms`.
The same graph and pool with exit-only durability measured a warm median of
`16.77 ms` (`13.58-19.42 ms` for the five warm samples), with zero LLM calls.
These figures measure graph routing plus PostgreSQL checkpointing and an
in-process no-op dispatcher; Telegram network latency and business SQL are not
included.

Final repository validation passed with `270 passed, 3 opt-in live tests
skipped`; Ruff confirmed 140 formatted files and `mypy app` checked 107 source
files without errors. After rebuilding and restarting the bot, the production
image measured a `25.93 ms` warm median and `32.57 ms` warm maximum across five
strict-callback turns. A repeated startup call retained the same compiled graph
object, and pool statistics showed the persistent checkpoint connection
available for reuse.

### Numeric clarification correction

The initial numeric fast path treated all context-matched values as ordinary
intake dispatches. For a completed athlete, `change my height` produced a
clarification in the global history, but dispatching the follow-up `170` entered
the nested stateless modification workflow with no prior question and lost the
field association. The checkpoint was intact; context was dropped at the
workflow boundary.

- [x] Distinguish exact mandatory birth-year, weight, and height intake prompts
  from post-onboarding clarification prompts.
- [x] Preserve intake dispatch for mandatory onboarding transitions.
- [x] Convert validated clarification answers directly into sparse update calls
  such as `update_onboarding_data(height_cm=170)`.
- [x] Return a centralized deterministic confirmation without echoing personal
  values or making another model request.
- [x] Add a two-turn regression that permits one clarification model call and
  fails if the numeric follow-up reaches the model or generic dispatcher.
- [x] Replay the affected persisted history read-only and verify the router now
  selects only the typed height update call.
- [x] Final validation: `271 passed, 3 opt-in live tests skipped`; Ruff and mypy
  passed. The rebuilt production bot started successfully, and the deployed
  image reproduced the typed `height_cm=170` route from the saved history.

## 2026-08-03 bounded provider context

Until the planner supplies structured athlete and training context, the global
provider receives at most the latest three safe conversation messages. Full
graph history remains checkpointed for now, but it is no longer sent wholesale
to DeepSeek. Historical tool requests/results are filtered from ordinary model
turns. When the agent is responding to a just-completed tool, the current human
request, assistant tool call, and matching tool result are retained as one valid
three-message unit so provider message-order requirements are preserved.

- [x] Add a reusable three-message provider context builder.
- [x] Preserve active tool-call/result adjacency and remove orphaned historical
  tool messages.
- [x] Use the bounded window only at the model boundary; deterministic routing
  and durable checkpoint state remain unchanged.
- [x] Add regression coverage for long plain history and active tool exchanges.
- [x] Final validation: `273 passed, 3 opt-in live tests skipped`; Ruff and mypy
  passed. The deployed image reports a three-message provider window while the
  saved two-turn height case still routes to `height_cm=170`.

## 2026-08-04 prompt-template modularization

### Progress and validation note

- [x] Extract the static onboarding prompts into versioned reusable templates
  while preserving the exact provider message content and existing deterministic
  validation. The static contract, future-date policy, and explicit-change tool
  policy now live in `app.workflows.prompts.onboarding`; the goal contract is
  versioned at `1` and documents that wording changes require a version bump and
  regression-test update.
- The first focused validation attempt could not start through `pytest.exe`
  because the Windows application-control policy blocked that executable, and
  `ruff` was absent from the default Python 3.11 environment. The repository's
  Python 3.13 launcher provides both tools; subsequent validation uses
  `py -3.13 -m pytest` and `py -3.13 -m ruff`. The Python 3.13 environment also
  needed the already declared `langgraph-checkpoint-postgres` and
  `psycopg[binary,pool]` dependencies installed before it could collect the
  Telegram workspace tests.
- [x] Add exact prompt-composition regressions plus graph/use-case coverage for
  the JSON contract, `COMPLETE`, `NEEDS_CLARIFICATION`, `OFF_TOPIC`, and future,
  ambiguous, and explicitly past dates.
- [x] Final validation: `279 passed, 3 opt-in live tests skipped`; Ruff reports
  141 formatted files and `mypy app` reports no issues in 109 source files.

## 2026-08-04 active-onboarding dispatcher routing correction

The global Telegram agent previously considered an initial goal sentence during
an active onboarding flow to be an onboarding-data update. For a newly created
account no training-goal record exists yet, so it invoked the update tool and
raised `OwnedRecordNotFoundError`. The correct owner of free-text onboarding is
the compiled onboarding dispatcher, which selects the focused goal-extraction
workflow and its structured LLM contract.

- [x] Add an explicit `onboarding_active` runtime context derived from the
  persisted account lifecycle.
- [x] Route every active-onboarding event through the dispatcher before the
  global tool-calling LLM; preserve the raw Telegram text byte-for-byte.
- [x] Keep the completed-profile path unchanged so explicit `change my ...`
  requests still enter the global agent and call the focused onboarding-update
  workflow.
- [x] Add unit and end-to-end journey regressions covering account deletion,
  recreated-account initial goal submission, and a completed-profile height
  change.
- [x] Final validation: `281 passed, 3 opt-in live tests skipped`; `ruff check
  .`, `ruff format --check .`, and `mypy app` passed.
- [x] Rebuild and restart the Docker bot with the correction. Docker Desktop
  could not read the new OneDrive placeholder files directly, so the identical
  backend source was staged in a temporary local build context; the rebuilt bot
  started successfully and reports `goal_llm_mode=live model=deepseek-v4-flash`.

## 2026-08-07 profile-first raw onboarding context and conversational edits

The supported onboarding order is now basic profile, confirmed goal, weekly
availability, goal-based equipment recommendation, equipment context, and
training limitations. Completion occurs only after all three context answers
are saved.

- [x] Add nullable raw `TEXT` columns to `athlete_profiles` for availability,
  equipment recommendation, equipment, and health limitations in Alembic
  revision `0013_add_athlete_profile_context`; extend the persisted onboarding
  step checks without changing the historical normalized context tables.
- [x] Add durable availability, recommendation, equipment, equipment-details,
  and limitations steps plus deterministic `ALL_RECOMMENDED` and
  `NONE_REPORTED` callback markers.
- [x] Compile stateless LangGraph workflows for raw-text accept/retry validation
  and a short goal-based equipment recommendation. Original athlete context is
  saved literally; recommendation failures retain availability and retry safely.
- [x] Extend conversational sparse updates for goal/outcome/date, basic profile,
  availability, equipment, and limitations. A material goal change invalidates
  equipment context, regenerates the recommendation, and reopens equipment
  review without changing unmentioned fields.
- [x] Keep raw health/context out of service errors, LLM-usage records,
  observer metadata, and global-agent checkpoints: active onboarding bypasses
  the workspace; successful post-onboarding update exchanges are removed before
  checkpoint exit; no post-write provider turn is made.
- [x] Add regression coverage for full and resumed onboarding, markers and free
  text, literal persistence, no normalized-table writes, recommendation retries,
  stale callbacks, chat updates, goal-triggered review, checkpoint privacy, and
  recommendation safety limits.
- [x] Final validation: `py -3.13 -m pytest -q` reports `306 passed, 3 skipped`;
  `py -3.13 -m ruff check .`, `py -3.13 -m ruff format --check .`, and
  `py -3.13 -m mypy app` pass. `docker compose up -d db` and
  `py -3.13 -m alembic upgrade head` applied revision `0013_add_athlete_profile_context`
  to the local PostgreSQL database; all four columns were verified as `text`.

## 2026-08-07 permissive raw-context intake

The product decision is that availability, equipment context, and limitations
are athlete-owned free text. The system must not reject a non-empty answer for
lack of detail: a sparse answer is still useful, and only limits later
personalisation.

- [x] Make the compiled raw-context intake graph accept every non-empty message
  deterministically, without asking an LLM to judge its adequacy.
- [x] Preserve literal persistence and retain the existing empty-message retry.
- [x] Update focused graph and onboarding regressions for vague availability.

## 2026-08-07 structured equipment recommendation table

- [x] Change the goal-based material recommendation to structured coach-selected
  equipment rows with `Essential`, `Recommended`, or `Optional` importance.
- [x] Validate one to five short, unique items and reject plan or medical content.
- [x] Render and persist a stable two-column monospaced Telegram table; retain
  the existing retry-after-provider-failure behavior without a new migration.

## 2026-08-07 DeepSeek JSON-mode compatibility

- [x] State the valid-JSON requirement explicitly in the equipment prompt so
  DeepSeek accepts its `json_object` response format.
- [x] Remove the product limit of five equipment rows; only reject a rendered
  table that would exceed Telegram's message-size capacity.
- [x] Recover valid provider JSON when DeepSeek leaves LangChain's parsed value
  empty; schema validation remains the workflow boundary and raw output is not
  logged or persisted.
- [x] Remove the per-item name-length limit after confirming it rejected a valid
  DeepSeek material item; the rendered Telegram-table limit remains enforced.
- [x] Align the workflow-result recommendation length with the Telegram-safe
  table limit, replacing the historical 700-character text-list limit.

## 2026-08-07 equipment timing column

- [x] Add a required `when_needed` field to every recommended equipment item.
- [x] Require one named training stage plus a concise reason and render it as
  the third `When needed` Telegram-table column.

## 2026-08-07 development onboarding shortcuts

- [x] Add the development-only `DEV_TELEGRAM_USER_IDS` allowlist and parse its
  comma-separated environment value as Telegram user IDs.
- [x] Register `/dev_step` and `/dev_reset` only when the runtime environment
  is `development`; production and test applications do not expose them.
- [x] Route these commands around the global-agent workspace so they reliably
  prepare a requested state even for a completed account. Unauthorized callers
  receive the ordinary not-found response.
- [x] Implement isolated synthetic states for `availability`, `equipment`,
  `limitations`, and `completed`, scoped to the requesting user and without
  changing activities or external integrations. `/dev_reset` returns only that
  user to consent without deleting profile data.
- [x] Add router, facade, and service regression coverage for command
  registration, bypassing the global agent, every seeded step, reset, and user
  isolation. `219` bot/unit, `2` scenario, `77` API/use-case, `13`
  repository/workout-persistence, and `5` migration integration tests pass;
  Ruff, format, and mypy pass. The rebuilt Docker bot was exercised with the
  configured development account: `/dev_step equipment` rendered the material
  review and `/dev_reset` rendered the consent prompt.

### Follow-up: faster import and goal/equipment checks

- [x] Add `/dev_import_history` as an explicit shortcut to the final optional
  training-history import screen.
- [x] Add `/dev_reset_goal_equipment` to remove only the requesting development
  athlete's goal and equipment/access selections, reset their durable settings
  UI state, and return them to goal intake. It preserves the athlete profile,
  workouts, import jobs, and immutable baselines.
- [x] Enforce the existing `DEV_TELEGRAM_USER_IDS` allowlist for every
  development shortcut.

## 2026-08-07 availability examples

- [x] Extend the free-text availability prompt with one neutral example that
  includes time, days, running, pool access, and cycling.
- [x] Do not classify the goal or interpret the answer: any non-empty response
  remains accepted and is saved literally as `availability_text`.

## 2026-08-08 deterministic equipment knowledge

- [x] Add goal-scoped deterministic equipment reference tables, seeded starter
  data, stage windows, substitutions, and status persistence in revision
  `0014_equipment_knowledge`.
- [x] Replace the onboarding recommendation call with the database-backed
  recommendation renderer and interactive resource-status selection.
- [x] Add a structured free-text equipment interpretation branch to the
  compiled context workflow; raw answers remain durable if it fails.
- [ ] Expand deterministic recommendation regression coverage for each seeded
  event and stage window.

## 2026-08-08 deterministic post-onboarding profile settings

- [x] Add the user-owned `profile_settings_sessions` checkpoint in revision
  `0015_profile_settings_session`; it is independent of onboarding lifecycle
  completion and records only the active mini-flow and transient answers.
- [x] Replace the completed equipment-only entry point with `Change profile`
  and stable `ps:v1:` callbacks for goal, availability, equipment, health, and
  personal details.
- [x] Persist goal, availability, health, and personal-detail edits through
  focused deterministic paths; literal free text is accepted only after an
  athlete chooses its corresponding setting and no settings callback invokes
  an LLM.
- [x] Route completed-athlete free text to the selected settings mini-flow or
  back to `Change profile`; do not call the global onboarding-update path.
- [x] Preserve the completed lifecycle while equipment editing preloads and
  replaces goal-revision-scoped availability statuses.

## 2026-08-11 equipment catalog redesign — Release A

- [x] Add revision `0017_equipment_catalog` with deterministic catalog IDs,
  five-discipline reference data, JSON substitutions, global athlete access,
  current-revision `AVAILABLE` backfill, and stale-session normalization.
- [x] Switch the ORM, repository, onboarding, profile settings, `/profile`, and
  Telegram checklist to `equipment_catalog` and `athlete_equipment`.
- [x] Replace event/stage/revision matching with bounded deterministic goal-to-
  discipline aliases and substitution-aware, non-blocking gap summaries.
- [x] Remove equipment recommendation and interpretation from the LangGraph/LLM
  contracts, raw update tools, mocks, and the equipment-details intake state.
- [x] Add focused resolver, substitution, catalog-integrity, replacement,
  isolation, migration-backfill, session-normalization, resume, and stale-
  callback regressions.
- [x] Keep the obsolete `0014` tables and raw profile columns physically present
  for Release A rollback compatibility while removing their active ORM/runtime
  use.
- [x] Validate Release A with `201 passed, 3 skipped`, Ruff, format, mypy,
  populated PostgreSQL `0016 -> 0017`, a fresh migration chain, rebuilt healthy
  API/bot containers, and the local health endpoint. The three skipped tests
  require explicit live-agent execution.
- [x] Perform Release B backup and mixed-version verification, then add the
  separately authorized destructive cleanup migration. This is intentionally
  not part of Release A.

## 2026-08-11 equipment cleanup — Release B

- [x] Verify current explicit-available source counts and unmatched codes, then
  create and validate the pre-cleanup PostgreSQL custom-format backup outside
  the repository.
- [x] Add guarded revision `0018_remove_obsolete_equipment`: rerun and verify the
  final backfill, abort on unknown source codes, normalize obsolete details
  checkpoints, tighten step checks, and remove all obsolete tables and columns.
- [x] Validate both a restored populated `0017 -> 0018` database and a fresh
  migration chain through `0018`; both retain the 26-row catalog and no obsolete
  equipment storage.
- [x] Validate the final state with `202 passed, 3 skipped`, Ruff, format, mypy,
  Alembic head `0018`, rebuilt healthy API/bot containers, and `/ready`.

## 2026-08-11 Telegram equipment and profile UI

- [x] Keep equipment substitutions and recommendation gaps structured through
  the application boundary, then render grouped escaped HTML `<pre>` tables for
  onboarding, profile settings, gap summaries, and `/profile`.
- [x] Bound table cells with ellipses and enforce Telegram's 4,096-character
  message limit; truncate only oversized current-value presentation with an
  explicit marker.
- [x] Add lifecycle reply keyboards for absent, onboarding/cancelled, completed,
  and deleted accounts, with exact deterministic routes for `Start`, `Resume`,
  `Profile`, `Change profile`, and `Delete`.
- [x] Populate typed current values for every editable field and reconstruct
  those values when profile-settings sessions resume. Equipment displays its
  selected state as the current value.
- [x] Add focused renderer and full-journey regressions for table structure,
  escaping, substitutions, message length, lifecycle menus, deterministic
  button routing, deletion reset, and current-value presentation.
- [x] Validate with `207 passed, 3 skipped`, Ruff, format, mypy, Alembic head
  `0018`, rebuilt healthy API/bot containers, and `/ready`. The skipped tests
  require explicit live-agent credentials.
- [x] Fix the discovered profile-edit navigation collision: text edit prompts
  now emit `ps:v1:done` instead of onboarding's `ob:v1:cancel`, and every
  `Back / Done` action renders an explicit closed-state confirmation.

## 2026-08-11 complete training-goal profile controls

- [x] Add the canonical goal's main goal, outcome, event date, secondary
  priority, original description, and status to the owned `/profile` view.
- [x] Extend the deterministic Goal editor through secondary priority and
  original description, including saved-value presentation and an explicit
  `None` action for the optional secondary priority.
- [x] Keep the single `CONFIRMED` status and technical ID, ownership, and audit
  fields system-managed rather than exposing unsafe edits.
- [x] Add migration `0019_expand_goal_settings` for the two durable settings
  states and validate PostgreSQL `0018 -> 0019 -> 0018 -> 0019`.
- [x] Validate with `209 passed, 3 skipped`, Ruff, format, mypy, migration head
  `0019_expand_goal_settings`, rebuilt API/bot containers, and `/ready`. The
  skipped tests remain the credential-gated live-agent checks.
- [x] Replace the undiscoverable sequential Goal flow with an explicit submenu
  for main goal, target outcome, event date, secondary priority, and original
  description. Each action now edits only the selected field and returns through
  deterministic `ps:v1:goal:*` callbacks.
- [x] Add and round-trip migration `0020_add_goal_menu` for the durable submenu
  state.
- [x] Validate the submenu release with `210 passed, 3 skipped`, Ruff, format,
  mypy, migration head `0020_add_goal_menu`, rebuilt API/bot containers, and
  `/ready`.
- [x] Correct the goal-setting boundary: keep `original_description` visible as
  immutable onboarding provenance, remove its UI/repository update path, and add
  migration `0021_remove_goal_description` to normalize any active description
  editor back to the Goal menu.
- [x] Validate the correction with `210 passed, 3 skipped`, static checks,
  migration round-trip through head `0021_remove_goal_description`, and rebuilt
  healthy runtime containers.
- [x] Remove `original_description` from the `/profile` presentation boundary;
  retain it only as internal immutable onboarding provenance.

## 2026-08-11 Telegram private-user allowlist

- [x] Add `TELEGRAM_ALLOWED_USER_IDS` parsing and enforce it before commands,
  text, callbacks, and document uploads reach application services. An empty
  allowlist in the production application denies all Telegram users.
- [x] Document the setting and validate the Telegram handler/application tests.

## 2026-08-12 dynamic goal, context, and capability catalog

The static discipline/equipment resolver is replaced by reusable planning
knowledge. Equipment no longer determines the goal or training context. The
confirmed primary and supporting templates determine target contexts, and each
context owns explicit preferred/substitute executions with capability
requirements.

- [x] Add irreversible revision `0022_dynamic_training_catalog` with seeded
  UUIDv5 goal templates, training contexts, capabilities, execution options,
  and relationships; deterministically classify safe historical matches,
  merge/backfill old athlete equipment, preserve ambiguous access, normalize
  active sessions, and remove the two old equipment tables.
- [x] Replace the old ORM, repositories, schemas, regex resolver, and equipment
  recommendation service with `TrainingCatalogRepository`,
  `AthleteCapabilityRepository`, and `CapabilityAssessmentService`.
- [x] Extend goal structured output with independent primary/supporting
  template decisions and provide the complete compact active catalog to the
  compiled classification graph.
- [x] Add two compiled structured expansion stages: grouped goal-to-context
  mapping followed, only for new contexts, by grouped execution/capability
  definition.
- [x] Validate proposal limits, references, reusable English catalog text,
  supporting roles, required capabilities, and preferred executions before any
  catalog write. Generate IDs in the application and serialize publication
  through a PostgreSQL advisory transaction lock.
- [x] Publish templates, contexts, capabilities, relationships, and athlete
  goal foreign keys atomically. Preserve confirmed drafts and clear only the
  in-flight marker after provider, rate-limit, or validation failures so
  Continue can retry safely.
- [x] Make Equipment & access a goal-scoped deterministic review of
  `AVAILABLE`, `UNAVAILABLE`, and implicit `UNKNOWN` capabilities. Compute
  preferred/substitute feasibility without blocking onboarding and expose the
  typed assessment boundary for the future planner.
- [x] Require focused LLM classification plus user confirmation when editing a
  primary or supporting goal. Keep outcome/date changes deterministic, clear a
  supporting goal with `None` without an LLM, and reopen Equipment & access
  only when a template foreign key changes.
- [x] Update Telegram review/assessment/profile presentation and stale UUID
  handling while keeping callbacks model-free and bounded to 4,096 characters.
- [x] Replace the obsolete test suite with focused catalog, migration,
  expansion retry/atomicity, assessment, isolation, callback, profile-edit, and
  known/dynamic journey coverage.
- [x] Update `docs/current-product-flow.md` and equipment knowledge
  documentation. Live LLM validation remains credential-gated and is not
  claimed by this change.
- [x] Take a pre-upgrade PostgreSQL custom-format backup, upgrade the populated
  local database from `0021` to `0022`, and verify 23 goals, 16 contexts, 29
  capabilities, 29 execution options, and absence of the two old equipment
  tables. `alembic current` reports head and `alembic check` reports no new
  operations. A fresh disposable database also upgraded through the complete
  chain to `0022` before being removed.
- [x] Final automated validation: `202 passed, 3` credential-gated live tests
  skipped; Ruff, format, and mypy pass. No live Telegram or live-LLM call was
  made.

### Migration issue found during validation

The first populated PostgreSQL attempt rolled back transactionally because the
lightweight Alembic seed table did not declare `limitations` as JSONB, causing
asyncpg to reject Python lists. The column is now explicitly typed, the final
migration passed, and the SQLite migration regression still passes. The same
validation exposed a pre-existing `profile_settings_sessions.pending_answers`
JSON/JSONB mismatch; `0022` now reconciles it. Alembic autogeneration also
ignores LangGraph's library-managed checkpoint tables instead of proposing to
delete them.

### Historical-goal resume correction

The first real post-migration availability submission exposed two deployment
and resume conditions. The running bot/API image predated `0022`, so its old
equipment query failed against the upgraded database; all backend services were
rebuilt and recreated from the current image. The affected active goal was also
intentionally left unclassified by the conservative migration. Equipment
resume now stages that persisted goal, classifies it through the focused
workflow without treating the athlete's retry text as goal content, asks for
canonical confirmation, and then opens Equipment & access using the already
saved availability. A focused regression covers this exact path. Final local
validation is `203 passed, 3 skipped`; Ruff, format, and mypy pass.

The next live attempt exposed that the immediate availability-success path
still called the strict capability lookup directly before entering that resume
helper. Availability had committed successfully, but the following
`goal_classification_required` error produced Telegram's generic failure. The
post-availability transition now uses the same classify-or-review helper, and
the regression starts at `AVAILABILITY_INTAKE` to cover the exact transaction
boundary rather than only a later retry checkpoint.

### Goal catalog response-shape correction

A real goal-intake call for Ironman 70.3 plus muscle retention reached the
provider successfully, but DeepSeek copied the compact catalog row shape into
`primary_template` and `supporting_template`: it returned `kind` and omitted
the required `decision`. The semantic classification itself was correct, but
strict boundary validation safely rejected the response.

- [x] Rename the catalog's prompt-only discriminator from `kind` to
  `template_type`, bump the static goal contract to version 4, and show the
  exact `USE_EXISTING` candidate shape.
- [x] Add a narrow boundary repair for an exact copied ACTIVE catalog row. It
  verifies code, type, display name, and description against the invocation
  snapshot before converting it to `USE_EXISTING`; altered or unknown rows
  remain invalid.
- [x] Cover the provider's observed primary/supporting response and the
  altered-row rejection without weakening the Pydantic contract.
- [x] Rebuild and recreate API/bot, then verify the exact reported sentence
  through the live compiled workflow and real active catalog. It returns an
  extracted `TRIATHLON_HALF_DISTANCE` primary and `MUSCLE_RETENTION` supporting
  template, both `USE_EXISTING`, with no error. Because no event date was
  supplied, the existing deterministic flow asks for the date or offers
  `Not yet`. Final local validation is `206 passed, 3 skipped`; Ruff, format,
  and mypy pass.

### Seed simplification

The initial catalog no longer seeds `safe_running_route`, `trail_access`,
`backpack`, `trekking_poles`, `bodyweight_space`, or
`training_space_unspecified`. Their execution requirements and `0022` backfill
mappings were removed. Because bodyweight execution then had no required
capability, its two options and the now-unused `strength_bodyweight` context
were also removed. Historical migrations `0014`, `0017`, and `0018` retain
their original rows so the pre-`0022` schema history remains reproducible. The
existing representative seed test now also checks every relation and verifies
that every execution option retains at least one required capability.

The populated local PostgreSQL database contained one athlete selection for
`safe_running_route` and one for `bodyweight_space`. After an explicit request
to remove them, irreversible migration `0023_prune_training_catalog_seed`
deletes those owned selections, all six retired seeded capabilities, their
execution requirements, the two bodyweight options, and the retired context.
Only `SEEDED` version-one definitions are eligible for deletion. A full custom
PostgreSQL backup was created at
`backups/adaptive_coach_pre_0023_20260813.dump` before applying the migration.

### Live catalog-expansion JSON-mode correction

The first manual creation attempt classified `ULTRA_RUNNING_12H` correctly but
failed after confirmation. The context-mapping call initially succeeded, while
the capability stage and subsequent retries returned DeepSeek HTTP 400
`invalid_request_error` before producing output. The expansion adapter uses
JSON mode, and unlike the goal-classification prompt, neither expansion system
prompt explicitly requested JSON. Both mapping and capability contracts now
require exactly one JSON object matching the schema; a focused regression keeps
that provider requirement visible. The confirmed candidate remains staged and
no partial catalog row was published.

Once JSON mode was accepted, the live response exposed a second contract issue:
DeepSeek returned intuitive top-level `mappings` and `new_contexts` fields
instead of the required nested `templates[].contexts[]` proposals. The mapping
prompt now states every exact field and includes a valid `USE_EXISTING` JSON
example; the capability prompt likewise enumerates its exact nested contract.
A live isolated retry for the staged template now succeeds and reuses
`running_road`, `running_treadmill`, and `strength_general`, so no capability
stage is required for this particular goal.

### Direct-modality catalog expansion correction

The first `ROWING_REGATTA` manual test published the new template but mapped it
only to seeded `strength_general` as TARGET, so no context or capability stage
ran. The mapping contract now defines TARGET as direct practice of the goal's
sport, movement, and environment; generic conditioning may be SUPPORTING but
cannot replace a missing direct modality. Every primary mapping must contain a
TARGET, and unknown broad modalities such as rowing use discipline `OTHER`
instead of inventing enum values.

DeepSeek JSON mode also required the exact generated JSON Schema in each
expansion prompt. The capability contract now limits knowledge to EQUIPMENT,
ACCESS, and FACILITY, forbids methods/services/content, requires exact
PREFERRED/SUBSTITUTE roles and integer priorities, and permits USE_EXISTING
only for exact active capability codes. Expansion alone uses a 60-second HTTP
timeout and 70-second workflow timeout; ordinary onboarding remains at its
existing limits. A live isolated rowing run now proposes a direct rowing
context, four execution options, and six CREATE capabilities without writing
partial data.

### Dynamic catalog publication ordering correction

The first validated live rowing publication exposed a PostgreSQL-only ordering
defect: the service assigned UUIDs directly and added contexts, execution
options, and requirements in one flush, but no ORM relationships told
SQLAlchemy that the new `training_contexts` rows had to be inserted first.
PostgreSQL rejected the option foreign key and the transaction rolled back in
full. Publication now flushes each dependency boundary explicitly: templates
and contexts, then capabilities, then execution options, then requirements. A
focused database test publishes this complete dynamic hierarchy.

After rebuilding API and bot, a fresh live expansion produced and validated
`rowing_regatta` as a new TARGET context, five new rowing capabilities, and
on-water, indoor-machine, and club execution options. A backup was created at
`backups/adaptive_coach_pre_rowing_repair_20260813.dump`. The incorrect
`ROWING_REGATTA -> strength_general` relation was then replaced atomically;
the existing athlete goal was preserved and now references the corrected
template. Database verification confirms all context, option, and requirement
relations and both runtime containers are healthy.

Final validation after the publication fix is `209 passed, 3 skipped`; Ruff,
format, mypy, `alembic upgrade head`, `alembic current`, and `alembic check`
all pass.

### Catalog expansion prompt compaction

The mapping and capability prompts now leave field names, enums, and structural
limits to the attached exact JSON Schema and retain only semantic catalog rules.
The context-mapping request also omits the redundant active goal-template
snapshot. Mapping prefers the smallest useful context set and reuses general
supporting contexts instead of generating sport-named conditioning duplicates.
The capability prompt retains an explicit declaration/reference invariant and
a minimal reused-capability example because live DeepSeek checks showed that an
abstract instruction alone could omit a referenced capability intermittently.

The final static prompts are approximately 1.2 KB and 1.4 KB. An isolated live
`CANOE_SPRINT` run (without publication) passed application validation: it
created one direct canoe TARGET, reused seeded strength/functional supporting
contexts, created canoe-specific resources, and correctly reused
`gym_access`/`free_weights`.

Final validation remains `209 passed, 3 skipped`; Ruff, format, and mypy pass.
The compacted prompts are deployed and API, bot, and PostgreSQL are healthy.

## Follow-up: optional workout-history import (2026-08-13)

The current onboarding now ends with an explicit optional training-history
decision after limitations. This follow-up intentionally imports source facts
only; athlete baseline calculation, subjective feedback, planning, and
adaptation remain future work.

- [x] Add `TRAINING_HISTORY_IMPORT`, **Skip for now**, resumable failure copy,
  and atomic onboarding completion after a successful Apple Health/TCX import.
- [x] Add migration `0024_training_history_import` with import context,
  onboarding-session provenance, `SwimmingEnvironment.UNKNOWN`, and
  owner-scoped normalized heart-rate observations.
- [x] Use Apple `source_record_key` as stable workout identity, retain direct
  metadata and raw `WorkoutStatistics` provenance, and stop demoting swims when
  their environment is unknown.
- [x] Persist quality-labelled Apple and TCX HR observations. Only exact or
  short-interval Apple observations drive average/max aggregates; coarse data
  remains available for future recalculation.
- [x] Keep Apple parsing limited to workouts and overlapping HR. Clinical CDA,
  sleep, body composition, activity summaries, gait, audio, and unrelated
  HealthKit records are not imported. Original files are deleted after use.
- [x] Cover source matching, zero-workout recovery, skip/import completion,
  active-import conflicts, stable identity, unknown swims, ownership, and
  workout/observation idempotency.

Validation evidence:

- `215 passed, 3 skipped`; the skipped tests require explicit live-model
  credentials. Ruff, Ruff format, and mypy pass.
- PostgreSQL migration `0024` upgrades, downgrades to `0023`, upgrades again,
  and `alembic check` reports no pending operations.
- The attached Apple Health export was processed in an isolated in-memory
  database without retaining its records: 28 workouts were imported (9 run,
  5 cycling, 14 swimming), 244 HR observations were retained, onboarding was
  completed, and an exact second import left both counts unchanged.
- No live Telegram or live-LLM validation is claimed.

## Follow-up: ZIP activity metric persistence investigation (2026-08-16)

- [x] Inspect the real root-level Apple Health ZIP: it contains one workout
  XML (28 workouts: 9 running, 5 cycling, 14 swimming) and a CDA XML that is
  intentionally not an activity source.
- [x] Trace a real workout through extraction, parser, adapter, normalization,
  canonical detail mapping, repository, PostgreSQL, and read projection. The
  archive produces 244 matched HR observations, but all are one-hour
  `COARSE_INTERVAL` samples; the parser summary excluded them from aggregate
  HR, leaving canonical average/max HR `NULL` even though observations were
  persisted.
- [x] Inventory source metrics: duration, distance, active energy, HR
  observations, and swimming stroke-count records. Duration and distance were
  already canonical; active energy was retained only in source-link provenance;
  swimming stroke count is not currently parsed; HR observations were
  persisted but coarse HR aggregates were not.
- [x] Persist useful activity metrics at the canonical/read boundary with
  explicit units. HR is retained as quality-labelled observations plus a
  sample aggregate, and calories are typed as `calories_kcal` on every detail
  variant. Swimming stroke count is not promoted because the source does not
  identify a safe pool/open-water mapping and the existing typed field is
  pool-specific.
- [x] Add a real-archive end-to-end regression and validate the real import
  against PostgreSQL.
- [x] Full validation: `221 passed, 3 skipped`, Ruff, Ruff format, mypy,
  `alembic upgrade head`, `alembic check`, and `git diff --check` pass.

## Catalog resolve/reuse/create flow (2026-08-15)

Canonical goal, context, and capability resolution now follows one invariant:
existing canonical catalog definitions are the source of truth and are reused
verbatim, and the LLM is only a semantic proposal engine for genuinely NEW
goals.

- The predefined catalog is assumed complete. When a confirmed goal template
  already exists, `OnboardingService.confirm_goal` reuses its ID, contexts,
  and capabilities with zero LLM calls and zero catalog mutation.
- Only when a goal template does not exist does onboarding run
  `_run_catalog_expansion`, which asks the LLM for the complete context set
  (`map_goal_contexts`) and then, goal-aware, the complete capability set for
  every resulting required context (`define_context_capabilities`), including
  contexts reused from the canonical catalog. The goal drafts are supplied to
  the capability call so the model reasons about the goal and each context
  together.
- The backend is authoritative: `TrainingCatalogPublicationService` reconciles
  every `CREATE`/`USE_EXISTING` proposal against live catalog rows, reuses
  existing entities by code, creates only missing ones, and inserts only
  missing links inside one advisory-lock-protected transaction. `publish` no
  longer takes an `existing_definition_contexts` argument and never repairs an
  existing context.
- The structural-completeness trigger (`incomplete_goal_template_ids`,
  `incomplete_contexts_for_goals`, `goal_definition_complete`, and
  `_ensure_goal_definition`) was removed. An incomplete predefined goal is a
  catalog/data-integrity concern, not a reason to invoke the LLM.
- The two static prompts now live in
  `app.workflows.prompts.catalog_expansion` as `NEW_GOAL_CONTEXT_EXPANSION`
  and `GOAL_CONTEXT_CAPABILITY_EXPANSION` (each versioned), and the workflow
  node imports them instead of embedding prompt strings.
- The deterministic `CapabilityAssessmentService` equipment/access review is
  unchanged and read-only.
- Tests cover: complete existing-goal reuse with zero expansion calls and no
  writes, new-goal reuse of existing contexts/capabilities, idempotent reuse
  across athletes, and atomic failure/retry.
- Validation evidence: 219 passed, 3 skipped, Ruff, Ruff format, and mypy
  pass. No live Telegram or live-LLM validation is claimed.

## Follow-up: new-goal capability resolution through assessment (2026-08-16)

- [x] Execute the confirmed-new-goal path with HYROX absent from the canonical
  goal table and inspect extraction, context mapping, capability definition,
  publication, persisted links, and equipment assessment.
- [x] Fix the first semantic-loss boundary: capability expansion previously ran
  only when a context was newly created. A new goal that correctly reused
  existing contexts therefore skipped goal-aware capability resolution and the
  review loaded only generic context requirements.
- [x] Define capabilities for every context resolved for a new goal, including
  `USE_EXISTING` contexts; publish their new options, capability rows, and
  requirement links idempotently while preserving the no-expansion path for
  already-canonical goals.
- [x] Make the deterministic mock honor the active goal catalog and derive
  context reuse from supplied semantic text instead of defaulting every unknown
  goal to road running.
- [x] Add a real LangGraph-backed HYROX scenario covering goal extraction,
  reuse of `running_road`/`running_shoes`, creation of distinct SkiErg, sled,
  burpee broad-jump, rowing, farmer-carry, sandbag-lunge, and wall-ball
  contexts/capabilities, atomic publication, and the final equipment review.

Validation evidence: the focused onboarding/catalog/scenario tests pass, and
the full suite passes with `221 passed, 3 skipped`. Live provider and Telegram
validation remain unclaimed.

## Follow-up: semantic catalog dataset validation (2026-08-16)

- [x] Add a 30-case semantic dataset covering running, road cycling, mountain
  biking, triathlon, pool/open-water swimming, HYROX, rowing, rafting, hiking,
  strength, reuse/create combinations, and existing-goal controls.
- [x] Run the dataset through the real goal extraction, catalog-expansion,
  publication, and equipment-review flow with recorded structured inputs and
  outputs at each model boundary.
- [x] Verify that HYROX reuses running while creating each materially distinct
  race challenge, rowing remains a context rather than equipment, and rafting
  remains a distinct water-sport modality.
- [x] Publish the complete dataset/result table and final structures in
  `docs/semantic-catalog-report.md`.

Validation evidence: the semantic dataset passes `30/30`; the complete suite
passes with `251 passed, 3 skipped`, Ruff, Ruff format, mypy, and
`git diff --check`. No live provider, Telegram, or live-LLM validation is
claimed.

## Follow-up: catalog expansion progress and operational logs (2026-08-16)

- [x] Show an immediate `Processing your goal...` response when the Telegram
  confirmation callback starts, so long context/capability expansion is visible
  to the athlete.
- [x] Add safe structured logs for callback receipt, goal resolution, context
  expansion, capability expansion, publication, rate limiting, and failures.
- [x] Keep logs free of raw user text, health data, profiles, tokens, and model
  payloads; catalog codes and lifecycle identifiers are sufficient for Docker
  diagnosis.
- [x] Add a Telegram handler regression test for the processing response.

Validation evidence: complete suite passes `252 passed, 3 skipped`; Ruff, Ruff
format, mypy, and `git diff --check` pass. The bot image was rebuilt and
restarted with the progress message and logs enabled. Use
`docker compose logs -f --tail=100 bot` to follow them.

## Follow-up: PostgreSQL confirmation deadlock (2026-08-16)

- [x] Diagnose the live Telegram symptom where `ob:v1:goal:confirm` was
  acknowledged but never produced a response.
- [x] Confirm the callback reached the bot and identify the wait: `confirm_goal`
  held the onboarding row lock in its outer transaction while starting the
  second-session catalog expansion workflow.
- [x] Close the goal-resolution transaction before invoking the LLM expansion;
  keep publication and athlete-goal persistence atomic in the finalization
  transaction.
- [x] Add safe callback receipt/acknowledgement/handling logs for future
  Telegram diagnosis.
- [x] Rebuild the bot container, release the stale PostgreSQL transaction, and
  verify the focused bot/onboarding tests and complete suite.

Validation evidence: callback deadlock reproduced against the local PostgreSQL
runtime, fixed, and cleared after bot restart; focused bot/onboarding tests
pass `30/30`; complete suite passes `251 passed, 3 skipped`; Ruff, Ruff
format, mypy, and `git diff --check` pass. Live Telegram delivery after the
fix still requires pressing the button again; no production deployment is
claimed.

## Follow-up: canonical HYROX seed and immutable existing goals (2026-08-16)

- [x] Correct the canonical HYROX definition to reuse `running_road` while
  retaining seven distinct station contexts: SkiErg, sled push/pull, burpee
  broad jump, rowing, farmer carry, sandbag lunge, and wall balls.
- [x] Add goal-aware seeded capabilities and execution requirements for every
  HYROX station; remove the generic `functional_fitness` HYROX link.
- [x] Add idempotent migration `0026_complete_hyrox_catalog`, including the
  missing-goal case encountered during PostgreSQL upgrade and preservation of
  existing row IDs when present.
- [x] Keep existing canonical goals immutable at onboarding: no expansion
  calls, catalog writes, completeness repair, or capability regeneration.
- [x] Exercise new-goal and existing-goal paths through the real onboarding,
  LangGraph boundaries, publication, and equipment assessment; the 30-case
  dataset includes existing Ironman/triathlon and existing HYROX controls.

Validation evidence: `251 passed, 3 skipped`; targeted catalog/scenario tests
pass `40/40`; Ruff, Ruff format, mypy, `git diff --check`, `alembic upgrade
head`, `alembic current`, and `alembic check` pass. PostgreSQL verification
shows eight HYROX contexts, no `functional_fitness` link, and the expected
station capabilities. No live provider, Telegram, or live-LLM validation is
claimed.

## Follow-up: capability definition scope mismatch (2026-08-16)

- [x] Diagnose the live publication failure after DeepSeek returned a successful
  capability expansion: `invalid_context_definition_scope` means the second
  LLM response renamed or omitted one of the contexts produced by the first
  expansion.
- [x] Add safe logs for the mapped context codes, defined capability-context
  codes, capability codes, and the exact scope mismatch.
- [x] Make the capability prompt require exactly one definition for every
  `new_training_contexts` code and explicitly forbid creating or renaming
  contexts, while retaining strict backend validation.

Validation evidence: prompt contract length remains below the regression limit;
targeted prompt/onboarding tests pass. Docker must be rebuilt before retrying
the live callback; successful live publication remains unclaimed until the
retry shows matching scopes and `catalog_publication_finished`.

## Follow-up: deterministic execution-option catalog compiler (2026-08-20)

- [x] Supply active execution options and their exact capability requirements to
  goal-context capability expansion.
- [x] Require explicit `USE_EXISTING`/`CREATE` decisions and validate reuse
  against the persisted target, execution context, role, priority, limitations,
  and requirement set.
- [x] Keep context creation in the mapping phase; capability expansion can only
  create capabilities and execution options.
- [x] Add safe publication logs that list option codes grouped by reuse/create
  decision, without model payloads or personal text.
- [x] Add idempotent migration `0027_catalog_option_standard` to remove only
  `ROWING_REGATTA -> hyrox_row`, preserving the HYROX station context and the
  `rowing_ergometer` capability.
- [x] Exercise reuse, collision, definition-mismatch, invalid execution-context,
  atomicity, and semantic dataset regressions.

Validation evidence: live DeepSeek execution in a disposable PostgreSQL database
completed a new HYROX semantic goal through context mapping, capability expansion,
deterministic publication, and capability assessment. The resulting assessment
read `hyrox_row` and `rowing_ergometer` from the persisted graph. The real Docker
database is at `0027_catalog_option_standard`; its HYROX graph retains eight
contexts and no `ROWING_REGATTA -> hyrox_row` relation. Complete validation passes
`257 passed, 3 skipped`, Ruff, Ruff format, mypy, and `git diff --check`; the API
is healthy and the bot is running with the rebuilt image.

## Fitness-state design investigation (2026-08-20)

This investigation established the reduced baseline design implemented in the
follow-up below. The original baseline and Strava implementation had been
removed from the active application schema and code during the development
cleanup; the older Alembic history still contains their historical tables but is
not an active application contract.

- The current durable evidence sources are Apple Health ZIP and TCX imports.
  They preserve UTC start time, elapsed duration, distance when supplied,
  calories, per-discipline type/environment, average/max heart rate, and
  quality-labelled HR observations. TCX additionally preserves cadence,
  elevation, and route points in source provenance.
- Neither active importer supplies `moving_duration_seconds`, power, laps, or
  splits. Derived canonical pace/speed is therefore normally unavailable; any
  elapsed-duration pace calculated by a future engine must be explicitly
  labelled lower-quality evidence rather than sustainable performance.
- The reduced fitness milestone introduces only immutable historical
  `athlete_baseline_assessments`. A future current projection and immutable
  snapshots remain deferred. Baselines use the existing broad `Discipline`
  enum rather than a foreign key to the dynamic planning catalog, whose
  contexts can be arbitrary goal-specific concepts.
- Versioned deterministic calculation inputs, evidence summaries, source-workout
  watermark, confidence, and calculation version are required so the future
  planner can consume the current projection while results remain reproducible.

## Follow-up: immutable goal-scoped import baseline (2026-08-20)

- [x] Keep only `athlete_baseline_assessments`, keyed by the existing
  `Discipline` enum; defer current fitness, snapshots, and a scheduler.
- [x] Build one immutable 14-day evidence baseline from the most recent owned
  workout in each primary/supporting-goal discipline, preserving provenance,
  quality flags, confidence, and a deterministic input digest.
- [x] Run that calculation only inside successful non-duplicate Apple Health
  and TCX import transactions; never recalculate an existing baseline.
- [x] Preserve the owned workout evidence timestamp/index and conservatively
  exclude likely Apple/TCX cross-source duplicate pairs from aggregates.
- [x] On an explicit history Skip, complete onboarding and explain that a later
  Apple Health or TCX import can personalize the starting point.

Validation: `pytest` passes `266 passed, 3 skipped`; Ruff, Ruff format, mypy,
and `git diff --check` pass. The isolated SQLite 0028 migration round-trip
(upgrade, downgrade, re-upgrade) also passes, including cleanup of the locally
superseded current/snapshot tables. Docker Desktop is unavailable in this
environment, so the requested PostgreSQL `alembic upgrade head` and `alembic
check` could not connect.

## Follow-up: weekly plan from imported workouts (2026-08-21)

- [x] Add a single immutable `weekly_training_plans` record per athlete and
  following Monday, with a structured seven-day plan, redacted evidence
  snapshot, deterministic digest, model/prompt/calculation versions, and safe
  planner LLM-usage feature metadata.
- [x] Gate model invocation per primary-goal `TARGET` discipline with a
  planner-specific 30-day calculator window: at least three deduplicated sessions over two
  active days. Supporting contexts do not affect that gate.
- [x] Create only missing TARGET baselines after passing the gate; preserve the
  existing immutable baseline behavior and use current aggregated evidence in
  the planner prompt.
- [x] Add deterministic Telegram routes and persistent keyboard state for Add
  workout, Plan next week, and View weekly plan. Viewing reads the saved plan
  only; insufficient evidence and provider failures create no plan.
- [x] Add migration `0029_weekly_training_plans`; it also removes two obsolete
  local current-fitness prototype tables when upgrading a developer database.

Validation: `274 passed, 3 skipped` (the three skipped tests require explicit
live-provider credentials); Ruff, Ruff format, mypy, `git diff --check`,
`alembic upgrade head`, `alembic current`, and `alembic check` pass against
the local PostgreSQL database. The applied head is
`0029_weekly_training_plans`.

## Follow-up: planner prompt and evidence separation (2026-08-21)

- [x] Move the versioned weekly planner prompt and message construction into
  `app.workflows.prompts.weekly_planning`, alongside the existing onboarding
  and catalog prompt contracts.
- [x] Move planner-only readiness, redacted evidence snapshot, and input-digest
  transforms into `app.services.weekly_planning.evidence`; retain database and
  provider orchestration in the service.
- [x] Add `planner_window_days=30`; only planning preflight and recent context
  use it. Immutable imported baselines retain `fitness_window_days=14`.

Validation: `279 passed, 3 skipped`; Ruff, Ruff format, mypy, `git diff
--check`, and `alembic check` pass. No schema migration was needed.

## Follow-up: manual iPhone HealthKit POC (2026-08-21)

- [x] Add a deliberately opt-in, revocable mobile credential owned by one
  athlete. Telegram issues a one-time ten-minute pairing code; PostgreSQL stores
  only SHA-256 hashes for that code and the opaque iPhone bearer token.
- [x] Add deterministic `/connect_iphone` and `/disconnect_iphone` bot commands
  with no LLM invocation, plus disabled and missing-connection responses that
  reveal no credential data.
- [x] Add authenticated pairing and HealthKit workout-sync API routes. The
  bounded POC payload contains only the HealthKit workout UUID, activity type,
  UTC interval, duration, optional distance, and optional active calories; it
  never accepts an athlete ID.
- [x] Reuse the source-neutral workout import boundary with
  `APPLE_HEALTH` / `healthkit:<uuid>` exact identity and safe
  `HEALTHKIT_IOS_POC` provenance. Sync deliberately never creates a baseline;
  the 30-day planner gate remains responsible for creating any missing baseline.
- [x] Add the isolated `ios/CoachHealthSync` SwiftUI project: Keychain-only
  token storage, HealthKit workout-read permission, a seven-day summary list,
  single-workout manual sync, local ignored HTTPS configuration, and a Monday
  device-test guide.
- [x] Add mobile payload/credential log redaction and tests for pairing expiry,
  one-time use, revocation, ownership, idempotency, invalid payloads, no
  baseline creation, migration ownership, adapter mapping, and planner
  visibility.

Validation: backend `pytest` passes `296 passed, 3 skipped`; Ruff, Ruff format,
mypy, and `git diff --check` pass. PostgreSQL migration verification completed
with `alembic upgrade head`, `alembic downgrade 0029_weekly_training_plans`,
`alembic upgrade head`, and `alembic check`; the head is
`0030_mobile_sync_credentials`. The iOS project received static source/project
reference checks on Windows, but its Xcode build, HealthKit permission flow,
Xiaomi-to-Apple-Health bridge, and live HTTPS-tunnel sync remain pending the
MacBook/iPhone proof described in `ios/CoachHealthSync/README.md`.
The local API and bot containers were rebuilt; `/ready` and the disabled mobile
route contract are available locally, but no live Telegram or iPhone pairing is
claimed while the opt-in flag remains off.
