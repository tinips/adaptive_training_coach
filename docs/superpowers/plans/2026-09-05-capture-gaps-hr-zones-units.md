# Close Capture Gaps + Age-Based HR Zones + Unit Formatting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture the actual-workout metrics currently missing (cycling power; pace/power/speed/cadence from screenshots), add age-based heart-rate zones for verification/display only (never prescribed), add a read-only "view my zones" command, and add the single min:sec pace-formatting helper used everywhere pace is shown to an athlete.

**Architecture:** Additive changes across four existing layers (DB/migration, screenshot extraction contract + prompt, first-week zone resolver, bot command surface) plus two new small, single-responsibility modules (`app/services/athlete_zones.py` for display-only zone composition, `app/services/formatting.py` for the pace renderer). Nothing in this plan touches `FirstWeekPlanPrescription`, `WeeklyPlanPrescription`, or any planner prompt-building code — the first-week planner's own zone resolver (`resolve_first_week_zones`) is modified only to close a pre-existing gap (see Global Constraints), never extended with new capability.

**Tech Stack:** Python, FastAPI/async SQLAlchemy, Alembic, Pydantic v2, pytest + pytest-asyncio, SQLite in-memory for unit/use-case tests, real Postgres for the migration itself.

**Spec:** The brief pasted into this session ("Coding-Agent Brief A: Close capture gaps + age-based HR zones + unit formatting"). This plan implements all six of its parts, plus one additional fix the exploration phase surfaced and the user explicitly approved: closing a pre-existing gap where the first-week planner could already select `HEART_RATE_BPM` as a prescribable zone via observed workout HR (unrelated to this brief's age-based zones, but it directly undermines the "HR is never prescribed" guarantee this brief asks for).

## Global Constraints

- First-week planner **prescription** behavior must not change: still power + pace, RPE-fallback where no data, no HR. `FirstWeekPlanPrescription`, `WeeklyPlanPrescription`, and the prompt-building functions in `app/workflows/prompts/weekly_planning.py` are never touched by this plan.
- Age-based HR zones are a **new, separate type** (`ReferenceHeartRateZones` in the new `app/services/athlete_zones.py`), never `ResolvedIntensityZones`, and never returned from `resolve_first_week_zones()` or merged into `prompt_context`. This is a structural guarantee (different Python type, different module), not just a naming convention.
- Age = `current_year - birth_year` (whole years only; only birth year is stored, not a full date of birth). This is documented as an additional approximation layered on top of Tanaka's own stated ±10-12 bpm uncertainty.
- Pace/speed derivation from distance+duration already exists (`pace_seconds_per_unit()` / `speed_kph()` in `app/schemas/workouts.py`, wired into every `*WorkoutDetailsData` model's `model_validator`) and already overwrites any directly-extracted value whenever distance+duration are both present, for every import source. Per the user's explicit choice, this plan does **not** add a "derived vs. directly-extracted" provenance marker and does **not** change this shared, cross-source behavior — it only extends the screenshot extractor to capture pace/power/speed/cadence *when visible*, which matters for the case where a screen shows pace but not distance (a treadmill with no GPS, for example).
- Existing pinning test `test_stationary_cycling_has_no_indoor_or_power_fields` (`backend/tests/use_cases/test_workout_creation.py:234`) currently asserts that no power column exists on `CyclingWorkoutDetails`. Task 1 deliberately invalidates that assertion and updates the test in the same task — this is an intended schema change, not a regression.
- `resolve_first_week_zones()` / `_resolve_discipline()` (`app/services/weekly_planning/zones.py`) is used **only** by the FIRST_WEEK planner path (single caller: `service.py:992`, inside `_prepare()`'s `FIRST_WEEK`-only branch). It has no other callers, so removing its observed-HR fallback in Task 5 cannot affect the ongoing/ONGOING planner, which has no zone-resolution step at all.
- Work happens directly on `main` (no feature branch), per this repo's standing instruction.
- Next migration number is `0051` (last is `0050_weekly_plan_outcomes`).

---

## File Structure

| File | Responsibility |
|---|---|
| `alembic/versions/0051_cycling_power.py` (new) | Migration: add nullable `average_power_watts`/`max_power_watts` to `cycling_workout_details`. |
| `app/db/models.py` | `CyclingWorkoutDetails`: add the two power columns + check constraints. |
| `app/schemas/workouts.py` | `CyclingWorkoutDetailsData`: add the two power fields. |
| `app/services/activities/contracts.py` | `ActivityImportData`: add the two power fields. |
| `app/repositories/workout_detail_mapper.py` | `details_for_import()`: pass power fields through for CYCLING. |
| `app/schemas/manual_import.py` | `ManualWorkoutImportRequest`: add pace/power/speed/cadence fields. |
| `app/integrations/llm/vision.py` | `_EXTRACTION_PROMPT`: instruct extraction of the new fields when visible. |
| `app/services/activities/adapters/manual_screenshot.py` | `from_manual_screenshot()`: map new fields into `ActivityImportData`. |
| `app/services/weekly_planning/zones.py` | Remove the observed-HR prescription fallback; rename `_power_zones`/`_running_pace_zones`/`_swim_pace_zones`/`_heart_rate_zones` to public, reusable names. |
| `app/services/athlete_zones.py` (new) | Age-based reference HR zones (Tanaka) + composition of HR/pace/power for the "view my zones" command. Display-only; never imported by planning code. |
| `app/services/formatting.py` (new) | The one seconds→min:sec pace formatter used everywhere. |
| `app/services/accounts/service.py` | `AccountQueryService.zones()`: read-model method mirroring `profile()`. |
| `app/bot/messages.py` | `zones_view()`: renders the "view my zones" text. |
| `app/bot/service.py` | `CoachBotApplicationService.zones()`: dispatch method mirroring `profile()`. |
| `app/bot/handlers.py` | `zones_handler`. |
| `app/bot/router.py` | Register `CommandHandler("zones", zones_handler)`. |

---

### Task 1: Add power fields to cycling actuals (migration + model + schema + contract + mapper)

**Files:**
- Create: `backend/alembic/versions/0051_cycling_power.py`
- Modify: `backend/app/db/models.py` (`CyclingWorkoutDetails`, around lines 922-1007)
- Modify: `backend/app/schemas/workouts.py` (`CyclingWorkoutDetailsData`, lines 96-118)
- Modify: `backend/app/services/activities/contracts.py` (`ActivityImportData`, around line 61)
- Modify: `backend/app/repositories/workout_detail_mapper.py` (`details_for_import()`, CYCLING branch)
- Modify: `backend/tests/use_cases/test_workout_creation.py` (fix the stale pinning test + add a power-persistence test)

**Interfaces:**
- Produces: `CyclingWorkoutDetails.average_power_watts: float | None`, `CyclingWorkoutDetails.max_power_watts: float | None`; `CyclingWorkoutDetailsData.average_power_watts: float | None`, `CyclingWorkoutDetailsData.max_power_watts: float | None`; `ActivityImportData.average_power_watts: float | None`, `ActivityImportData.max_power_watts: float | None`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/use_cases/test_workout_creation.py`:

```python
@pytest.mark.asyncio
async def test_cycling_workout_persists_power_when_provided(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    async with factory.begin() as session:
        user_id = await create_user(session, telegram_user_id=22_002)
        workout = await TrainingActivityRepository(session).create_manual(
            WorkoutCreate(
                athlete_id=user_id,
                discipline=Discipline.CYCLING,
                started_at=NOW,
                duration_seconds=3600,
                source=ActivitySource.MANUAL,
                details=CyclingWorkoutDetailsData(
                    cycling_type=CyclingType.STATIONARY,
                    distance_meters=30_000,
                    moving_duration_seconds=3600,
                    average_power_watts=185,
                    max_power_watts=240,
                ),
            )
        )

    details = workout.cycling_details
    assert details is not None
    assert details.average_power_watts == 185
    assert details.max_power_watts == 240
```

Also update the existing stale test in the same file (it currently pins the *absence* of power columns, which this task deliberately changes):

```python
@pytest.mark.asyncio
async def test_stationary_cycling_has_no_indoor_field(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    async with factory.begin() as session:
        user_id = await create_user(session, telegram_user_id=22_001)
        workout = await TrainingActivityRepository(session).create_manual(
            WorkoutCreate(
                athlete_id=user_id,
                discipline=Discipline.CYCLING,
                started_at=NOW,
                duration_seconds=3600,
                source=ActivitySource.MANUAL,
                details=CyclingWorkoutDetailsData(
                    cycling_type=CyclingType.STATIONARY,
                    distance_meters=30_000,
                    moving_duration_seconds=3600,
                ),
            )
        )

    details = workout.cycling_details
    assert details is not None
    assert details.cycling_type is CyclingType.STATIONARY
    assert details.average_speed_kph == 30
    assert not hasattr(details, "is_indoor")
    assert details.average_power_watts is None
    assert details.max_power_watts is None
```

(This replaces the old `test_stationary_cycling_has_no_indoor_or_power_fields` function entirely — same name change, same body replaced.)

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`, inside the dev container — see Task 10 for the exact docker invocation):
`python -m pytest tests/use_cases/test_workout_creation.py -k "power or no_indoor" -v`

Expected: FAIL — `test_cycling_workout_persists_power_when_provided` fails with `TypeError: CyclingWorkoutDetailsData() got unexpected keyword argument 'average_power_watts'` (field doesn't exist yet); the renamed `test_stationary_cycling_has_no_indoor_field` fails the same way for the same reason.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/db/models.py`, inside `CyclingWorkoutDetails.__table_args__` (the tuple of `CheckConstraint`s, right after the existing `max_cadence_nonnegative` constraint):

```python
        CheckConstraint(
            "average_power_watts IS NULL OR average_power_watts >= 0",
            name="average_power_nonnegative",
        ),
        CheckConstraint(
            "max_power_watts IS NULL OR max_power_watts >= 0",
            name="max_power_nonnegative",
        ),
```

And add the two columns right after `max_cadence_rpm` (before the `workout: Mapped[Workout] = relationship(...)` line):

```python
    average_power_watts: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_power_watts: Mapped[float | None] = mapped_column(Float, nullable=True)
```

In `backend/app/schemas/workouts.py`, in `CyclingWorkoutDetailsData`, right after `max_cadence_rpm: float | None = Field(default=None, ge=0)`:

```python
    average_power_watts: float | None = Field(default=None, ge=0)
    max_power_watts: float | None = Field(default=None, ge=0)
```

In `backend/app/services/activities/contracts.py`, in `ActivityImportData`, right after `max_speed_kph: float | None = None`:

```python
    average_power_watts: float | None = None
    max_power_watts: float | None = None
```

In `backend/app/repositories/workout_detail_mapper.py`, in the `if incoming.discipline is Discipline.CYCLING:` branch of `details_for_import()`:

```python
    if incoming.discipline is Discipline.CYCLING:
        return CyclingWorkoutDetailsData(
            cycling_type=incoming.cycling_type or CyclingType.OTHER,
            average_speed_kph=incoming.average_speed_kph,
            max_speed_kph=incoming.max_speed_kph,
            average_power_watts=incoming.average_power_watts,
            max_power_watts=incoming.max_power_watts,
            elevation_gain_meters=incoming.elevation_gain_meters,
            elevation_loss_meters=incoming.elevation_loss_meters,
            average_cadence_rpm=incoming.average_cadence,
            max_cadence_rpm=incoming.max_cadence,
            **common,
        )
```

Create `backend/alembic/versions/0051_cycling_power.py`:

```python
"""Add cycling power fields for smart-trainer/static-bike actuals.

Revision ID: 0051_cycling_power
Revises: 0050_weekly_plan_outcomes
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0051_cycling_power"
down_revision: str | None = "0050_weekly_plan_outcomes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("cycling_workout_details") as batch:
        batch.add_column(sa.Column("average_power_watts", sa.Float(), nullable=True))
        batch.add_column(sa.Column("max_power_watts", sa.Float(), nullable=True))
        batch.create_check_constraint(
            "average_power_nonnegative",
            "average_power_watts IS NULL OR average_power_watts >= 0",
        )
        batch.create_check_constraint(
            "max_power_nonnegative",
            "max_power_watts IS NULL OR max_power_watts >= 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("cycling_workout_details") as batch:
        batch.drop_constraint("max_power_nonnegative", type_="check")
        batch.drop_constraint("average_power_nonnegative", type_="check")
        batch.drop_column("max_power_watts")
        batch.drop_column("average_power_watts")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/use_cases/test_workout_creation.py -v`
Expected: PASS, full file, no regressions.

- [ ] **Step 5: Apply the migration against the real Postgres DB and verify existing rows get null power**

```bash
docker compose up -d db
docker compose up migrate
docker exec adaptive_training_coach-db-1 psql -U coach -d adaptive_coach -c \
  "SELECT average_power_watts, max_power_watts FROM cycling_workout_details LIMIT 5;"
```
Expected: migration applies cleanly; any pre-existing cycling rows show `NULL` in both new columns (no error, no default other than NULL).

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0051_cycling_power.py backend/app/db/models.py \
  backend/app/schemas/workouts.py backend/app/services/activities/contracts.py \
  backend/app/repositories/workout_detail_mapper.py backend/tests/use_cases/test_workout_creation.py
git commit -m "feat: add cycling power fields to actuals"
```

---

### Task 2: Extend the screenshot import schema with pace/power/speed/cadence fields

**Files:**
- Modify: `backend/app/schemas/manual_import.py`
- Test: `backend/tests/unit/test_manual_import.py` (new)

**Interfaces:**
- Produces: `ManualWorkoutImportRequest.average_pace_seconds_per_km: float | None`, `.average_pace_seconds_per_100m: float | None`, `.average_speed_kph: float | None`, `.max_speed_kph: float | None`, `.average_power_watts: float | None`, `.max_power_watts: float | None`, `.average_cadence: float | None`, `.max_cadence: float | None`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_manual_import.py
"""Validation coverage for the extended manual-screenshot import request."""

from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.manual_import import ManualWorkoutImportRequest


def test_manual_import_accepts_optional_pace_power_speed_cadence() -> None:
    request = ManualWorkoutImportRequest(
        discipline="CYCLING",
        source_app_name="Wahoo",
        started_at=datetime(2026, 9, 5, 8, tzinfo=UTC),
        duration_seconds=3600,
        average_speed_kph=28.5,
        max_speed_kph=42.0,
        average_power_watts=185.0,
        max_power_watts=310.0,
        average_cadence=88.0,
        max_cadence=105.0,
    )

    assert request.average_speed_kph == 28.5
    assert request.max_speed_kph == 42.0
    assert request.average_power_watts == 185.0
    assert request.max_power_watts == 310.0
    assert request.average_cadence == 88.0
    assert request.max_cadence == 105.0


def test_manual_import_pace_fields_default_to_none() -> None:
    request = ManualWorkoutImportRequest(
        discipline="RUNNING",
        source_app_name="Strava",
        started_at=datetime(2026, 9, 5, 8, tzinfo=UTC),
        duration_seconds=1800,
    )

    assert request.average_pace_seconds_per_km is None
    assert request.average_pace_seconds_per_100m is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_manual_import.py -v`
Expected: FAIL with `ValidationError: ... Extra inputs are not permitted` (the fields don't exist yet on the strict schema).

- [ ] **Step 3: Write minimal implementation**

In `backend/app/schemas/manual_import.py`, in `ManualWorkoutImportRequest`, right after `max_heart_rate: float | None = Field(default=None, ge=0, le=300)`:

```python
    average_pace_seconds_per_km: float | None = Field(default=None, ge=0)
    average_pace_seconds_per_100m: float | None = Field(default=None, ge=0)
    average_speed_kph: float | None = Field(default=None, ge=0)
    max_speed_kph: float | None = Field(default=None, ge=0)
    average_power_watts: float | None = Field(default=None, ge=0)
    max_power_watts: float | None = Field(default=None, ge=0)
    average_cadence: float | None = Field(default=None, ge=0)
    max_cadence: float | None = Field(default=None, ge=0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_manual_import.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/manual_import.py backend/tests/unit/test_manual_import.py
git commit -m "feat: add pace/power/speed/cadence fields to the screenshot import schema"
```

---

### Task 3: Extend the extraction prompt to read pace/power/speed/cadence when visible

**Files:**
- Modify: `backend/app/integrations/llm/vision.py` (`_EXTRACTION_PROMPT`)
- Test: `backend/tests/unit/test_vision.py` (extend)

**Interfaces:**
- Consumes: `ManualWorkoutImportRequest` fields from Task 2 (the structured-output call already targets this schema directly, so extending it only requires prompt text — no code change to the call site).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_vision.py`:

```python
def test_extraction_prompt_covers_pace_power_speed_and_cadence() -> None:
    prompt = vision._EXTRACTION_PROMPT

    assert "average_pace_seconds_per_km" in prompt
    assert "average_pace_seconds_per_100m" in prompt
    assert "average_power_watts" in prompt
    assert "average_speed_kph" in prompt
    assert "average_cadence" in prompt
    assert "static bike" in prompt.lower() or "smart trainer" in prompt.lower()
    assert "treadmill" in prompt.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_vision.py -v`
Expected: FAIL with `AssertionError` (none of these terms are in the current prompt).

- [ ] **Step 3: Write minimal implementation**

In `backend/app/integrations/llm/vision.py`, append to `_EXTRACTION_PROMPT` (right before the closing `"""`, after the existing "Leave any field the image does not show empty rather than guessing a value." line):

```python
- average_pace_seconds_per_km (RUNNING only): if a running pace is shown \
(e.g. "5:30/km" or "5:30 min/km"), convert it to whole seconds per \
kilometre (5:30 -> 330). Leave empty if no pace is shown.
- average_pace_seconds_per_100m (SWIMMING only): if a swim pace is shown \
(e.g. "1:45/100m"), convert it to whole seconds per 100 metres (1:45 -> \
105). Leave empty if no pace is shown.
- average_speed_kph, max_speed_kph, average_power_watts, max_power_watts \
(CYCLING only): extract directly whenever the screen shows speed in km/h \
or power in watts, which is common on a smart trainer or static bike \
display. Static bike screens are this athlete's primary equipment: read \
their watts and speed fields especially carefully when present. Leave any \
of these empty if not shown.
- average_cadence, max_cadence (RUNNING or CYCLING): extract steps-per-\
minute (running) or revolutions-per-minute (cycling) cadence if shown. \
Treadmill screens are this athlete's primary running equipment: read \
their pace field especially carefully when present. Leave empty if not \
shown.
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_vision.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add backend/app/integrations/llm/vision.py backend/tests/unit/test_vision.py
git commit -m "feat: extend the screenshot extraction prompt for pace/power/speed/cadence"
```

---

### Task 4: Map the new screenshot fields into the canonical import contract

**Files:**
- Modify: `backend/app/services/activities/adapters/manual_screenshot.py`
- Test: `backend/tests/unit/test_manual_screenshot_adapter.py` (new)

**Interfaces:**
- Consumes: `ManualWorkoutImportRequest` fields (Task 2), `ActivityImportData` fields (Task 1 + existing).
- Produces: `from_manual_screenshot()` now populates `ActivityImportData.average_pace_seconds_per_km`, `.average_pace_seconds_per_100m`, `.average_speed_kph`, `.max_speed_kph`, `.average_power_watts`, `.max_power_watts`, `.average_cadence`, `.max_cadence`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_manual_screenshot_adapter.py
"""Field-coverage tests for the screenshot-to-canonical-import mapping."""

from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.manual_import import ManualWorkoutImportRequest
from app.services.activities.adapters.manual_screenshot import from_manual_screenshot


def test_adapter_maps_cycling_power_speed_and_cadence() -> None:
    payload = ManualWorkoutImportRequest(
        discipline="CYCLING",
        source_app_name="Wahoo",
        started_at=datetime(2026, 9, 5, 8, tzinfo=UTC),
        duration_seconds=3600,
        distance_meters=30_000,
        average_speed_kph=28.5,
        max_speed_kph=42.0,
        average_power_watts=185.0,
        max_power_watts=310.0,
        average_cadence=88.0,
        max_cadence=105.0,
    )

    incoming = from_manual_screenshot(payload)

    assert incoming.average_speed_kph == 28.5
    assert incoming.max_speed_kph == 42.0
    assert incoming.average_power_watts == 185.0
    assert incoming.max_power_watts == 310.0
    assert incoming.average_cadence == 88.0
    assert incoming.max_cadence == 105.0


def test_adapter_maps_running_pace() -> None:
    payload = ManualWorkoutImportRequest(
        discipline="RUNNING",
        source_app_name="Strava",
        started_at=datetime(2026, 9, 5, 8, tzinfo=UTC),
        duration_seconds=1800,
        distance_meters=5000,
        average_pace_seconds_per_km=330,
    )

    incoming = from_manual_screenshot(payload)

    assert incoming.average_pace_seconds_per_km == 330


def test_adapter_leaves_unshown_fields_null() -> None:
    payload = ManualWorkoutImportRequest(
        discipline="CYCLING",
        source_app_name="Wahoo",
        started_at=datetime(2026, 9, 5, 8, tzinfo=UTC),
        duration_seconds=3600,
    )

    incoming = from_manual_screenshot(payload)

    assert incoming.average_power_watts is None
    assert incoming.max_power_watts is None
    assert incoming.average_speed_kph is None
    assert incoming.average_cadence is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_manual_screenshot_adapter.py -v`
Expected: FAIL — `AssertionError: assert None == 28.5` (the adapter doesn't map these fields yet).

- [ ] **Step 3: Write minimal implementation**

In `backend/app/services/activities/adapters/manual_screenshot.py`, in `from_manual_screenshot()`, add to the `ActivityImportData(...)` constructor call (after `max_heart_rate=payload.max_heart_rate,`):

```python
        average_pace_seconds_per_km=payload.average_pace_seconds_per_km,
        average_pace_seconds_per_100m=payload.average_pace_seconds_per_100m,
        average_speed_kph=payload.average_speed_kph,
        max_speed_kph=payload.max_speed_kph,
        average_power_watts=payload.average_power_watts,
        max_power_watts=payload.max_power_watts,
        average_cadence=payload.average_cadence,
        max_cadence=payload.max_cadence,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_manual_screenshot_adapter.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/activities/adapters/manual_screenshot.py \
  backend/tests/unit/test_manual_screenshot_adapter.py
git commit -m "feat: map screenshot pace/power/speed/cadence into the import contract"
```

---

### Task 5: Close the pre-existing HR-in-first-week-prescription gap

**Files:**
- Modify: `backend/app/services/weekly_planning/zones.py`
- Test: `backend/tests/unit/test_first_week_zones.py` (extend)

**Interfaces:**
- Produces: `hr_zone_bands(max_hr: float) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]` (public, replaces the now-deleted call site of the old private `_heart_rate_zones`; used by Task 6). `power_zones`, `running_pace_zones`, `swim_pace_zones` (renamed from `_power_zones`, `_running_pace_zones`, `_swim_pace_zones` — public so Task 6 can reuse them for display).
- Removes: the observed-HR fallback branch inside `_resolve_discipline()` — `resolve_first_week_zones()` can no longer return `metric="HEART_RATE_BPM"` for any discipline.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_first_week_zones.py`:

```python
from app.schemas.fitness import BaselineCalculation


def test_resolver_never_selects_heart_rate_even_with_reliable_observed_hr() -> None:
    calculation = BaselineCalculation(
        discipline=Discipline.RUNNING,
        analysis_started_at=datetime(2026, 8, 1, tzinfo=UTC),
        analysis_ended_at=datetime(2026, 9, 1, tzinfo=UTC),
        calculated_at=datetime(2026, 9, 1, tzinfo=UTC),
        session_count=1,
        active_day_count=1,
        total_duration_seconds=1800,
        distance_session_count=1,
        longest_duration_seconds=1800,
        reliable_hr_sample_count=1,
        reliable_max_hr_bpm=172.0,
        confidence=0.5,
        discipline_metrics_jsonb={},
    )

    zones = resolve_first_week_zones(
        baseline=None,
        calculations={Discipline.RUNNING: calculation},
        disciplines=(Discipline.RUNNING,),
    )

    assert zones[Discipline.RUNNING].mode == "RPE_FALLBACK"
    assert zones[Discipline.RUNNING].metric == "RPE"
```

(Add `from datetime import UTC, datetime` to the file's imports if not already present.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_first_week_zones.py -v`
Expected: FAIL — `assert 'HEART_RATE_BPM' == 'RPE'` (today's code selects HR from the observed-HR fallback).

- [ ] **Step 3: Write minimal implementation**

In `backend/app/services/weekly_planning/zones.py`, replace the `_resolve_discipline` body's tail (the observed-HR check right before the RPE fallback return):

```python
    if calculation is not None and calculation.reliable_max_hr_bpm is not None:
        return _heart_rate_zones(calculation.reliable_max_hr_bpm)
    return ResolvedIntensityZones(
```

with (delete the HR-fallback branch entirely):

```python
    return ResolvedIntensityZones(
```

Rename the four zone-building functions to drop their leading underscore (mechanical rename only, no behavior change), and update their three call sites inside `_resolve_discipline` accordingly:

```python
def power_zones(ftp: int) -> ResolvedIntensityZones:
    ...  # body unchanged, just renamed from _power_zones


def running_pace_zones(race_pace: float) -> ResolvedIntensityZones:
    ...  # body unchanged, just renamed from _running_pace_zones


def swim_pace_zones(threshold_pace: float) -> ResolvedIntensityZones:
    ...  # body unchanged, just renamed from _swim_pace_zones
```

Replace the now-deleted `_heart_rate_zones` function with a public banding helper Task 6 will reuse:

```python
def hr_zone_bands(
    max_hr: float,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    """Shared easy/moderate/hard percent-of-max-HR bands (60-75/76-85/86-92%).

    Not used anywhere in this module any more (the observed-HR prescription
    fallback was removed above) -- exported purely so the display-only
    age-based reference zones in app/services/athlete_zones.py use the exact
    same percentages without duplicating them.
    """

    return (
        (round(max_hr * 0.60), round(max_hr * 0.75)),
        (round(max_hr * 0.76), round(max_hr * 0.85)),
        (round(max_hr * 0.86), round(max_hr * 0.92)),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_first_week_zones.py -v`
Expected: PASS, full file, no regressions (the earlier grep in this plan's exploration confirmed no test imports the private names directly, so the rename is safe).

Then run the wider first-week test surface to confirm nothing else assumed the old behavior:

Run: `python -m pytest tests/unit/test_first_week_menu_validation.py tests/unit/test_first_week_tiers.py tests/use_cases/test_weekly_planning.py -v`
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/weekly_planning/zones.py backend/tests/unit/test_first_week_zones.py
git commit -m "fix: stop the first-week planner from ever selecting HEART_RATE_BPM"
```

---

### Task 6: Age-based reference HR zones + display-zone composition

**Files:**
- Create: `backend/app/services/athlete_zones.py`
- Test: `backend/tests/unit/test_athlete_zones.py` (new)

**Interfaces:**
- Consumes: `hr_zone_bands`, `power_zones`, `running_pace_zones`, `swim_pace_zones` from Task 5; `AthleteBaselineData` (existing schema).
- Produces: `ReferenceHeartRateZones` (new type), `estimate_max_hr_bpm(*, birth_year: int, current_year: int) -> float`, `resolve_reference_hr_zones(*, birth_year: int, current_year: int) -> ReferenceHeartRateZones`, `AthleteDisplayZones` (new type), `resolve_athlete_display_zones(*, birth_year: int | None, baseline: AthleteBaselineData | None, current_year: int) -> AthleteDisplayZones`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_athlete_zones.py
"""Age-based reference HR zones and display-zone composition, display-only."""

from __future__ import annotations

from app.schemas.baseline import (
    AthleteBaselineData,
    CyclingBaseline,
    RecentRaceResult,
    RunningBaseline,
)
from app.services.athlete_zones import (
    estimate_max_hr_bpm,
    resolve_athlete_display_zones,
    resolve_reference_hr_zones,
)


def test_estimate_max_hr_uses_tanaka_formula() -> None:
    # 208 - 0.7 * (2026 - 1990) = 208 - 0.7 * 36 = 182.8
    assert estimate_max_hr_bpm(birth_year=1990, current_year=2026) == 182.8


def test_reference_hr_zones_carry_an_approximation_caveat() -> None:
    zones = resolve_reference_hr_zones(birth_year=1990, current_year=2026)

    assert zones.estimated_max_hr_bpm == 182.8
    assert zones.easy == (round(182.8 * 0.60), round(182.8 * 0.75))
    assert zones.moderate == (round(182.8 * 0.76), round(182.8 * 0.85))
    assert zones.hard == (round(182.8 * 0.86), round(182.8 * 0.92))
    assert "approximate" in zones.caveat
    assert "±10-12 bpm" in zones.caveat or "10-12 bpm" in zones.caveat


def test_display_zones_compose_hr_pace_and_power_from_baseline() -> None:
    baseline = AthleteBaselineData(
        running=RunningBaseline(
            typical_weekly_sessions=4,
            typical_weekly_duration_minutes=200,
            longest_recent_run_minutes=90,
            recent_race_result=RecentRaceResult(distance_km=10, duration_seconds=2520),
        ),
        cycling=CyclingBaseline(
            typical_weekly_sessions=3,
            typical_weekly_duration_minutes=240,
            longest_recent_ride_minutes=120,
            riding_environment="INDOOR",
            riding_confidence="CONFIDENT",
            recent_ftp_watts=260,
        ),
    )

    zones = resolve_athlete_display_zones(
        birth_year=1990, baseline=baseline, current_year=2026
    )

    assert zones.heart_rate is not None
    assert zones.heart_rate.estimated_max_hr_bpm == 182.8
    assert zones.running is not None
    assert zones.running.metric == "PACE_SECONDS_PER_KM"
    assert zones.cycling is not None
    assert zones.cycling.metric == "POWER_WATTS"
    assert zones.swimming is None  # no swim baseline supplied


def test_display_zones_handle_missing_birth_year_and_baseline() -> None:
    zones = resolve_athlete_display_zones(
        birth_year=None, baseline=None, current_year=2026
    )

    assert zones.heart_rate is None
    assert zones.running is None
    assert zones.cycling is None
    assert zones.swimming is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_athlete_zones.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.athlete_zones'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/athlete_zones.py
"""Athlete-facing verification zones: HR (age-estimated), pace, and power.

Entirely separate from weekly_planning.zones's prescription-facing
ResolvedIntensityZones for heart rate: nothing here is read by the planner
or the prompt-building code. HR here is display-only; it can never become
a first-week prescription metric (see weekly_planning/zones.py, which no
longer has any code path that returns HEART_RATE_BPM at all).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.schemas.baseline import AthleteBaselineData
from app.services.weekly_planning.zones import (
    ResolvedIntensityZones,
    hr_zone_bands,
    power_zones,
    running_pace_zones,
    swim_pace_zones,
)

TANAKA_INTERCEPT = 208.0
TANAKA_AGE_COEFFICIENT = 0.7
HR_ZONE_CAVEAT = (
    "Age-estimated max heart rate is approximate (individual maxHR can vary "
    "±10-12 bpm) and will be refined from your observed workout heart "
    "rate over time."
)


class ReferenceHeartRateZones(BaseModel):
    """Age-estimated max HR and its easy/moderate/hard bands, display-only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    estimated_max_hr_bpm: float
    easy: tuple[float, float]
    moderate: tuple[float, float]
    hard: tuple[float, float]
    caveat: str = HR_ZONE_CAVEAT


class AthleteDisplayZones(BaseModel):
    """Everything the "view my zones" command shows, independent of any plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    heart_rate: ReferenceHeartRateZones | None
    running: ResolvedIntensityZones | None
    cycling: ResolvedIntensityZones | None
    swimming: ResolvedIntensityZones | None


def estimate_max_hr_bpm(*, birth_year: int, current_year: int) -> float:
    """Tanaka formula: maxHR = 208 - 0.7 x age, age from whole birth years."""

    age = current_year - birth_year
    return TANAKA_INTERCEPT - TANAKA_AGE_COEFFICIENT * age


def resolve_reference_hr_zones(
    *, birth_year: int, current_year: int
) -> ReferenceHeartRateZones:
    """Age-based HR zones for verification/display only -- never prescribed."""

    max_hr = estimate_max_hr_bpm(birth_year=birth_year, current_year=current_year)
    easy, moderate, hard = hr_zone_bands(max_hr)
    return ReferenceHeartRateZones(
        estimated_max_hr_bpm=round(max_hr, 1),
        easy=easy,
        moderate=moderate,
        hard=hard,
    )


def resolve_athlete_display_zones(
    *,
    birth_year: int | None,
    baseline: AthleteBaselineData | None,
    current_year: int,
) -> AthleteDisplayZones:
    """Compose HR (age)/pace(race)/power(FTP)/swim-pace(400m), display only.

    Unlike the planner's zone resolver, this shows a zone whenever the
    matching baseline value exists, regardless of recent workout evidence --
    "view my zones" is explicitly plan-independent.
    """

    heart_rate = (
        resolve_reference_hr_zones(birth_year=birth_year, current_year=current_year)
        if birth_year is not None
        else None
    )
    race = baseline.running.recent_race_result if baseline and baseline.running else None
    running = (
        running_pace_zones(race.duration_seconds / race.distance_km)
        if race is not None
        else None
    )
    ftp = baseline.cycling.recent_ftp_watts if baseline and baseline.cycling else None
    cycling = power_zones(ftp) if ftp is not None else None
    threshold = (
        baseline.swimming.recent_400m_seconds if baseline and baseline.swimming else None
    )
    swimming = swim_pace_zones(threshold / 4) if threshold is not None else None
    return AthleteDisplayZones(
        heart_rate=heart_rate, running=running, cycling=cycling, swimming=swimming
    )


__all__ = [
    "AthleteDisplayZones",
    "ReferenceHeartRateZones",
    "estimate_max_hr_bpm",
    "resolve_athlete_display_zones",
    "resolve_reference_hr_zones",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_athlete_zones.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/athlete_zones.py backend/tests/unit/test_athlete_zones.py
git commit -m "feat: add age-based reference HR zones, display-only"
```

---

### Task 7: The single seconds→min:sec pace formatter

**Files:**
- Create: `backend/app/services/formatting.py`
- Test: `backend/tests/unit/test_formatting.py` (new)

**Interfaces:**
- Produces: `format_pace_min_sec(seconds_per_unit: float, *, unit_label: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_formatting.py
"""The one seconds->min:sec pace renderer athlete-facing output must use."""

from __future__ import annotations

from app.services.formatting import format_pace_min_sec


def test_formats_running_pace_seconds_per_km_as_min_sec() -> None:
    assert format_pace_min_sec(296, unit_label="/km") == "4:56/km"


def test_formats_swimming_pace_seconds_per_100m_as_min_sec() -> None:
    assert format_pace_min_sec(95, unit_label="/100m") == "1:35/100m"


def test_pads_single_digit_seconds() -> None:
    assert format_pace_min_sec(305, unit_label="/km") == "5:05/km"


def test_rounds_fractional_seconds_before_formatting() -> None:
    assert format_pace_min_sec(296.6, unit_label="/km") == "4:57/km"


def test_never_renders_raw_seconds_without_a_colon() -> None:
    rendered = format_pace_min_sec(296, unit_label="/km")

    assert ":" in rendered
    assert rendered != "296/km"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_formatting.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.formatting'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/formatting.py
"""Athlete-facing unit formatting.

Pace is stored and computed in seconds everywhere else in this codebase
(running s/km, swimming s/100m) -- this is the single place that converts
it to the min:sec an athlete actually sees. Every athlete-facing surface
that shows a pace (the "view my zones" command, plan rendering, and any
future evaluator output) must call this rather than rendering seconds.
"""

from __future__ import annotations


def format_pace_min_sec(seconds_per_unit: float, *, unit_label: str) -> str:
    """Render e.g. 296 with unit_label="/km" as "4:56/km"."""

    total_seconds = round(seconds_per_unit)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}:{seconds:02d}{unit_label}"


__all__ = ["format_pace_min_sec"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_formatting.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/formatting.py backend/tests/unit/test_formatting.py
git commit -m "feat: add the single seconds-to-min:sec pace formatter"
```

---

### Task 8: The "view my zones" read model and message rendering

**Files:**
- Modify: `backend/app/services/accounts/service.py` (`AccountQueryService`)
- Modify: `backend/app/bot/messages.py`
- Test: `backend/tests/unit/test_zones_view_message.py` (new)
- Test: `backend/tests/use_cases/test_account_query_zones.py` (new)

**Interfaces:**
- Consumes: `resolve_athlete_display_zones`, `AthleteDisplayZones` (Task 6); `format_pace_min_sec` (Task 7); `AthleteBaselineRepository` (existing, `app/repositories/athlete_baselines.py`); `ProfileService.get()` (existing, returns `PersistedMandatoryProfileData | None`).
- Produces: `AccountQueryService.zones(identity: TelegramIdentity) -> AthleteDisplayZones | None`; `zones_view(zones: AthleteDisplayZones) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_zones_view_message.py
"""Rendering tests for the read-only "view my zones" message."""

from __future__ import annotations

from app.bot.messages import zones_view
from app.services.athlete_zones import AthleteDisplayZones, ReferenceHeartRateZones
from app.services.weekly_planning.zones import power_zones, running_pace_zones


def test_zones_view_renders_hr_pace_and_power_with_min_sec() -> None:
    zones = AthleteDisplayZones(
        heart_rate=ReferenceHeartRateZones(
            estimated_max_hr_bpm=182.8,
            easy=(110, 137),
            moderate=(139, 155),
            hard=(157, 168),
        ),
        running=running_pace_zones(252.0),
        cycling=power_zones(260),
        swimming=None,
    )

    text = zones_view(zones)

    assert "182.8" in text or "183" in text
    assert "approximate" in text
    assert ":" in text  # pace rendered as min:sec somewhere
    assert "296" not in text  # never show a raw pace-in-seconds number
    assert "no numeric source" in text.lower()  # swimming has no baseline


def test_zones_view_notes_missing_birth_year() -> None:
    zones = AthleteDisplayZones(
        heart_rate=None, running=None, cycling=None, swimming=None
    )

    text = zones_view(zones)

    assert "birth year" in text.lower() or "profile" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_zones_view_message.py -v`
Expected: FAIL with `ImportError: cannot import name 'zones_view' from 'app.bot.messages'`.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/bot/messages.py`, add (near `persisted_profile`):

```python
from app.services.athlete_zones import AthleteDisplayZones
from app.services.formatting import format_pace_min_sec
from app.services.weekly_planning.zones import ResolvedIntensityZones


def zones_view(zones: AthleteDisplayZones) -> str:
    """Render the read-only "view my zones" output."""

    lines = ["Your current zones (informational only, not a training plan):", ""]
    if zones.heart_rate is not None:
        hr = zones.heart_rate
        lines.append(f"Heart rate (age-estimated max ~{hr.estimated_max_hr_bpm:.0f} bpm):")
        lines.append(f"  Easy: {hr.easy[0]:.0f}-{hr.easy[1]:.0f} bpm")
        lines.append(f"  Moderate: {hr.moderate[0]:.0f}-{hr.moderate[1]:.0f} bpm")
        lines.append(f"  Hard: {hr.hard[0]:.0f}-{hr.hard[1]:.0f} bpm")
        lines.append(f"  {hr.caveat}")
    else:
        lines.append("Heart rate: add your birth year in /profile to see this.")
    lines.append("")
    lines.append(_zone_line("Running pace", zones.running, unit_label="/km"))
    lines.append(_zone_line("Cycling power", zones.cycling, unit_label=" W"))
    lines.append(_zone_line("Swimming pace", zones.swimming, unit_label="/100m"))
    return "\n".join(lines)


def _zone_line(
    label: str, zone: ResolvedIntensityZones | None, *, unit_label: str
) -> str:
    if zone is None or zone.mode == "RPE_FALLBACK":
        return f"{label}: no numeric source yet -- use RPE/feel."
    assert zone.easy is not None
    assert zone.moderate is not None
    assert zone.hard is not None
    if zone.metric == "POWER_WATTS":
        render = _render_power_range
    else:
        render = _render_pace_range
    return (
        f"{label}: easy {render(zone.easy, unit_label)}, "
        f"moderate {render(zone.moderate, unit_label)}, "
        f"hard {render(zone.hard, unit_label)}"
    )


def _render_power_range(bounds: tuple[float, float], unit_label: str) -> str:
    return f"{bounds[0]:.0f}-{bounds[1]:.0f}{unit_label}"


def _render_pace_range(bounds: tuple[float, float], unit_label: str) -> str:
    return (
        f"{format_pace_min_sec(bounds[0], unit_label=unit_label)}-"
        f"{format_pace_min_sec(bounds[1], unit_label=unit_label)}"
    )
```

In `backend/app/services/accounts/service.py`, add imports at the top:

```python
from datetime import UTC, datetime

from pydantic import ValidationError

from app.repositories.athlete_baselines import AthleteBaselineRepository
from app.schemas.baseline import AthleteBaselineData
from app.services.athlete_zones import AthleteDisplayZones, resolve_athlete_display_zones
```

And add a method to `AccountQueryService`, right after `profile()`:

```python
    async def zones(
        self,
        identity: TelegramIdentity,
    ) -> AthleteDisplayZones | None:
        user_id = await self.resolve_user_id(identity)
        if user_id is None:
            return None
        profile = await self._profiles.get(user_id=user_id)
        if profile is None:
            return None
        async with self._session_factory() as session:
            saved_baseline = await AthleteBaselineRepository(session).get(
                athlete_id=user_id
            )
        baseline: AthleteBaselineData | None = None
        if saved_baseline is not None:
            try:
                baseline = AthleteBaselineData.model_validate(
                    saved_baseline.baseline_jsonb
                )
            except ValidationError:
                baseline = None
        return resolve_athlete_display_zones(
            birth_year=profile.birth_year,
            baseline=baseline,
            current_year=datetime.now(UTC).year,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_zones_view_message.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Write the query-service test (failing first)**

```python
# backend/tests/use_cases/test_account_query_zones.py
"""Zones read-model coverage: profile + baseline compose into display zones."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base
from app.domain.enums import AthleteGender, CoachingStyle, Discipline
from app.repositories.athlete_baselines import AthleteBaselineRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.users import UserRepository
from app.schemas.baseline import AthleteBaselineData, RunningBaseline, TrainingPreferences
from app.schemas.common import TelegramIdentity
from app.services.accounts.service import AccountQueryService


@pytest_asyncio.fixture
async def database() -> AsyncIterator[
    tuple[AsyncEngine, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    yield engine, factory
    await engine.dispose()


def _identity() -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=5001, telegram_username="zones_test", first_name="Z"
    )


@pytest.mark.asyncio
async def test_zones_composes_birth_year_and_baseline(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    identity = _identity()
    async with factory.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=identity.telegram_user_id,
            telegram_username=identity.telegram_username,
            first_name=identity.first_name,
        )
        await ProfileRepository(session).upsert_mandatory_athlete_profile(
            user_id=user.id,
            birth_year=1990,
            gender=AthleteGender.MALE,
            weight_kg=74,
            height_cm=179,
        )
        await AthleteBaselineRepository(session).upsert(
            athlete_id=user.id,
            goal_signature="test-signature",
            baseline=AthleteBaselineData(
                running=RunningBaseline(
                    typical_weekly_sessions=3,
                    typical_weekly_duration_minutes=150,
                    longest_recent_run_minutes=60,
                ),
                preferences=TrainingPreferences(
                    coaching_style=CoachingStyle.NORMAL,
                    desired_weekly_sessions={Discipline.RUNNING: 3},
                ),
            ),
        )

    service = AccountQueryService(factory)

    zones = await service.zones(identity)

    assert zones is not None
    assert zones.heart_rate is not None
    assert zones.running is None  # no recent_race_result in the baseline above


@pytest.mark.asyncio
async def test_zones_returns_none_for_unknown_athlete(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = database
    service = AccountQueryService(factory)

    zones = await service.zones(
        TelegramIdentity(telegram_user_id=999, telegram_username=None, first_name=None)
    )

    assert zones is None
```

- [ ] **Step 6: Run test to verify it fails, then run both new files to verify green**

Run: `python -m pytest tests/use_cases/test_account_query_zones.py -v`
Expected first: FAIL with `AttributeError: 'AccountQueryService' object has no attribute 'zones'` (before Step 3's implementation is picked up — if Step 3 already landed the method, this instead confirms PASS immediately; either order is fine here since Step 3's code above already includes the method, but run this to prove the query-service test itself is exercising real behavior).

Then run: `python -m pytest tests/unit/test_zones_view_message.py tests/use_cases/test_account_query_zones.py -v`
Expected: PASS (4 tests total).

- [ ] **Step 7: Commit**

```bash
git add backend/app/bot/messages.py backend/app/services/accounts/service.py \
  backend/tests/unit/test_zones_view_message.py backend/tests/use_cases/test_account_query_zones.py
git commit -m "feat: add the view-my-zones read model and message rendering"
```

---

### Task 9: Wire the `/zones` bot command

**Files:**
- Modify: `backend/app/bot/service.py` (`CoachBotApplicationService`)
- Modify: `backend/app/bot/handlers.py`
- Modify: `backend/app/bot/router.py`
- Test: `backend/tests/bot/test_zones_command.py` (new)

**Interfaces:**
- Consumes: `AccountQueryService.zones()` (Task 8), `messages.zones_view()` (Task 8).
- Produces: `CoachBotApplicationService.zones(identity: TelegramIdentity) -> TelegramResponse`; `zones_handler`; `CommandHandler("zones", zones_handler)` registered in the bot application.

- [ ] **Step 1: Write the failing test**

First, check the existing dispatch test pattern for `/profile` to mirror it exactly:

Run: `grep -n "def test.*profile" backend/tests/bot/test_context_onboarding_service.py backend/tests/bot/*.py 2>/dev/null | head -5`

Then write, in a new file:

```python
# backend/tests/bot/test_zones_command.py
"""Dispatch coverage for the read-only /zones command."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.bot.service import CoachBotApplicationService
from app.schemas.common import TelegramIdentity
from app.services.athlete_zones import AthleteDisplayZones


def _identity() -> TelegramIdentity:
    return TelegramIdentity(telegram_user_id=7001, telegram_username="z", first_name="Z")


@pytest.mark.asyncio
async def test_zones_command_renders_the_view_when_athlete_known() -> None:
    account_queries = AsyncMock()
    account_queries.zones.return_value = AthleteDisplayZones(
        heart_rate=None, running=None, cycling=None, swimming=None
    )
    service = CoachBotApplicationService.__new__(CoachBotApplicationService)
    service._account_queries = account_queries

    response = await service.zones(_identity())

    account_queries.zones.assert_awaited_once_with(_identity())
    assert "birth year" in response.text.lower() or "profile" in response.text.lower()


@pytest.mark.asyncio
async def test_zones_command_reports_not_found_for_unknown_athlete() -> None:
    account_queries = AsyncMock()
    account_queries.zones.return_value = None
    service = CoachBotApplicationService.__new__(CoachBotApplicationService)
    service._account_queries = account_queries

    response = await service.zones(_identity())

    assert response.text == "Sorry, I don't have your profile yet. Send /start."
```

(If the exploration in Step 1 finds `messages.NOT_FOUND` has different exact text than the placeholder above, use the real constant's value instead of a literal string — check `backend/app/bot/messages.py` for `NOT_FOUND` and match it exactly.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/bot/test_zones_command.py -v`
Expected: FAIL with `AttributeError: 'CoachBotApplicationService' object has no attribute 'zones'`.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/bot/service.py`, add right after `async def profile(...)`:

```python
    async def zones(self, identity: TelegramIdentity) -> TelegramResponse:
        zones = await self._account_queries.zones(identity)
        return (
            TelegramResponse(messages.zones_view(zones))
            if zones is not None
            else TelegramResponse(messages.NOT_FOUND)
        )
```

Add `"/zones": self.zones,` to the command-dispatch dict (find it near `"/profile": self.profile,` at the line the exploration found, around line 201).

In `backend/app/bot/handlers.py`, add right after `profile_handler`:

```python
async def zones_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await _agent_delegate(update, context, "/zones")
```

In `backend/app/bot/router.py`, add right after the `"profile"` `CommandHandler` registration:

```python
    application.add_handler(CommandHandler("zones", zones_handler))
```

(Add `zones_handler` to the import from `app.bot.handlers` at the top of `router.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/bot/test_zones_command.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/bot/service.py backend/app/bot/handlers.py backend/app/bot/router.py \
  backend/tests/bot/test_zones_command.py
git commit -m "feat: add the read-only /zones command"
```

---

### Task 10: Full verification pass + live sample for the report

**Files:** none (verification only).

- [ ] **Step 1: Run the full backend suite**

```bash
docker run --rm \
  -v "$(pwd)/backend:/app" \
  -e DATABASE_URL=sqlite+aiosqlite:///:memory: \
  -e LLM_MODE=mock \
  adaptive-training-coach-backend:dev \
  python -m pytest -q
```
Expected: all tests pass, zero regressions, zero warnings beyond the pre-existing SQLAlchemy deprecation warning already present before this plan.

- [ ] **Step 2: Run ruff, ruff format, and mypy over the whole backend**

```bash
docker run --rm \
  -v "$(pwd)/backend:/app" \
  adaptive-training-coach-backend:dev \
  bash -c "ruff check . && ruff format --check . && mypy app"
```
Expected: all three green. If the dev image predates any of this plan's earlier commits, rebuild it first: `docker build --build-arg INSTALL_DEV=true -t adaptive-training-coach-backend:dev -f backend/Dockerfile backend/`.

- [ ] **Step 3: Rebuild the production-parity image and re-run migrations**

```bash
docker build -t adaptive-training-coach-backend:local -f backend/Dockerfile backend/
docker compose up -d db
docker compose up migrate
```
Expected: migration `0051_cycling_power` applies on top of `0050_weekly_plan_outcomes`, exits 0.

- [ ] **Step 4: Regenerate the fit triathlete's "view my zones" output for the report**

Reuse the existing live harness fixtures (`backend/scripts/baseline_adaptation_test.py`'s `ATHLETES["A"]` baseline) with a small ad hoc script that calls `AccountQueryService.zones()` and `messages.zones_view()` directly against the already-seeded Athlete A row, then prints the rendered text. Do not regenerate a new plan (no live LLM call needed for this step — "view my zones" never calls a model).

- [ ] **Step 5: Confirm the "no HR prescription" guarantee end-to-end**

Run: `python -m pytest tests/unit/test_first_week_zones.py tests/unit/test_first_week_menu_validation.py -v -k "heart_rate or hr"`
Expected: the Task 5 regression test passes, and no other test in these files ever asserts a HEART_RATE_BPM metric in a first-week prescription.

- [ ] **Step 6: Commit if anything was left uncommitted**

```bash
git status
git add -A
git commit -m "chore: verification pass for capture gaps + HR zones + unit formatting" --allow-empty
```

---

## Self-Review

**Spec coverage:**
- Part 1 (cycling power) → Task 1.
- Part 2 (extend screenshot extractor) → Tasks 2-4.
- Part 3 (derive pace/speed) → covered by the Global Constraints note: already implemented, deliberately left unchanged per the user's explicit choice; Tasks 2-4 only add capture for the case distance/duration aren't both shown.
- Part 4 (age-based HR zones, verification-only, never prescribed) → Tasks 5-6. Task 5 additionally closes the pre-existing gap that threatened this guarantee before any of this plan's code even lands.
- Part 5 ("view my zones" command) → Tasks 8-9.
- Part 6 (unit formatting) → Task 7 (formatter) + Task 8 (used in `zones_view`).
- "Explicitly unchanged": `FirstWeekPlanPrescription`/`WeeklyPlanPrescription` schemas and `app/workflows/prompts/weekly_planning.py` are never opened by any task above. `compare_week()`'s `FirstWeekPlan` no-op (`service.py:476-479`) is untouched.
- Verify checklist: migration applies/nulls (Task 1 Step 5) — extractor field coverage (Task 3) — derived pace/speed already covered, tested at Task 2/4's adapter level — screenshot missing field stays null (Task 4's third test) — zone resolver excludes HR from prescription (Task 5) — "view my zones" caveat (Task 8) — pace formatting tests (Task 7) — full suite/ruff/mypy (Task 10).

**Placeholder scan:** no TBD/TODO strings; every step has literal code or a literal shell command. Task 9 Step 1 has one explicit "check the real value and use it" instruction rather than a placeholder string left unresolved — this is deliberate because the exact `NOT_FOUND` message text wasn't independently re-verified during planning and must not be guessed at implementation time.

**Type consistency:** `hr_zone_bands` (Task 5) returns the same 3-tuple-of-2-tuples shape `resolve_reference_hr_zones` (Task 6) destructures. `AthleteDisplayZones` (Task 6) is the same type `zones_view` (Task 8) and `AccountQueryService.zones()` (Task 8) both use. `format_pace_min_sec` (Task 7) signature matches exactly how `_render_pace_range` (Task 8) calls it. `ResolvedIntensityZones` (existing, renamed functions from Task 5) is the type `zones.running`/`zones.cycling`/`zones.swimming` hold in `AthleteDisplayZones`.

**Open item carried forward, not resolved here:** the "derived vs. directly-extracted" provenance marker was explicitly declined by the user (see Global Constraints) — flagged here so a future brief doesn't rediscover this as a surprise gap.
