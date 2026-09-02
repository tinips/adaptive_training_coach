# Repository Guidelines

## Project Structure & Module Organization

This is an adaptive endurance coach: a Python backend and Telegram bot.
Backend code is in `backend/app/`: `api/` contains FastAPI routes (health
checks plus two Telegram Mini App form endpoints), `bot/` Telegram
presentation/routing, `services/` application behavior, `repositories/`
persistence, `schemas/` Pydantic boundaries, `integrations/` external
adapters (LLM providers, Apple Health/TCX parsing), and `workflows/prompts/`
the one remaining LLM prompt module (the weekly planner). Database models
and migrations live in `backend/app/db/` and `backend/alembic/`. Keep tests
under `backend/tests/`, grouped by `unit/`, `api/`, `integration/`,
`use_cases/`, `bot/`, and `scenarios/`. Product scope and architecture
decisions live in [STATE.md](STATE.md); this file covers conventions only.

## Build, Test, and Development Commands

Run backend commands from `backend/` after installing `pip install -e ".[dev]"`:

```powershell
pytest                       # full deterministic test suite
ruff check .                  # lint and import-order checks
ruff format --check .         # verify formatting
mypy app                      # strict static type checks
alembic upgrade head          # apply schema migrations
```

From the repository root, `docker compose up --build` starts PostgreSQL, a
one-shot migration container, FastAPI (`http://localhost:8000`), the bot, and
`adminer`. Use `docker compose up -d db` when validating migrations locally,
or `docker compose up -d --build --no-deps bot` / `... --no-deps api` to
rebuild just one service after a code-only change.

## Coding Style & Naming Conventions

Target Python 3.12, use four-space indentation, double quotes, and an
88-column limit; Ruff enforces these (`select = ["E", "F", "I", "UP", "B",
"ASYNC", "RUF"]`). Keep types complete — `mypy app` runs with `strict = true`.
Use `snake_case` for Python modules/functions, `PascalCase` for classes, and
descriptive test names such as `test_mobile_sync_rejects_unknown_token`. Use
SQLAlchemy 2 async APIs, Pydantic models at every boundary, timezone-aware
UTC timestamps, and explicit enums (`StrEnum`). Keep Telegram text and
keyboard labels in `app/bot/messages.py` and `app/bot/keyboards.py`;
handlers should stay thin and delegate to services.

## Testing, Data, and Security

Add focused regression coverage with every behavior change. Tests use a
deterministic model double, mocked provider transports, and synthetic Apple
Health/TCX fixtures; mark a test that calls a real model provider and a real
PostgreSQL database with `@pytest.mark.live` (the only registered marker —
`--strict-markers` will reject an unregistered one). Add an Alembic revision
for persistent schema changes and test upgrades when practical. When a
migration destroys data that cannot be reconstructed (a dropped column, a
retired catalog row an athlete may already reference), prefer retiring rows
over deleting them where a foreign key could still point at them, and make
`downgrade()` raise `NotImplementedError` with an explanation rather than
silently faking a revert — several existing migrations do this
(`0032_retire_non_endurance_goals`, `0037_prune_equipment_catalog`,
`0045_remove_mobile_sync`, `0046_structured_availability_only`) and it's the
established pattern here. Never commit `.env`, credentials, device tokens,
or health/profile text; repository operations must scope personal data by
`user_id`.

## Environment and Configuration

`app/config.py` defines one `Settings` object (Pydantic Settings) loaded from
`.env` at the repository root; copy `.env.example` to `.env` and fill in only
what you need — every LLM/Langfuse/screenshot feature defaults to off or to a
deterministic mock. Never commit `.env`. `Settings.exposed_configuration()`
is the only settings surface allowed in diagnostics/logs; do not log the
`Settings` object directly, since it also holds secrets (`SecretStr` fields).

## Commits & Pull Requests

Recent history uses imperative, sentence-case subjects (for example,
`Retire the mountain-bike race primary goal`). Keep commits narrow. Pull
requests should explain the user-visible change, note migrations/configuration,
link the relevant issue or design document, and include test results. Add
screenshots for Telegram UI changes.
