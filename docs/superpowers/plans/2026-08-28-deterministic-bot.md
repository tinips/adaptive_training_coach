# Deterministic Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Leave the weekly planner as the only model call in the product, and narrow the goal catalog to swimming, cycling and running.

**Architecture:** Seven tasks. Three are deletions of subsystems nothing depends on, ordered first so the noise is gone before the real work. One prunes the catalog. Two replace free-text goal extraction: a two-level menu and a race date step, the only tasks that write more than they remove. One makes supporting goals reach the planner.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic, Pydantic v2, python-telegram-bot 22.8, pytest with `asyncio_mode = "auto"`.

**Spec:** `docs/superpowers/specs/2026-08-28-deterministic-bot-design.md`. Read it before starting.

## Global Constraints

- Run all commands from `backend/`. Validation is `pytest`, `ruff check .`, `ruff format --check .`, `mypy app`. All four must pass before every commit.
- The host Mac has only Python 3.9 and this project needs 3.12. Run everything through Docker: `docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c "pip install -q '.[dev]' && <command>"`. Installing 3.12 locally first will save hours.
- **Do not touch the `Discipline` enum or the `ck_workouts_workout_discipline` check constraint.** Narrowing the catalog narrows what can be planned, never what can be recorded. Spec section 3.2.
- **Do not delete `DeterministicFakeOnboardingModel`.** The weekly planner still needs a fake provider in tests.
- Keep Telegram handlers thin. Centralise messages in `app/bot/messages.py` and keyboard labels in `app/bot/keyboards.py`.
- Deterministic Telegram callbacks must not invoke a model. After this plan, nothing in the bot may.
- **Conflict warning.** Task 6 edits `app/services/weekly_planning/service.py`, which Tasks 4, 6 and 7 of `2026-08-28-planner-produces-a-valid-plan.md` also edit. Finish that plan first, or expect to merge by hand.

---

## File Structure

| File | Fate | Task |
|---|---|---|
| `app/workflows/catalog_expansion/` | delete the package | 1 |
| `app/schemas/catalog_expansion.py` | delete | 1 |
| `app/services/training_catalog/service.py` | remove expansion methods, keep the rest | 1 |
| `app/workflows/telegram_orchestrator/` | delete the package | 2 |
| `app/bot/service.py` | remove the workspace branch and parameter | 2 |
| `app/bot/main.py` | remove workspace and extractor wiring | 2, 5 |
| `app/workflows/onboarding_context/` | delete the package | 3 |
| `app/training_catalog_seed.py` | remove four primary goals and their orphan contexts | 4 |
| `alembic/versions/0030_*.py` | new: retire the removed catalog rows | 4 |
| `app/workflows/onboarding_goal/` | delete the package | 5 |
| `app/schemas/onboarding_goal.py` | delete | 5 |
| `app/repositories/training_catalog.py` | new method listing primary goals with their target disciplines | 5 |
| `app/services/training_catalog/grouping.py` | new, pure: group goals by sport | 5 |
| `app/bot/keyboards.py` | new goal menu keyboards | 5 |
| `app/services/onboarding/service.py` | menu handlers replace extractor calls | 3, 5 |
| `alembic/versions/0031_*.py` | new: add the `GOAL_EVENT_DATE` onboarding step | 5b |
| `app/services/weekly_planning/service.py` | supporting contexts reach the planner | 6 |
| `app/workflows/prompts/weekly_planning.py` | prompt explains target versus supporting | 6 |

Test files deleted: `tests/unit/test_telegram_orchestrator.py`, `tests/unit/test_onboarding_goal_graph.py`, `tests/unit/test_onboarding_context_graph.py`, `tests/use_cases/test_conversational_goal_onboarding.py`, `tests/scenarios/test_new_goal_catalog_e2e.py`, `tests/scenarios/test_semantic_catalog_dataset.py`, `tests/integration/test_live_agent_onboarding.py`.

Test files rewritten: `tests/scenarios/test_bot_journey.py`, `tests/use_cases/test_mandatory_profile_onboarding.py`, `tests/use_cases/test_training_catalog.py`.

---

### Task 1: Delete catalog expansion

All 23 goal templates and all 22 training contexts in every environment are
`source = SEEDED`. The model has never invented one. This subsystem has never done
work.

**Files:**
- Delete: `app/workflows/catalog_expansion/` (4 files), `app/schemas/catalog_expansion.py`
- Delete: `tests/scenarios/test_new_goal_catalog_e2e.py`, `tests/scenarios/test_semantic_catalog_dataset.py`
- Modify: `app/services/training_catalog/service.py`, `app/services/training_catalog/__init__.py`, `app/services/onboarding/service.py`
- Modify: `tests/use_cases/test_training_catalog.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `app/services/training_catalog/service.py` keeps only its read paths. `CatalogExpansionError` and every symbol importing from `app.schemas.catalog_expansion` are gone.

- [ ] **Step 1: Prove nothing has ever been generated**

```bash
cd .. && docker compose up -d db && sleep 5
docker compose exec -T db psql -U coach -d adaptive_coach -c \
  "SELECT source, count(*) FROM goal_templates GROUP BY source;
   SELECT source, count(*) FROM training_contexts GROUP BY source;"
cd backend
```
Expected: `SEEDED` is the only source in both tables. If any row says otherwise, **stop** and report it. A generated row means the catalog carries data this task would strand.

- [ ] **Step 2: Delete the packages and their tests**

```bash
git rm -r app/workflows/catalog_expansion
git rm app/schemas/catalog_expansion.py
git rm tests/scenarios/test_new_goal_catalog_e2e.py
git rm tests/scenarios/test_semantic_catalog_dataset.py
```

- [ ] **Step 3: Find every remaining reference**

Run:
```bash
grep -rn "catalog_expansion\|CatalogExpansion" app/ tests/
```
Expected: hits only in `app/services/training_catalog/service.py`,
`app/services/training_catalog/__init__.py`, `app/services/onboarding/service.py` and
`tests/use_cases/test_training_catalog.py`.

Remove each one. In `training_catalog/service.py` delete the imports, the
`CatalogExpansionError` class, and every method whose only purpose is to persist a
generated goal or context. Keep every read method: the planner and the new goal menu
both depend on them. In `onboarding/service.py` delete the imports and any branch
that handled an expansion outcome.

- [ ] **Step 4: Run the suite and the type checker**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest -q && mypy app"
```
Expected: PASS. `mypy` is the important one here; it catches a missed import that
`pytest` would only find at runtime.

If `tests/use_cases/test_training_catalog.py` covers expansion, delete those test
functions and keep the ones covering reads.

- [ ] **Step 5: Lint and commit**

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && ruff check . && ruff format --check . && mypy app"
git add -A
git commit -m "Delete catalog expansion

Every goal template and training context in every environment is SEEDED. The
model has never invented one, so this subsystem has never done work."
```

---

### Task 2: Delete the conversation layer

`app/bot/service.py:98` already declares the workspace as
`TelegramAgentWorkspace | None = None`, and line 205 already falls through to the
deterministic dispatcher when it is absent. A working path without it exists today.

**Files:**
- Delete: `app/workflows/telegram_orchestrator/` (2 files), `tests/unit/test_telegram_orchestrator.py`
- Modify: `app/bot/service.py`, `app/bot/main.py`, `app/workflows/prompts/onboarding.py`
- Modify: `tests/scenarios/test_bot_journey.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `TelegramBotService.__init__` no longer accepts `agent_workspace`. Free text from a finished athlete reaches `handle_text`.

- [ ] **Step 1: Write the failing test**

Append to `tests/scenarios/test_bot_journey.py`:

```python
@pytest.mark.asyncio
async def test_the_bot_service_takes_no_agent_workspace() -> None:
    """After this task nothing in the bot may call a model."""

    import inspect

    from app.bot.service import TelegramBotService

    assert "agent_workspace" not in inspect.signature(
        TelegramBotService.__init__
    ).parameters
```

- [ ] **Step 2: Run it and confirm it fails**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest tests/scenarios/test_bot_journey.py -k agent_workspace -v"
```
Expected: FAIL, the parameter is still there.

- [ ] **Step 3: Remove the branch in `app/bot/service.py`**

Delete the import block at lines 39-43. Delete the `agent_workspace` parameter at line
98 and the assignment at line 110. Then replace the tail of `_handle_agent_input`:

```python
        if self._agent_workspace is None:
            return await self._dispatch(identity, event_type, content)
        return await self._agent_workspace.invoke(
            thread_id=f"telegram:{identity.telegram_user_id}",
            message=message,
            context=TelegramAgentContext(
                user_id=cast(UUID, lifecycle["user_id"]),
                dispatcher=lambda kind, content: self._dispatch(
                    identity, kind, content
                ),
                onboarding_updater=None,
                onboarding_active=False,
            ),
        )
```

with:

```python
        return await self._dispatch(identity, event_type, content)
```

Rename `_handle_agent_input` to `_handle_input`, since nothing about it is an agent
any more, and update its call sites. Remove the now-unused `cast` and `UUID` imports
if nothing else in the file needs them; `ruff` will tell you.

- [ ] **Step 4: Remove the wiring in `app/bot/main.py`**

Delete the `TelegramAgentWorkspace` import at line 31, its construction at line 74,
the `agent_workspace=` arguments at lines 106 and 112, the field at line 44, and the
`start()` and `aclose()` calls at lines 51 and 57.

- [ ] **Step 5: Delete the package and its test**

```bash
git rm -r app/workflows/telegram_orchestrator
git rm tests/unit/test_telegram_orchestrator.py
```

Then check `app/workflows/prompts/onboarding.py`. If a prompt in it exists only for
the orchestrator, delete that prompt. If it is shared with the goal extractor, leave
it; Task 5 removes it.

- [ ] **Step 6: Run the suite**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest -q && mypy app"
```
Expected: PASS, including the new test. `tests/scenarios/test_bot_journey.py` may
construct the service with a workspace; remove that argument.

- [ ] **Step 7: Lint and commit**

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && ruff check . && ruff format --check . && mypy app"
git add -A
git commit -m "Delete the Telegram conversation layer

The workspace was already optional and the code already fell through to the
deterministic dispatcher when it was absent, so removing it exercises a path
that already existed. Free text from a finished athlete now reaches
handle_text, which is what happened whenever the workspace was not supplied."
```

---

### Task 3: Replace context validation with a length check

`app/services/onboarding/service.py:3052-3056` already stores availability and health
text exactly as typed. Its own comment says the workflow result is "only a go/no-go
validation signal". So this is a model call that judges an answer and changes nothing
else.

**Files:**
- Delete: `app/workflows/onboarding_context/` (4 files), `tests/unit/test_onboarding_context_graph.py`
- Modify: `app/services/onboarding/service.py`
- Modify: `tests/use_cases/test_mandatory_profile_onboarding.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `OnboardingService.__init__` no longer accepts `context_workflow`. A new module-level constant `_CONTEXT_TEXT_MAX_LENGTH = 2000` and a minimum of 3 characters after stripping.

- [ ] **Step 1: Write the failing test**

Append to `tests/use_cases/test_mandatory_profile_onboarding.py`:

```python
@pytest.mark.asyncio
async def test_availability_text_is_stored_verbatim_with_no_model_call() -> None:
    """The athlete's exact words are kept; only length is checked."""

    import inspect

    from app.services.onboarding.service import OnboardingService

    assert "context_workflow" not in inspect.signature(
        OnboardingService.__init__
    ).parameters
```

- [ ] **Step 2: Run it and confirm it fails**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest tests/use_cases/test_mandatory_profile_onboarding.py -k verbatim -v"
```
Expected: FAIL, the parameter is still there.

- [ ] **Step 3: Replace the validation**

In `app/services/onboarding/service.py`, add near the other module constants:

```python
# Availability and health text is stored exactly as the athlete typed it, so the
# only check is that it is neither empty nor absurd. A model was previously asked
# whether the answer was sensible; it judged nothing that mattered and changed
# nothing that was stored.
_CONTEXT_TEXT_MIN_LENGTH = 3
_CONTEXT_TEXT_MAX_LENGTH = 2000
```

Delete the `context_workflow` constructor parameter (line 167 area), the
`self._context_workflow` assignment, and the import at line 77. Then find every
`await self._context_workflow...` call and replace it with:

```python
            cleaned = text.strip()
            if not (
                _CONTEXT_TEXT_MIN_LENGTH <= len(cleaned) <= _CONTEXT_TEXT_MAX_LENGTH
            ):
                raise OnboardingApplicationError("invalid_action")
```

Delete any branch that handled a model clarification or provider failure for these
two steps. The text write at line 3052 stays exactly as it is.

- [ ] **Step 4: Delete the package and its test**

```bash
git rm -r app/workflows/onboarding_context
git rm tests/unit/test_onboarding_context_graph.py
git rm app/schemas/onboarding_context.py 2>/dev/null || true
```

Then remove the `create_context_onboarding_workflow` import and construction in
`app/bot/main.py` at lines 29 and 73.

- [ ] **Step 5: Run the suite**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest -q && mypy app"
```
Expected: PASS.

- [ ] **Step 6: Lint and commit**

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && ruff check . && ruff format --check . && mypy app"
git add -A
git commit -m "Check the length of availability text instead of asking a model

The text was already stored verbatim and the workflow was a go/no-go signal
that changed nothing else, so a bounds check does the same job."
```

---

### Task 4: Narrow the catalog to swimming, cycling and running

Four primary goals leave: `GENERAL_HIKING`, `GENERAL_STRENGTH`, `HYROX`,
`OBSTACLE_RACE`. All fourteen swim, bike and run goals stay, so a marathon runner is
as well supported as a triathlete. All five supporting goals stay, including both
strength ones.

**Files:**
- Modify: `app/training_catalog_seed.py`
- Create: `alembic/versions/0030_retire_non_endurance_goals.py`
- Create test: `tests/use_cases/test_catalog_is_endurance_only.py`

**Interfaces:**
- Consumes: nothing.
- Produces: exactly 14 rows in `goal_templates` with `kind = 'PRIMARY'` and
  `status = 'ACTIVE'`. Supporting goals are untouched.

- [ ] **Step 1: Write the failing test**

Create `tests/use_cases/test_catalog_is_endurance_only.py`:

```python
"""The planned catalog covers swimming, cycling and running only."""

from __future__ import annotations

from app.training_catalog_seed import PRIMARY_GOAL_CODES  # see step 3

RETIRED = {"GENERAL_HIKING", "GENERAL_STRENGTH", "HYROX", "OBSTACLE_RACE"}

EXPECTED_PRIMARY = {
    "GENERAL_RUNNING",
    "RUNNING_5K",
    "RUNNING_10K",
    "HALF_MARATHON",
    "MARATHON",
    "TRAIL_RACE",
    "ROAD_CYCLING_EVENT",
    "MTB_RACE",
    "OPEN_WATER_SWIM",
    "POOL_SWIMMING_EVENT",
    "TRIATHLON_SPRINT",
    "TRIATHLON_OLYMPIC",
    "TRIATHLON_HALF_DISTANCE",
    "TRIATHLON_FULL_DISTANCE",
}


def test_the_seed_offers_only_endurance_primary_goals() -> None:
    assert PRIMARY_GOAL_CODES == EXPECTED_PRIMARY


def test_no_retired_goal_remains_in_the_seed() -> None:
    assert not (PRIMARY_GOAL_CODES & RETIRED)
```

- [ ] **Step 2: Run it and confirm it fails**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest tests/use_cases/test_catalog_is_endurance_only.py -v"
```
Expected: FAIL with `ImportError` on `PRIMARY_GOAL_CODES`.

- [ ] **Step 3: Prune the seed and expose the codes**

In `app/training_catalog_seed.py`, delete the four primary goal entries and every
training context used **only** by them, for example the HYROX station contexts around
lines 213-220. A context also used by a surviving goal must stay.

Find orphans by checking, for each context you plan to remove, that no surviving goal
references it in the seed's relation table.

Then add near the top of the module:

```python
PRIMARY_GOAL_CODES: frozenset[str] = frozenset(
    code for code, kind, *_ in _GOAL_TEMPLATES if kind == "PRIMARY"
)
```

Adjust the comprehension to whatever the actual seed structure is; the entries are
tuples of `(code, kind, display_name, description)`.

- [ ] **Step 4: Write the migration**

Create `alembic/versions/0030_retire_non_endurance_goals.py`. Set `down_revision` to
the current head, which you find with:

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && alembic heads"
```

```python
"""Retire the non-endurance primary goals.

Rows are retired rather than deleted. An athlete may already have chosen one,
and training_goals.goal_template_id references them.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"  # replace with the output of `alembic heads`
branch_labels = None
depends_on = None

_RETIRED = ("GENERAL_HIKING", "GENERAL_STRENGTH", "HYROX", "OBSTACLE_RACE")


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE goal_templates SET status = 'RETIRED' "
            "WHERE code = ANY(:codes)"
        ).bindparams(sa.bindparam("codes", value=list(_RETIRED)))
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE goal_templates SET status = 'ACTIVE' "
            "WHERE code = ANY(:codes)"
        ).bindparams(sa.bindparam("codes", value=list(_RETIRED)))
    )
```

Check that `RETIRED` is a real member of the catalog status enum first:

```bash
grep -n "class CatalogItemStatus" -A 6 app/domain/enums.py
```

If it is not, use whatever member means "no longer offered". Do not add one.

Retiring rather than deleting matters: `training_goals.goal_template_id` is a foreign
key, and an athlete may already hold one of these.

- [ ] **Step 5: Run the migration and the suite**

Run:
```bash
cd .. && docker compose up -d db && sleep 5 && cd backend
docker run --rm --network adaptive_training_coach_default \
  -e DATABASE_URL="postgresql+asyncpg://coach:coach@db:5432/adaptive_coach" \
  -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && alembic upgrade head && python -m pytest -q && mypy app"
```
Expected: the migration applies and all tests PASS.

- [ ] **Step 6: Confirm against the database**

```bash
cd .. && docker compose exec -T db psql -U coach -d adaptive_coach -c \
  "SELECT kind, status, count(*) FROM goal_templates GROUP BY kind, status ORDER BY kind;"
cd backend
```
Expected: 14 PRIMARY ACTIVE, 4 PRIMARY RETIRED, 5 SUPPORTING ACTIVE.

- [ ] **Step 7: Lint and commit**

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && ruff check . && ruff format --check . && mypy app"
git add -A
git commit -m "Narrow the primary goal catalog to swimming, cycling and running

Retires hiking, general strength, Hyrox and obstacle racing. All fourteen
endurance goals stay, so a marathon runner is as well supported as a
triathlete, and all five supporting goals stay including both strength ones.
Rows are retired rather than deleted because training_goals references them."
```

---

### Task 5: Replace goal extraction with a two-level menu

This is the only task that writes more than it deletes. `goal_input_keyboard()` today
contains one button, Cancel, so the goal step is free text and nothing else. The
`ob:v1:goal:choice:` callback is not a menu; it handles a model's clarifying question.

**Files:**
- Delete: `app/workflows/onboarding_goal/` (4 files), `app/schemas/onboarding_goal.py`
- Delete: `tests/unit/test_onboarding_goal_graph.py`, `tests/use_cases/test_conversational_goal_onboarding.py`, `tests/integration/test_live_agent_onboarding.py`
- Create: `app/services/training_catalog/grouping.py`
- Create test: `tests/unit/test_goal_grouping.py`
- Modify: `app/repositories/training_catalog.py`, `app/bot/keyboards.py`, `app/bot/service.py`, `app/bot/messages.py`, `app/services/onboarding/service.py`, `app/bot/main.py`, `app/config.py`
- Modify: `tests/scenarios/test_bot_journey.py`

**Interfaces:**
- Consumes: `PRIMARY_GOAL_CODES` and the retired rows from Task 4.
- Produces:
  - `app/services/training_catalog/grouping.py` exports `GoalSport` (a `StrEnum` with `RUNNING`, `CYCLING`, `SWIMMING`, `TRIATHLON`) and `group_goals_by_sport(goals) -> dict[GoalSport, tuple[GoalOption, ...]]`, where `GoalOption` is a frozen dataclass of `code: str`, `display_name: str`, `disciplines: frozenset[Discipline]`.
  - The keyboard functions take plain strings and `(code, display_name)` pairs.
    `app/bot/keyboards.py` imports only `telegram` and `app.bot.rendering` today and
    must keep it that way, so the service layer converts before calling them.
  - Callbacks `ob:v1:goal:sport:<SPORT>`, `ob:v1:goal:template:<CODE>`, `ob:v1:goal:back`, `ob:v1:support:<CODE>`, `ob:v1:support:none`.
  - `OnboardingService.__init__` no longer accepts `goal_extractor`.

- [ ] **Step 1: Write the failing test for the pure grouping**

Create `tests/unit/test_goal_grouping.py`:

```python
"""Goals are grouped by sport from their target disciplines, not hardcoded."""

from __future__ import annotations

from app.domain.enums import Discipline
from app.services.training_catalog.grouping import (
    GoalOption,
    GoalSport,
    group_goals_by_sport,
)


def _option(code: str, *disciplines: Discipline) -> GoalOption:
    return GoalOption(
        code=code, display_name=code.title(), disciplines=frozenset(disciplines)
    )


def test_a_single_discipline_goal_groups_under_that_sport() -> None:
    grouped = group_goals_by_sport(
        (
            _option("MARATHON", Discipline.RUNNING),
            _option("MTB_RACE", Discipline.CYCLING),
            _option("OPEN_WATER_SWIM", Discipline.SWIMMING),
        )
    )

    assert [item.code for item in grouped[GoalSport.RUNNING]] == ["MARATHON"]
    assert [item.code for item in grouped[GoalSport.CYCLING]] == ["MTB_RACE"]
    assert [item.code for item in grouped[GoalSport.SWIMMING]] == ["OPEN_WATER_SWIM"]
    assert GoalSport.TRIATHLON not in grouped


def test_a_multi_discipline_goal_groups_under_triathlon() -> None:
    grouped = group_goals_by_sport(
        (
            _option(
                "TRIATHLON_SPRINT",
                Discipline.SWIMMING,
                Discipline.CYCLING,
                Discipline.RUNNING,
            ),
        )
    )

    assert [item.code for item in grouped[GoalSport.TRIATHLON]] == ["TRIATHLON_SPRINT"]
    assert GoalSport.RUNNING not in grouped


def test_grouping_is_stable_and_alphabetical_within_a_sport() -> None:
    grouped = group_goals_by_sport(
        (
            _option("MARATHON", Discipline.RUNNING),
            _option("HALF_MARATHON", Discipline.RUNNING),
        )
    )

    assert [item.code for item in grouped[GoalSport.RUNNING]] == [
        "HALF_MARATHON",
        "MARATHON",
    ]
```

- [ ] **Step 2: Run it and confirm it fails**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest tests/unit/test_goal_grouping.py -v"
```
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the pure grouping module**

Create `app/services/training_catalog/grouping.py`:

```python
"""Group primary goals into the menu the athlete picks from.

The grouping is derived from each goal's target disciplines rather than
hardcoded, so adding a goal stays a change to the seed alone.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.domain.enums import Discipline


class GoalSport(StrEnum):
    """The first level of the goal menu."""

    RUNNING = "RUNNING"
    CYCLING = "CYCLING"
    SWIMMING = "SWIMMING"
    TRIATHLON = "TRIATHLON"


@dataclass(frozen=True, slots=True)
class GoalOption:
    """One selectable primary goal and the disciplines it targets."""

    code: str
    display_name: str
    disciplines: frozenset[Discipline]


_SPORT_BY_DISCIPLINE = {
    Discipline.RUNNING: GoalSport.RUNNING,
    Discipline.CYCLING: GoalSport.CYCLING,
    Discipline.SWIMMING: GoalSport.SWIMMING,
}


def group_goals_by_sport(
    goals: Sequence[GoalOption],
) -> dict[GoalSport, tuple[GoalOption, ...]]:
    """Bucket goals by sport; anything spanning several is a triathlon goal."""

    buckets: dict[GoalSport, list[GoalOption]] = defaultdict(list)
    for goal in goals:
        if len(goal.disciplines) > 1:
            buckets[GoalSport.TRIATHLON].append(goal)
            continue
        for discipline in goal.disciplines:
            sport = _SPORT_BY_DISCIPLINE.get(discipline)
            if sport is not None:
                buckets[sport].append(goal)
    return {
        sport: tuple(sorted(options, key=lambda option: option.code))
        for sport, options in buckets.items()
    }
```

- [ ] **Step 4: Run the grouping tests and confirm they pass**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest tests/unit/test_goal_grouping.py -v"
```
Expected: all three PASS.

- [ ] **Step 5: Commit the pure part before touching the flow**

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && ruff check . && ruff format --check . && mypy app"
git add app/services/training_catalog/grouping.py tests/unit/test_goal_grouping.py
git commit -m "Add pure goal grouping for the selection menu

Groups by target discipline rather than a hardcoded list, so adding a goal
stays a seed change."
```

- [ ] **Step 6: Add the repository read**

In `app/repositories/training_catalog.py`, add a method returning active primary
goals with their target disciplines. Follow the existing query style in that file:

```python
Return plain data, not a service-layer dataclass. Repositories in this codebase do
not import from `app/services/`, and `GoalOption` lives there. The caller converts.

```python
    async def active_primary_goal_target_disciplines(
        self,
    ) -> tuple[tuple[str, str, frozenset[Discipline]], ...]:
        """Active primary goals as (code, display_name, target disciplines)."""

        rows = await self._session.execute(
            select(
                GoalTemplate.code,
                GoalTemplate.display_name,
                TrainingContext.discipline,
            )
            .join(
                GoalTemplateContext,
                GoalTemplateContext.goal_template_id == GoalTemplate.id,
            )
            .join(
                TrainingContext,
                TrainingContext.id == GoalTemplateContext.training_context_id,
            )
            .where(
                GoalTemplate.kind == GoalTemplateKind.PRIMARY,
                GoalTemplate.status == CatalogItemStatus.ACTIVE,
                GoalTemplateContext.role == GoalContextRole.TARGET,
            )
        )
        by_code: dict[str, tuple[str, set[Discipline]]] = {}
        for code, display_name, discipline in rows:
            entry = by_code.setdefault(code, (display_name, set()))
            entry[1].add(discipline)
        return tuple(
            (code, display_name, frozenset(disciplines))
            for code, (display_name, disciplines) in sorted(by_code.items())
        )
```

Add the imports it needs at the top of that file. The onboarding service turns these
rows into `GoalOption` before calling `group_goals_by_sport`.

- [ ] **Step 7: Add the keyboards**

In `app/bot/keyboards.py`, add labels to `LABELS`:

```python
    "goal_sport_running": "Running",
    "goal_sport_cycling": "Cycling",
    "goal_sport_swimming": "Swimming",
    "goal_sport_triathlon": "Triathlon",
    "goal_back": "Back",
    "support_none": "No supporting goal",
```

Then replace `goal_input_keyboard` and add two more:

```python
def goal_sport_keyboard(sports: Sequence[str]) -> InlineKeyboardMarkup:
    """First level: which sport is the goal in.

    Takes plain sport values rather than a service-layer enum, so this module
    keeps importing nothing but telegram and app.bot.rendering.
    """

    label_by_sport = {
        "RUNNING": LABELS["goal_sport_running"],
        "CYCLING": LABELS["goal_sport_cycling"],
        "SWIMMING": LABELS["goal_sport_swimming"],
        "TRIATHLON": LABELS["goal_sport_triathlon"],
    }
    rows = [
        [(label_by_sport[sport], f"ob:v1:goal:sport:{sport}")] for sport in sports
    ]
    rows.append([(LABELS["cancel"], "ob:v1:cancel")])
    return _rows(rows)


def goal_template_keyboard(
    options: Sequence[tuple[str, str]],
) -> InlineKeyboardMarkup:
    """Second level: which goal within that sport. Takes (code, name) pairs."""

    rows = [
        [(display_name, f"ob:v1:goal:template:{code}")]
        for code, display_name in options
    ]
    rows.append([(LABELS["goal_back"], "ob:v1:goal:back")])
    rows.append([(LABELS["cancel"], "ob:v1:cancel")])
    return _rows(rows)


def supporting_goal_keyboard(
    options: Sequence[tuple[str, str]],
) -> InlineKeyboardMarkup:
    """Optional supporting goal, offered to every athlete."""

    rows = [[(name, f"ob:v1:support:{code}")] for code, name in options]
    rows.append([(LABELS["support_none"], "ob:v1:support:none")])
    rows.append([(LABELS["cancel"], "ob:v1:cancel")])
    return _rows(rows)
```

Telegram limits callback data to 64 bytes. The longest is
`ob:v1:goal:template:TRIATHLON_FULL_DISTANCE` at 43 bytes, so every code fits.

- [ ] **Step 8: Wire the callbacks**

In `app/bot/service.py`, replace the `ob:v1:goal:choice:` branch at line 442 with:

```python
        if callback_data.startswith("ob:v1:goal:sport:"):
            result = await self._onboarding.choose_goal_sport(
                identity, callback_data.removeprefix("ob:v1:goal:sport:")
            )
            return await self._render_onboarding(identity, result)
        if callback_data == "ob:v1:goal:back":
            result = await self._onboarding.reopen_goal_sports(identity)
            return await self._render_onboarding(identity, result)
        if callback_data.startswith("ob:v1:goal:template:"):
            result = await self._onboarding.choose_goal_template(
                identity, callback_data.removeprefix("ob:v1:goal:template:")
            )
            return await self._render_onboarding(identity, result)
        if callback_data.startswith("ob:v1:support:"):
            raw = callback_data.removeprefix("ob:v1:support:")
            result = await self._onboarding.choose_supporting_goal(
                identity, None if raw == "none" else raw
            )
            return await self._render_onboarding(identity, result)
```

- [ ] **Step 9: Replace the extractor calls in the onboarding service**

Delete the `goal_extractor` constructor parameter and the three `self._goal_extractor`
calls at lines 1750, 3260 and 3392, along with every branch handling clarification,
low confidence or provider failure for the goal step.

**Read `choose_gender` (`app/services/onboarding/service.py:1138-1161`) before
writing these.** They follow its shape exactly: validate, lock, require active, check
the step, save progress, return `self._result(...)`.

The two menu levels both live inside `GOAL_INTAKE`; the chosen sport is UI state in
`answers`. Picking a template advances to `GOAL_CONFIRMED`, where the supporting goal
is offered. No new `OnboardingStep` member is needed, so no migration.

Add near the other answer-key constants:

```python
_GOAL_SPORT_KEY = "goal_sport"
```

Then add the four methods:

```python
    async def choose_goal_sport(
        self,
        identity: TelegramIdentity,
        choice: str,
    ) -> OnboardingServiceResult:
        """Remember which sport the athlete is choosing a goal within."""

        try:
            sport = GoalSport(choice)
        except ValueError as exc:
            raise OnboardingApplicationError("invalid_action") from exc
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.GOAL_INTAKE:
                raise OnboardingApplicationError("stale_action")
            answers = self._answers(onboarding)
            answers[_GOAL_SPORT_KEY] = sport.value
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.GOAL_INTAKE,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

    async def reopen_goal_sports(
        self, identity: TelegramIdentity
    ) -> OnboardingServiceResult:
        """Step back from the goal list to the sport list."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.GOAL_INTAKE:
                raise OnboardingApplicationError("stale_action")
            answers = self._answers(onboarding)
            answers.pop(_GOAL_SPORT_KEY, None)
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.GOAL_INTAKE,
                answers=cast(dict[str, object], answers),
            )
            return self._result(user, onboarding)

    async def choose_goal_template(
        self,
        identity: TelegramIdentity,
        code: str,
    ) -> OnboardingServiceResult:
        """Persist the chosen primary goal, validated against the catalog."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.GOAL_INTAKE:
                raise OnboardingApplicationError("stale_action")
            template = await TrainingCatalogRepository(session).active_goal_by_code(
                code=code
            )
            # Callback data is not trustworthy. An unvalidated code would write
            # a dangling foreign key into training_goals.
            if template is None or template.kind is not GoalTemplateKind.PRIMARY:
                raise OnboardingApplicationError("invalid_action")
            await ProfileRepository(session).upsert_conversational_training_goal(
                user_id=user.id,
                main_goal=template.display_name,
                event_date=None,
                target_outcome=template.display_name,
                secondary_priority=None,
                original_description=template.display_name,
                goal_template_id=template.id,
                supporting_goal_template_id=None,
            )
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.GOAL_EVENT_DATE,
                answers=cast(dict[str, object], self._answers(onboarding)),
            )
            return self._result(user, onboarding)

    async def choose_supporting_goal(
        self,
        identity: TelegramIdentity,
        code: str | None,
    ) -> OnboardingServiceResult:
        """Attach an optional supporting goal, then leave the goal steps."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.GOAL_CONFIRMED:
                raise OnboardingApplicationError("stale_action")
            profiles = ProfileRepository(session)
            goal = await profiles.get_training_goal(user_id=user.id)
            if goal is None:
                raise OnboardingApplicationError("stale_action")
            supporting_id: uuid.UUID | None = None
            if code is not None:
                template = await TrainingCatalogRepository(
                    session
                ).active_goal_by_code(code=code)
                if (
                    template is None
                    or template.kind is not GoalTemplateKind.SUPPORTING
                ):
                    raise OnboardingApplicationError("invalid_action")
                supporting_id = template.id
            await profiles.upsert_conversational_training_goal(
                user_id=user.id,
                main_goal=goal.main_goal,
                event_date=goal.event_date,
                target_outcome=goal.target_outcome,
                secondary_priority=goal.secondary_priority,
                original_description=goal.original_description,
                goal_template_id=goal.goal_template_id,
                supporting_goal_template_id=supporting_id,
            )
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.PROFILE_BIRTH_YEAR_INTAKE,
                answers=cast(dict[str, object], self._answers(onboarding)),
            )
            return self._result(user, onboarding)
```

**Two things to check before writing these.**

`TrainingCatalogRepository.active_goal_by_code` may not exist. Only `active_goal_by_id`
was confirmed. If it is missing, add it alongside, filtering on
`GoalTemplate.code == code` and `status == CatalogItemStatus.ACTIVE`.

`upsert_conversational_training_goal` (`app/repositories/profiles.py:72`) is no longer
conversational once this task lands. Rename it to `upsert_training_goal` and update
its call sites in the same commit, or the name will mislead every later reader.

- [ ] **Step 9b: Render the right keyboard for each state**

`GOAL_INTAKE` now has two screens. Wherever the renderer picks a keyboard for that
step, branch on whether `_GOAL_SPORT_KEY` is present in `answers`:

- absent: show `goal_sport_keyboard`, built from `group_goals_by_sport(...).keys()`
- present: show `goal_template_keyboard`, built from that sport's options

`GOAL_CONFIRMED` shows `supporting_goal_keyboard`, built from the active supporting
goals in the catalog.

Add the prompts to `app/bot/messages.py`: one asking which sport, one asking which
goal, one asking whether they want a supporting goal.

- [ ] **Step 10: Delete the extractor**

```bash
git rm -r app/workflows/onboarding_goal
git rm app/schemas/onboarding_goal.py
git rm tests/unit/test_onboarding_goal_graph.py
git rm tests/use_cases/test_conversational_goal_onboarding.py
git rm tests/integration/test_live_agent_onboarding.py
```

Remove the `create_goal_extractor` import and construction in `app/bot/main.py` at
lines 30, 72 and 89. Remove `ai_workflow_name` from `app/config.py:43` and its entry
in `exposed_configuration` if it has one; nothing uses it once the extractor is gone.

- [ ] **Step 11: Rewrite the journey test**

`tests/scenarios/test_bot_journey.py` walks the whole onboarding. Replace the
free-text goal step with the two callbacks, and assert the goal was stored:

```python
    await service.handle_callback(identity, "ob:v1:goal:sport:TRIATHLON")
    await service.handle_callback(
        identity, "ob:v1:goal:template:TRIATHLON_HALF_DISTANCE"
    )
    # Task 5b inserts the race date step between the template and the support.
    # Until that task lands, drop this line and go straight to the support.
    await service.handle_text(identity, "2027-07-11")
    await service.handle_callback(identity, "ob:v1:support:STRENGTH_MAINTENANCE")

    async with factory() as session:
        goal = await session.scalar(select(TrainingGoal))
    assert goal is not None
    assert goal.goal_template_id is not None
    assert goal.supporting_goal_template_id is not None
```

Add a second walk through with `ob:v1:goal:sport:RUNNING`,
`ob:v1:goal:template:MARATHON`, `ob:v1:goal:nodate` and `ob:v1:support:none`, so both
the single-sport athlete required by spec section 1.1 of the planning design and the
athlete with no race are covered.

- [ ] **Step 12: Run everything**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest -q && mypy app"
```
Expected: PASS.

- [ ] **Step 13: Confirm no model call is left outside the planner**

Run:
```bash
grep -rn "ainvoke_structured\|ainvoke\|StructuredOnboardingModel" app/ | grep -v integrations/llm
```
Expected: hits only in `app/services/weekly_planning/service.py`. Anything else means
a model call survived.

- [ ] **Step 14: Lint and commit**

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && ruff check . && ruff format --check . && mypy app"
git add -A
git commit -m "Replace free-text goal extraction with a two-level menu

Pick a sport, then a goal within it, then an optional supporting goal. The
menu is built from the catalog at runtime, so adding a goal stays a seed
change. The weekly planner is now the only model call in the product."
```

---

### Task 5b: A race date step

**This closes a regression Task 5 opens.** Today the athlete's race date is pulled out
of their free text by the model. The real athlete has `event_date = 2027-07-11` in the
database, and it got there that way. Delete the extractor without replacing this and
`event_date` is null for everyone forever, which means every athlete lands in the
`GENERAL` phase under section 4.10 of the planning design. The whole periodisation
idea quietly stops working.

**Files:**
- Modify: `app/domain/enums.py`
- Create: `alembic/versions/0031_add_goal_event_date_step.py`
- Modify: `app/services/onboarding/service.py`, `app/bot/service.py`, `app/bot/keyboards.py`, `app/bot/messages.py`
- Modify: `tests/scenarios/test_bot_journey.py`

**Interfaces:**
- Consumes: `choose_goal_template` from Task 5, which advances to `GOAL_EVENT_DATE`.
- Produces: `OnboardingStep.GOAL_EVENT_DATE`. A callback `ob:v1:goal:nodate`. A text
  handler for that step accepting `YYYY-MM-DD` and `DD/MM/YYYY`. Both routes advance
  to `GOAL_CONFIRMED`.

- [ ] **Step 1: Write the failing test**

Append to `tests/scenarios/test_bot_journey.py`:

```python
@pytest.mark.asyncio
async def test_a_race_date_can_be_entered_and_skipped(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    """Without this the planner has no weeks-to-race and no phase."""

    from datetime import date

    service, identity, factory = await _onboard_to_goal_chosen(database)

    await service.handle_callback(identity, "ob:v1:goal:template:MARATHON")
    await service.handle_text(identity, "2027-07-11")

    async with factory() as session:
        goal = await session.scalar(select(TrainingGoal))
    assert goal is not None
    assert goal.event_date == date(2027, 7, 11)


@pytest.mark.asyncio
async def test_an_unparseable_race_date_is_rejected_without_advancing(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    service, identity, factory = await _onboard_to_goal_chosen(database)
    await service.handle_callback(identity, "ob:v1:goal:template:MARATHON")

    await service.handle_text(identity, "sometime next summer")

    async with factory() as session:
        onboarding = await session.scalar(select(OnboardingSession))
    assert onboarding is not None
    assert onboarding.current_step is OnboardingStep.GOAL_EVENT_DATE
```

`_onboard_to_goal_chosen` is a helper you write in that file, walking consent through
to the sport choice. Reuse whatever the existing journey test already does for the
earlier steps rather than writing it twice.

- [ ] **Step 2: Run and confirm it fails**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest tests/scenarios/test_bot_journey.py -k race_date -v"
```
Expected: FAIL with `AttributeError: GOAL_EVENT_DATE`.

- [ ] **Step 3: Add the enum member**

In `app/domain/enums.py`, add to `OnboardingStep` between `GOAL_INTAKE` and
`GOAL_CONFIRMED`:

```python
    GOAL_EVENT_DATE = "GOAL_EVENT_DATE"
```

- [ ] **Step 4: Write the migration**

`current_step` is a persisted enum (`app/db/models.py:251-253`,
`persisted_enum(OnboardingStep, name="onboarding_step", length=32)`), so the database
constrains the allowed values and a new member needs a migration.

Find the head first:

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && alembic heads"
```

Then look at how `persisted_enum` is implemented:

```bash
grep -rn "def persisted_enum" -A 20 app/db/
```

If it produces a native PostgreSQL enum type, the migration is
`ALTER TYPE onboarding_step ADD VALUE 'GOAL_EVENT_DATE'`, which **cannot run inside a
transaction**, so the migration needs `op.execute(sa.text(...))` with
`autocommit_block()`. If it produces a `VARCHAR` plus a check constraint, drop and
recreate the constraint with the new value included. Write whichever one matches; do
not guess.

- [ ] **Step 5: Add the skip button and the prompt**

In `app/bot/keyboards.py` add `"goal_no_date": "No date yet"` to `LABELS` and:

```python
def goal_event_date_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["goal_no_date"], "ob:v1:goal:nodate")],
            [(LABELS["cancel"], "ob:v1:cancel")],
        ]
    )
```

In `app/bot/messages.py` add a prompt asking for the race date and stating both
accepted formats.

- [ ] **Step 6: Handle both routes**

In `app/services/onboarding/service.py`, add a module constant and two methods:

```python
_EVENT_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y")


def _parse_event_date(text: str) -> date | None:
    """Parse a race date deterministically. No model, no fuzzy matching."""

    cleaned = text.strip()
    for pattern in _EVENT_DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    return None
```

```python
    async def submit_event_date(
        self,
        identity: TelegramIdentity,
        text: str,
    ) -> OnboardingServiceResult:
        """Store a typed race date, or reject it without advancing."""

        parsed = _parse_event_date(text)
        if parsed is None:
            raise OnboardingApplicationError("invalid_action")
        return await self._store_event_date(identity, parsed)

    async def skip_event_date(
        self, identity: TelegramIdentity
    ) -> OnboardingServiceResult:
        """An athlete with no event still gets a plan, in the GENERAL phase."""

        return await self._store_event_date(identity, None)

    async def _store_event_date(
        self,
        identity: TelegramIdentity,
        event_date: date | None,
    ) -> OnboardingServiceResult:
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.GOAL_EVENT_DATE:
                raise OnboardingApplicationError("stale_action")
            profiles = ProfileRepository(session)
            goal = await profiles.get_training_goal(user_id=user.id)
            if goal is None:
                raise OnboardingApplicationError("stale_action")
            await profiles.upsert_conversational_training_goal(
                user_id=user.id,
                main_goal=goal.main_goal,
                event_date=event_date,
                target_outcome=goal.target_outcome,
                secondary_priority=goal.secondary_priority,
                original_description=goal.original_description,
                goal_template_id=goal.goal_template_id,
                supporting_goal_template_id=goal.supporting_goal_template_id,
            )
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.GOAL_CONFIRMED,
                answers=cast(dict[str, object], self._answers(onboarding)),
            )
            return self._result(user, onboarding)
```

Reject a date in the past the same way as an unparseable one; a race that has already
happened is not a goal.

In `app/bot/service.py` add the callback branch:

```python
        if callback_data == "ob:v1:goal:nodate":
            result = await self._onboarding.skip_event_date(identity)
            return await self._render_onboarding(identity, result)
```

and route text on the `GOAL_EVENT_DATE` step to `submit_event_date`, following how the
other text-intake steps are dispatched in `handle_text`.

- [ ] **Step 7: Run everything**

Run:
```bash
cd .. && docker compose up -d db && sleep 5 && cd backend
docker run --rm --network adaptive_training_coach_default \
  -e DATABASE_URL="postgresql+asyncpg://coach:coach@db:5432/adaptive_coach" \
  -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && alembic upgrade head && python -m pytest -q && mypy app"
```
Expected: PASS.

- [ ] **Step 8: Lint and commit**

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && ruff check . && ruff format --check . && mypy app"
git add -A
git commit -m "Ask for the race date instead of extracting it from free text

The date was previously pulled out of the athlete's words by the model.
Without a replacement, event_date would be null for everyone and every athlete
would sit in the GENERAL training phase forever. Two accepted formats, a skip
button, and past dates rejected."
```

---

### Task 6: Supporting goals reach the planner

Today `_primary_target_contexts` reads only the primary goal's TARGET contexts, so a
supporting goal never becomes a discipline the coach plans for. Meanwhile
`_goal_disciplines` in the baseline service does include supporting goals, so
baselines get built for disciplines no plan will mention. Choosing strength
maintenance produces fitness numbers and no strength sessions.

**Files:**
- Modify: `app/services/weekly_planning/service.py:424-443`
- Modify: `app/workflows/prompts/weekly_planning.py`
- Modify test: `tests/use_cases/test_weekly_planning.py`

**Interfaces:**
- Consumes: `GoalContextRole` and `GoalTemplateKind`, already imported there.
- Produces: `_TargetContext` gains `role: GoalContextRole`. `_primary_target_contexts` is renamed `_planned_contexts` and returns TARGET contexts from the primary goal plus SUPPORTING contexts from the supporting goal. `prompt_context["goal"]["target_contexts"]` entries gain a `"role"` key.

- [ ] **Step 1: Write the failing test**

Append to `tests/use_cases/test_weekly_planning.py`:

```python
@pytest.mark.asyncio
async def test_a_supporting_goal_is_planned_alongside_the_primary_one(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strength chosen as a support must appear in the planned disciplines."""

    _, factory = database
    monkeypatch.setattr("app.services.weekly_planning.service.utc_now", lambda: NOW)
    async with factory.begin() as session:
        athlete_id = await _seed_target_goal(
            session, supporting_discipline=Discipline.STRENGTH
        )
        for days_ago in (1, 3, 5):
            await _add_running(
                session, athlete_id=athlete_id, started_at=NOW - timedelta(days=days_ago)
            )

    result = await _service(factory).generate_next_week(_identity())

    assert result.kind == "created"
    assert result.readiness is not None
    planned = {row.discipline for row in result.readiness.disciplines}
    assert Discipline.RUNNING in planned
    assert Discipline.STRENGTH in planned
```

Note that `_seed_target_goal` already accepts `supporting_discipline`, but it attaches
the supporting context to the **primary** template. Change that helper so a supporting
discipline creates a separate SUPPORTING-kind template and sets
`TrainingGoal.supporting_goal_template_id`, matching how the real onboarding writes it
after Task 5.

- [ ] **Step 2: Run it and confirm it fails**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest tests/use_cases/test_weekly_planning.py -k supporting_goal -v"
```
Expected: FAIL. `Discipline.STRENGTH` is absent, because only the primary goal's
TARGET contexts are read.

- [ ] **Step 3: Return supporting contexts too**

In `app/services/weekly_planning/service.py`, add `role` to the dataclass:

```python
@dataclass(frozen=True, slots=True)
class _TargetContext:
    code: str
    display_name: str
    discipline: Discipline
    role: GoalContextRole
```

Replace `_primary_target_contexts` with:

```python
async def _planned_contexts(
    *,
    catalog: TrainingCatalogRepository,
    goal_template_id: uuid.UUID | None,
    supporting_goal_template_id: uuid.UUID | None,
) -> tuple[_TargetContext, ...]:
    """Disciplines the coach plans: the primary target plus any support.

    A supporting goal previously reached the baseline service but never the
    planner, so an athlete could hold strength maintenance, accumulate fitness
    numbers for it, and never receive a strength session.
    """

    expected_role_by_goal_id: dict[uuid.UUID, GoalContextRole] = {}
    if goal_template_id is not None:
        primary = await catalog.active_goal_by_id(goal_template_id=goal_template_id)
        if primary is not None and primary.kind is GoalTemplateKind.PRIMARY:
            expected_role_by_goal_id[primary.id] = GoalContextRole.TARGET
    if supporting_goal_template_id is not None:
        supporting = await catalog.active_goal_by_id(
            goal_template_id=supporting_goal_template_id
        )
        if supporting is not None and supporting.kind is GoalTemplateKind.SUPPORTING:
            expected_role_by_goal_id[supporting.id] = GoalContextRole.SUPPORTING
    if not expected_role_by_goal_id:
        return ()

    rows = await catalog.contexts_for_goals(
        goal_template_ids=expected_role_by_goal_id.keys()
    )
    return tuple(
        _TargetContext(
            code=context.code,
            display_name=context.display_name,
            discipline=context.discipline,
            role=relation.role,
        )
        for relation, context in rows
        if expected_role_by_goal_id.get(relation.goal_template_id) is relation.role
    )
```

This mirrors `_goal_disciplines` in `app/services/fitness/service.py:180-225`, which
is deliberate: the two now agree on which disciplines matter.

- [ ] **Step 4: Update the call site**

In `_prepare`, replace the call:

```python
            target_contexts = await _planned_contexts(
                catalog=TrainingCatalogRepository(session),
                goal_template_id=(goal.goal_template_id if goal is not None else None),
                supporting_goal_template_id=(
                    goal.supporting_goal_template_id if goal is not None else None
                ),
            )
```

and add the role to each entry in `prompt_context`:

```python
                    "target_contexts": [
                        {
                            "code": item.code,
                            "display_name": item.display_name,
                            "discipline": item.discipline.value,
                            "role": item.role.value,
                        }
                        for item in target_contexts
                    ],
```

- [ ] **Step 5: Tell the prompt what a support is**

In `app/workflows/prompts/weekly_planning.py`, bump the version and append to the
system prompt:

```python
WEEKLY_PLANNER_PROMPT_VERSION: Final = 3
```

```
Each entry in target_contexts carries a role. TARGET means the discipline the
athlete's event is in; it gets the bulk of the week. SUPPORTING means a discipline
that exists to support the target, such as strength work to maintain muscle. Give a
supporting discipline one or two short sessions and never let it displace target
training.
```

If you are running this plan before the planner plan, the version here is 2 rather
than 3. Check the current value first and increment it.

- [ ] **Step 6: Run everything**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest -q && mypy app"
```
Expected: PASS.

- [ ] **Step 7: Lint and commit**

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && ruff check . && ruff format --check . && mypy app"
git add -A
git commit -m "Plan supporting disciplines, not only target ones

A supporting goal reached the baseline service but never the planner, so an
athlete could choose strength maintenance, accumulate fitness numbers for it,
and never receive a strength session. The planner and the baseline service now
agree on which disciplines matter."
```

---

## Final verification

- [ ] **The planner is the only model call**

```bash
grep -rn "ainvoke_structured\|StructuredOnboardingModel" app/ | grep -v integrations/llm
```
Expected: `app/services/weekly_planning/service.py` and nothing else.

- [ ] **No workflow packages remain except the planner prompt**

```bash
find app/workflows -type d
```
Expected: `app/workflows` and `app/workflows/prompts` only.

- [ ] **Everything passes**

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest -q && ruff check . && \
   ruff format --check . && mypy app"
```

- [ ] **Walk the real bot**

Run `/dev_reset` then `/start` in Telegram and complete onboarding through the menu.
Confirm the goal and the supporting goal are stored, and that no `llm_usage` row is
written during onboarding:

```bash
docker compose exec -T db psql -U coach -d adaptive_coach -c \
  "SELECT feature, status, created_at FROM llm_usage ORDER BY created_at DESC LIMIT 5;"
```

---

## Deliberately out of scope

| Not doing | Why |
|---|---|
| Touching the `Discipline` enum or the workout check constraint | Spec 3.2. Ingestion must keep accepting everything the watch records |
| Deleting `DeterministicFakeOnboardingModel` | The planner still needs a fake provider in tests |
| The onboarding step machine | Consent, profile intake, equipment and history import are already deterministic |
| Anything in `2026-08-28-adaptive-planning-design.md` | Different files, different plan. Task 6 is the only overlap and it is flagged |
