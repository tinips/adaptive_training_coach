# Onboarding and Strava Vertical Slice

> **2026-08-03 scope amendment:** The original multi-step onboarding described
> below is retained as historical implementation context, not current product
> behavior. Current onboarding ends immediately after explicit conversational
> goal confirmation. See the final amendment section and
> `docs/current-product-flow.md` for the supported architecture.

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
