# Repository Working Agreement

This repository implements the first vertical slice of an adaptive endurance
coach. The active implementation plan is:

`/.agent/execplans/onboarding-strava-vertical-slice.md`

## Product boundaries

- The rendered product interface is English-only for this milestone.
- This milestone covers Telegram onboarding, normalized athlete profiles,
  Strava ingestion, and deterministic athlete baselines.
- It does not generate training plans, use RAG, expose a dashboard, or provide
  medical diagnosis.
- Deterministic Telegram callbacks must not invoke an LLM.
- Explicit free-text onboarding paths must use the compiled LangGraph workflow
  and LangChain structured output.
- PostgreSQL is the source of truth. LangGraph does not persist checkpoints.

## Engineering conventions

- Keep Telegram handlers thin. Business behavior belongs in application
  services and repository methods.
- Centralize Telegram messages in `backend/app/bot/messages.py`.
- Centralize Telegram keyboard labels in `backend/app/bot/keyboards.py`.
- Enforce `user_id` ownership in every personal-data repository operation.
- Never log secrets, OAuth tokens, raw health descriptions, full profiles, or
  unredacted free-text answers.
- Use timezone-aware UTC timestamps, explicit enums, Pydantic boundary models,
  SQLAlchemy 2 async APIs, and narrowly scoped exception types.
- Update the active ExecPlan whenever a phase completes, a failure is found, or
  a design decision changes.

## Validation

Run from `backend` unless a command says otherwise:

```powershell
pytest
ruff check .
ruff format --check .
mypy app
```

From the repository root, validate the database and runtime with:

```powershell
docker compose up -d db
cd backend
alembic upgrade head
```

Do not claim live Telegram, Strava, webhook, or live-LLM validation unless the
required external credentials and public callback URL were actually used.
