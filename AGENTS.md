# Repository Guidelines

## Project Structure & Module Organization

This is an adaptive endurance coach: a Python backend and Telegram bot, plus a
small iOS HealthKit sync companion. Backend code is in `backend/app/`:
`api/` contains FastAPI routes, `bot/` Telegram presentation/routing,
`services/` application behavior, `repositories/` persistence, `schemas/`
Pydantic boundaries, and `integrations/` external adapters. Database models and
migrations live in `backend/app/db/` and `backend/alembic/`. Keep tests under
`backend/tests/`, grouped by `unit/`, `api/`, `integration/`, `use_cases/`, and
`bot/`. The iOS project is `ios/CoachHealthSync/`; product decisions and flow
documentation are in `docs/`.

## Build, Test, and Development Commands

Run backend commands from `backend/` after installing `pip install -e ".[dev]"`:

```powershell
pytest                       # full deterministic test suite
ruff check .                  # lint and import-order checks
ruff format --check .         # verify formatting
mypy app                      # strict static type checks
alembic upgrade head          # apply schema migrations
```

From the repository root, `docker compose up --build` starts PostgreSQL,
migrations, FastAPI (`http://localhost:8000`), and the bot. Use
`docker compose up -d db` when validating migrations locally. For iOS, open
`ios/CoachHealthSync/CoachHealthSync.xcodeproj` in Xcode and run tests against
a configured scheme/device; HealthKit behavior requires a physical iPhone.

## Coding Style & Naming Conventions

Target Python 3.12, use four-space indentation, double quotes, and an 88-column
limit; Ruff enforces these rules. Keep types complete—`mypy app` runs in strict
mode. Use `snake_case` for Python modules/functions, `PascalCase` for classes,
and descriptive test names such as `test_mobile_sync_rejects_unknown_token`.
Use SQLAlchemy async APIs, Pydantic models at boundaries, timezone-aware UTC
timestamps, and explicit enums. Keep Telegram text and keyboard labels in
`app/bot/messages.py` and `app/bot/keyboards.py`; handlers should delegate to
services.

## Testing, Data, and Security

Add focused regression coverage with every behavior change. Tests use mocked
providers and synthetic Apple Health/TCX data; mark real-provider/PostgreSQL
tests with `@pytest.mark.live`. Add an Alembic revision for persistent schema
changes and test upgrades when practical. Never commit `.env`, iOS
`Config/Developer.xcconfig`, credentials, device tokens, or health/profile text;
repository operations must scope personal data by `user_id`.

## Commits & Pull Requests

Recent history uses imperative, sentence-case subjects (for example,
`Retire the mountain-bike race primary goal`). Keep commits narrow. Pull
requests should explain the user-visible change, note migrations/configuration,
link the relevant issue or design document, and include test results. Add
screenshots for Telegram/iOS UI changes.
