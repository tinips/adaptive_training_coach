# Onboarding and Strava Vertical Slice

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
- [x] Upsert canonical activities and relevant heart-rate observations,
  deduplicate cumulative exports, and persist only safe job metadata/counters.
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
