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
- Strava's current reference documents `activity:read` for listing logged-in
  athlete activities; `activity:read_all` is needed only for activities with
  Only Me visibility. This milestone requests the narrower `activity:read`
  scope.

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
