# Planner Produces A Valid Plan — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the weekly planner produce a valid, honest plan for any athlete, instead of refusing every request and then failing if it ever got through.

**Architecture:** Seven independent backend changes, ordered so each one is testable alone. Three fix the deterministic evidence calculation so the numbers sent to the model are true. Two change the gate so one weak sport cannot veto a whole plan. Two fix the model call itself, which has never once produced a parseable plan.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Pydantic v2, pytest with `asyncio_mode = "auto"`, LangChain with `langchain-openai` 1.6, DeepSeek as the provider.

**Spec:** `docs/superpowers/specs/2026-08-28-adaptive-planning-design.md` — sections 2, 3.1 to 3.5, 4.3, 4.5. Read it before starting. This plan argues from it.

## Global Constraints

- Run all commands from `backend/`. Validation is `pytest`, `ruff check .`, `ruff format --check .`, `mypy app`. All four must pass before every commit.
- Python 3.12 or later. The host Mac has only 3.9, so run tests through Docker: `docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c "pip install -q '.[dev]' && python -m pytest ..."`. That writes setuptools output into `backend/build/`, which is already gitignored; leave it alone.
- Timezone-aware UTC timestamps everywhere. Explicit enums. Pydantic models at boundaries. SQLAlchemy 2 async APIs. Narrow exception types.
- Never log secrets, OAuth tokens, raw health descriptions, full profiles, or unredacted free-text answers.
- Keep Telegram handlers thin. Business behaviour belongs in services and repositories.
- The fitness calculator in `app/services/fitness/calculator.py` is pure and deterministic. Keep it that way. Version behaviour changes through `CALCULATION_VERSION`, never by rewriting stored rows.
- **The system is not being built for one triathlete.** Every task's tests must cover a single-sport athlete as well as a multisport one. See spec section 1.1.
- **`athlete_baseline_assessments` currently holds zero rows** in every environment. The `CALCULATION_VERSION` bump in Task 2 is therefore bookkeeping, not a migration problem. Do not write a backfill.

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `app/services/fitness/calculator.py` | Pure baseline maths. Gains zero-distance normalisation, a confidence ceiling, and active dates. | 1, 2, 3 |
| `app/schemas/fitness.py` | `BaselineCalculation` gains `active_dates`. | 3 |
| `app/domain/enums.py` | New `DisciplineEvidenceState`. | 3 |
| `app/schemas/weekly_plans.py` | `PlanReadinessDiscipline.ready` becomes `state`; `PlanReadiness.ready` becomes a stored field plus athlete-level totals. | 3 |
| `app/services/weekly_planning/evidence.py` | Builds readiness from the new states; snapshot gains baseline window bounds. | 3, 7 |
| `app/services/weekly_planning/service.py` | Passes evidence state to the prompt, passes its own window to the baseline service, stops swallowing validation failures. | 4, 6, 7 |
| `app/workflows/prompts/weekly_planning.py` | Prompt v2: says what the evidence states mean. | 4 |
| `app/bot/messages.py` | The insufficient-evidence message describes the new rule. | 4 |
| `app/integrations/llm/live.py` | Sends the schema via `function_calling`. | 5 |
| `app/services/fitness/service.py` | Accepts an explicit window from the planner. | 7 |

New test files: `tests/unit/test_fitness_calculator.py` (Tasks 1, 2, 3), plus additions to `tests/unit/test_weekly_planning_evidence.py` and `tests/use_cases/test_weekly_planning.py`.

---

### Task 1: Zero distance means "not measured"

An indoor trainer ride reports zero metres because distance does not apply, not because the athlete travelled nowhere. Zero is not `None`, so today it counts as distance evidence and drags every derived speed to `0.0`. The live database contains two such rides, and the planner would tell the coach the athlete cycles at 0 km/h.

**Files:**
- Modify: `app/services/fitness/calculator.py`
- Create test: `tests/unit/test_fitness_calculator.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `calculate_baseline_window` unchanged in signature. Behaviour change only: for `RUNNING`, `CYCLING`, `HIKING` and `SWIMMING`, an input `distance_meters == 0` is treated exactly as `None`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_fitness_calculator.py`:

```python
"""Pure baseline-calculation tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.domain.enums import ActivitySource, Discipline
from app.schemas.fitness import FitnessWorkoutEvidence
from app.services.fitness.calculator import calculate_baseline_window

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


def _workout(
    *,
    discipline: Discipline,
    started_at: datetime,
    distance_meters: float | None,
    duration_seconds: int = 3600,
) -> FitnessWorkoutEvidence:
    return FitnessWorkoutEvidence(
        workout_id=uuid.uuid4(),
        discipline=discipline,
        source=ActivitySource.APPLE_HEALTH,
        started_at=started_at,
        duration_seconds=duration_seconds,
        fitness_input_updated_at=NOW,
        distance_meters=distance_meters,
    )


def test_zero_distance_is_treated_as_unmeasured_not_as_zero_speed() -> None:
    """An indoor ride reports 0 m because distance does not apply."""

    calculation = calculate_baseline_window(
        discipline=Discipline.CYCLING,
        workouts=(
            _workout(
                discipline=Discipline.CYCLING,
                started_at=NOW - timedelta(days=8),
                distance_meters=0.0,
            ),
            _workout(
                discipline=Discipline.CYCLING,
                started_at=NOW - timedelta(days=2),
                distance_meters=0.0,
            ),
        ),
        window_started_at=NOW - timedelta(days=30),
        window_ended_at=NOW,
        calculated_at=NOW,
    )

    assert calculation is not None
    assert calculation.session_count == 2
    assert calculation.distance_session_count == 0
    assert calculation.known_distance_meters is None
    assert calculation.discipline_metrics_jsonb["elapsed_speed_kph"] is None
    assert "MISSING_DISTANCE" in calculation.quality_flags_jsonb


def test_real_distance_is_still_counted() -> None:
    calculation = calculate_baseline_window(
        discipline=Discipline.RUNNING,
        workouts=(
            _workout(
                discipline=Discipline.RUNNING,
                started_at=NOW - timedelta(days=3),
                distance_meters=10_000.0,
                duration_seconds=3600,
            ),
        ),
        window_started_at=NOW - timedelta(days=30),
        window_ended_at=NOW,
        calculated_at=NOW,
    )

    assert calculation is not None
    assert calculation.distance_session_count == 1
    assert calculation.known_distance_meters == 10_000.0
    assert "MISSING_DISTANCE" not in calculation.quality_flags_jsonb
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest tests/unit/test_fitness_calculator.py -v"
```
Expected: `test_zero_distance_is_treated_as_unmeasured_not_as_zero_speed` FAILS. `distance_session_count` will be `2`, `known_distance_meters` will be `0.0`, and `elapsed_speed_kph` will be `0.0` rather than `None`. The second test passes already.

- [ ] **Step 3: Implement the normalisation**

In `app/services/fitness/calculator.py`, add the constant next to the existing `_DUPLICATE_SOURCES` (around line 26):

```python
_DISTANCE_MEANINGFUL = {
    Discipline.RUNNING,
    Discipline.CYCLING,
    Discipline.HIKING,
    Discipline.SWIMMING,
}
```

Add the helper below `calculate_baseline_window`:

```python
def _normalise_unmeasured_distance(
    workouts: Sequence[FitnessWorkoutEvidence],
) -> tuple[FitnessWorkoutEvidence, ...]:
    """Treat a zero distance as absent where distance is meaningful.

    An indoor trainer ride reports zero metres because distance does not
    apply, not because the athlete travelled nowhere. Zero is not None, so
    without this the session counts as distance evidence and drags every
    derived pace and speed to zero.
    """

    return tuple(
        workout.model_copy(update={"distance_meters": None})
        if workout.discipline in _DISTANCE_MEANINGFUL
        and workout.distance_meters == 0
        else workout
        for workout in workouts
    )
```

In `calculate_baseline_window`, normalise before the window filter. Replace:

```python
    in_window = tuple(
        workout
        for workout in workouts
```

with:

```python
    in_window = tuple(
        workout
        for workout in _normalise_unmeasured_distance(workouts)
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest tests/unit/test_fitness_calculator.py -v && python -m pytest -q"
```
Expected: both new tests PASS and the whole suite still passes.

- [ ] **Step 5: Lint, type check and commit**

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && ruff check . && ruff format --check . && mypy app"
git add app/services/fitness/calculator.py tests/unit/test_fitness_calculator.py
git commit -m "Treat a zero distance as unmeasured where distance is meaningful

An indoor trainer ride reports zero metres because distance does not apply.
Zero is not None, so it counted as distance evidence and drove every derived
speed to zero. The planner would have told the coach the athlete cycles at
0 km/h."
```

---

### Task 2: Confidence cannot claim certainty without heart rate

`_confidence` saturates at 1.0 on session volume alone. An athlete with five sessions, no heart rate and no moving duration scores maximum confidence while the coach knows nothing whatsoever about how hard they trained.

**Files:**
- Modify: `app/services/fitness/calculator.py`
- Modify test: `tests/unit/test_fitness_calculator.py`

**Interfaces:**
- Consumes: Task 1's `_normalise_unmeasured_distance` is already in place; no direct dependency.
- Produces: `CALCULATION_VERSION == 2`. `_confidence` gains a `has_reliable_hr: bool` keyword argument and caps its result at `_NO_HEART_RATE_CONFIDENCE_CEILING = 0.6` when that is `False`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_fitness_calculator.py`:

```python
def test_confidence_is_capped_when_no_heart_rate_is_available() -> None:
    """Volume alone must not read as full confidence: effort is unknown."""

    workouts = tuple(
        _workout(
            discipline=Discipline.RUNNING,
            started_at=NOW - timedelta(days=day),
            distance_meters=10_000.0,
        )
        for day in (2, 4, 6, 8, 10, 12)
    )
    calculation = calculate_baseline_window(
        discipline=Discipline.RUNNING,
        workouts=workouts,
        window_started_at=NOW - timedelta(days=30),
        window_ended_at=NOW,
        calculated_at=NOW,
    )

    assert calculation is not None
    assert calculation.reliable_hr_sample_count == 0
    assert calculation.confidence <= 0.6


def test_calculation_version_is_two() -> None:
    from app.services.fitness.calculator import CALCULATION_VERSION

    assert CALCULATION_VERSION == 2
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest tests/unit/test_fitness_calculator.py -v"
```
Expected: the confidence test FAILS with a confidence of `1.0`, and the version test FAILS with `1 != 2`.

- [ ] **Step 3: Implement the ceiling and bump the version**

In `app/services/fitness/calculator.py`, change line 20:

```python
CALCULATION_VERSION = 2
```

Add next to it:

```python
# With no reliable heart rate anywhere in the window, effort is entirely
# unknown, so the score must not read as certainty however many sessions
# there are.
_NO_HEART_RATE_CONFIDENCE_CEILING = 0.6
```

Change the `_confidence` signature and its final return:

```python
def _confidence(
    *,
    session_count: int,
    active_day_count: int,
    distance_session_count: int,
    reliable_hr_sample_count: int,
    quality_flags: Sequence[str],
) -> float:
    confidence = 0.2
    confidence += min(session_count, 5) * 0.09
    confidence += min(active_day_count, 5) * 0.06
    confidence += 0.12 * (distance_session_count / session_count)
    if reliable_hr_sample_count:
        confidence += 0.08
    else:
        confidence = min(confidence, _NO_HEART_RATE_CONFIDENCE_CEILING)
    if "MISSING_DISTANCE" in quality_flags:
        confidence -= 0.04
    if "COARSE_HR_ONLY" in quality_flags:
        confidence -= 0.03
    return round(min(1.0, max(0.0, confidence)), 4)
```

No call-site change is needed: `reliable_hr_sample_count` is already passed.

- [ ] **Step 4: Run the tests and confirm they pass**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest -q"
```
Expected: all PASS. If an existing test asserts an exact confidence value, update it to the new number and note in the commit that the calculation version changed.

- [ ] **Step 5: Lint, type check and commit**

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && ruff check . && ruff format --check . && mypy app"
git add app/services/fitness/calculator.py tests/unit/test_fitness_calculator.py
git commit -m "Cap confidence at 0.6 when no reliable heart rate exists

The score saturated at 1.0 on session volume alone, so the coach was told
we were certain about an athlete whose effort is entirely unknown. Bumps
CALCULATION_VERSION to 2; no stored rows exist, so no backfill is needed."
```

---

### Task 3: Per-sport evidence states and a whole-athlete floor

Today the gate needs 3 sessions and 2 active days in **every** target sport, and `PlanReadiness.ready` is an `all(...)`. A triathlete with one swim gets no plan at all, including for the running and cycling that are nearly ready. Replace that with a floor judged on the athlete as a whole, and classify each sport instead of vetoing on it.

**Files:**
- Modify: `app/schemas/fitness.py`
- Modify: `app/services/fitness/calculator.py`
- Modify: `app/domain/enums.py`
- Modify: `app/schemas/weekly_plans.py`
- Modify: `app/services/weekly_planning/evidence.py`
- Modify test: `tests/unit/test_weekly_planning_evidence.py`

**Interfaces:**
- Consumes: `calculate_baseline_window` from Tasks 1 and 2.
- Produces:
  - `BaselineCalculation.active_dates: tuple[date, ...]`, sorted, not persisted to `athlete_baseline_assessments`.
  - `DisciplineEvidenceState` with members `WELL_EVIDENCED`, `THIN`, `NONE`.
  - `PlanReadinessDiscipline.state: DisciplineEvidenceState`, replacing `ready: bool`.
  - `PlanReadiness.ready: bool` as a stored field, plus `total_session_count: int` and `total_active_day_count: int`.
  - `build_plan_readiness` keeps its existing keyword signature.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_weekly_planning_evidence.py`:

```python
from app.domain.enums import DisciplineEvidenceState


def _running(started_at: datetime) -> FitnessWorkoutEvidence:
    return _workout(source=ActivitySource.TCX, started_at=started_at)


def _readiness_for(calculations: dict[Discipline, object]) -> object:
    return build_plan_readiness(
        week_start=date(2026, 8, 31),
        window_started_at=NOW - timedelta(days=30),
        window_ended_at=NOW,
        calculations=calculations,
    )


def _calculation_with(discipline: Discipline, day_offsets: tuple[int, ...]):
    workouts = tuple(
        FitnessWorkoutEvidence(
            workout_id=uuid.uuid4(),
            discipline=discipline,
            source=ActivitySource.APPLE_HEALTH,
            started_at=NOW - timedelta(days=offset),
            duration_seconds=1800,
            fitness_input_updated_at=NOW,
            distance_meters=5_000,
        )
        for offset in day_offsets
    )
    return calculate_baseline_window(
        discipline=discipline,
        workouts=workouts,
        window_started_at=NOW - timedelta(days=30),
        window_ended_at=NOW,
        calculated_at=NOW,
    )


def test_one_thin_sport_no_longer_vetoes_the_whole_plan() -> None:
    """A triathlete with 2 rides, 2 runs and 1 swim must get a plan."""

    readiness = _readiness_for(
        {
            Discipline.CYCLING: _calculation_with(Discipline.CYCLING, (8, 2)),
            Discipline.RUNNING: _calculation_with(Discipline.RUNNING, (6, 4)),
            Discipline.SWIMMING: _calculation_with(Discipline.SWIMMING, (7,)),
        }
    )

    assert readiness.total_session_count == 5
    assert readiness.total_active_day_count == 5
    assert readiness.ready is True
    states = {row.discipline: row.state for row in readiness.disciplines}
    assert states[Discipline.CYCLING] is DisciplineEvidenceState.THIN
    assert states[Discipline.RUNNING] is DisciplineEvidenceState.THIN
    assert states[Discipline.SWIMMING] is DisciplineEvidenceState.THIN


def test_a_single_sport_athlete_reaches_the_floor_alone() -> None:
    """A runner who only runs must not be penalised for having one sport."""

    readiness = _readiness_for(
        {Discipline.RUNNING: _calculation_with(Discipline.RUNNING, (6, 4, 2))}
    )

    assert readiness.ready is True
    assert readiness.disciplines[0].state is DisciplineEvidenceState.WELL_EVIDENCED


def test_a_sport_with_no_sessions_is_none_and_does_not_block() -> None:
    readiness = _readiness_for(
        {
            Discipline.RUNNING: _calculation_with(Discipline.RUNNING, (6, 4, 2)),
            Discipline.SWIMMING: None,
        }
    )

    assert readiness.ready is True
    states = {row.discipline: row.state for row in readiness.disciplines}
    assert states[Discipline.SWIMMING] is DisciplineEvidenceState.NONE


def test_below_the_floor_is_not_ready() -> None:
    readiness = _readiness_for(
        {Discipline.RUNNING: _calculation_with(Discipline.RUNNING, (6, 6))}
    )

    assert readiness.total_session_count == 2
    assert readiness.total_active_day_count == 1
    assert readiness.ready is False
```

Add the imports this needs at the top of the file: `from app.domain.enums import DisciplineEvidenceState` and `from datetime import date`.

- [ ] **Step 2: Run the tests and confirm they fail**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest tests/unit/test_weekly_planning_evidence.py -v"
```
Expected: FAIL with `ImportError: cannot import name 'DisciplineEvidenceState'`.

- [ ] **Step 3: Add the enum**

In `app/domain/enums.py`, next to `HeartRateTemporalQuality`:

```python
class DisciplineEvidenceState(StrEnum):
    """How much recent evidence the planner holds for one target discipline."""

    WELL_EVIDENCED = "WELL_EVIDENCED"
    THIN = "THIN"
    NONE = "NONE"
```

- [ ] **Step 4: Add `active_dates` to the calculation**

In `app/schemas/fitness.py`, add `date` to the datetime import and add the field to `BaselineCalculation` immediately after `active_day_count`:

```python
    active_dates: tuple[date, ...] = ()
```

In `app/services/fitness/calculator.py`, inside `calculate_baseline_window`, replace:

```python
    active_day_count = len({_as_utc(item.started_at).date() for item in included})
```

with:

```python
    active_dates = tuple(
        sorted({_as_utc(item.started_at).date() for item in included})
    )
    active_day_count = len(active_dates)
```

and add `active_dates=active_dates,` to the `BaselineCalculation(...)` construction, directly after `active_day_count=active_day_count,`.

Do **not** add `"active_dates"` to `_EVIDENCE_ATTRIBUTES` in `app/services/fitness/service.py`. It must not reach `athlete_baseline_assessments`, which has no such column.

- [ ] **Step 5: Change the readiness schemas**

In `app/schemas/weekly_plans.py`, add `from app.domain.enums import DisciplineEvidenceState` to the imports, then replace `PlanReadinessDiscipline` and `PlanReadiness`:

```python
class PlanReadinessDiscipline(_WeeklyPlanSchema):
    """Recent evidence held for one target discipline."""

    discipline: Discipline
    session_count: int = Field(ge=0)
    active_day_count: int = Field(ge=0)
    state: DisciplineEvidenceState
    quality_flags: tuple[str, ...] = ()


class PlanReadiness(_WeeklyPlanSchema):
    """The deterministic preflight outcome, before any provider call.

    ``ready`` is judged on the athlete as a whole rather than per discipline,
    so a sport with little history is planned gently instead of blocking the
    sports that are ready.
    """

    week_start: date
    analysis_started_at: datetime
    analysis_ended_at: datetime
    disciplines: tuple[PlanReadinessDiscipline, ...]
    total_session_count: int = Field(ge=0)
    total_active_day_count: int = Field(ge=0)
    ready: bool
```

- [ ] **Step 6: Build the new readiness**

In `app/services/weekly_planning/evidence.py`, add `from app.domain.enums import Discipline, DisciplineEvidenceState` and replace `build_plan_readiness`:

```python
def _evidence_state(
    calculation: BaselineCalculation | None,
) -> DisciplineEvidenceState:
    if calculation is None or calculation.session_count == 0:
        return DisciplineEvidenceState.NONE
    if (
        calculation.session_count >= MINIMUM_SESSIONS
        and calculation.active_day_count >= MINIMUM_ACTIVE_DAYS
    ):
        return DisciplineEvidenceState.WELL_EVIDENCED
    return DisciplineEvidenceState.THIN


def build_plan_readiness(
    *,
    week_start: date,
    window_started_at: datetime,
    window_ended_at: datetime,
    calculations: dict[Discipline, BaselineCalculation | None],
) -> PlanReadiness:
    """Build the planner gate, judged on the athlete rather than per sport.

    A discipline with little history is classified rather than used as a veto,
    so a triathlete with one swim still receives a plan for their running and
    cycling, with the swim treated gently.
    """

    rows = tuple(
        PlanReadinessDiscipline(
            discipline=discipline,
            session_count=calculation.session_count if calculation else 0,
            active_day_count=calculation.active_day_count if calculation else 0,
            state=_evidence_state(calculation),
            quality_flags=tuple(calculation.quality_flags_jsonb) if calculation else (),
        )
        for discipline, calculation in sorted(
            calculations.items(), key=lambda item: item[0].value
        )
    )
    active_dates: set[date] = set()
    for calculation in calculations.values():
        if calculation is not None:
            active_dates.update(calculation.active_dates)
    total_session_count = sum(row.session_count for row in rows)
    total_active_day_count = len(active_dates)
    return PlanReadiness(
        week_start=week_start,
        analysis_started_at=window_started_at,
        analysis_ended_at=window_ended_at,
        disciplines=rows,
        total_session_count=total_session_count,
        total_active_day_count=total_active_day_count,
        ready=(
            bool(rows)
            and total_session_count >= MINIMUM_SESSIONS
            and total_active_day_count >= MINIMUM_ACTIVE_DAYS
        ),
    )
```

Also exclude `active_dates` from the snapshot, to keep up to ninety dates out of the prompt. In `build_evidence_snapshot`, change:

```python
            discipline.value: calculation.model_dump(mode="json")
```

to:

```python
            discipline.value: calculation.model_dump(
                mode="json", exclude={"active_dates"}
            )
```

- [ ] **Step 7: Fix the two existing assertions that used `.ready` on a row**

In `tests/unit/test_weekly_planning_evidence.py` line 56, replace `assert readiness.disciplines[0].ready is True` with:

```python
    assert readiness.disciplines[0].state is DisciplineEvidenceState.WELL_EVIDENCED
```

At line 81, replace `assert readiness.disciplines[0].ready is False` with:

```python
    assert readiness.disciplines[0].state is not DisciplineEvidenceState.WELL_EVIDENCED
```

In `tests/use_cases/test_weekly_planning.py`, add `DisciplineEvidenceState` to the
`app.domain.enums` import, then replace the assertion at line 223. That athlete has
2 running sessions on 2 days, which is `THIN`, not `WELL_EVIDENCED`:

```python
    assert (row.discipline, row.session_count, row.active_day_count, row.state) == (
        Discipline.RUNNING,
        2,
        2,
        DisciplineEvidenceState.THIN,
    )
```

That test asserts `result.kind == "insufficient"`, and it still should: two sessions
is below the new floor of three, so the athlete-level gate refuses for a different
and better reason than before.

- [ ] **Step 8: Run the whole suite**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest -q"
```
Expected: all PASS. `app/bot/messages.py:257` still references `item.ready` and will now raise at runtime, but no test covers it; Task 4 fixes it. If a test does fail there, jump to Task 4 Step 3 and bring it forward.

- [ ] **Step 9: Lint, type check and commit**

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && ruff check . && ruff format --check . && mypy app"
git add app/domain/enums.py app/schemas/fitness.py app/schemas/weekly_plans.py \
        app/services/fitness/calculator.py app/services/weekly_planning/evidence.py \
        tests/unit/test_weekly_planning_evidence.py tests/use_cases/test_weekly_planning.py
git commit -m "Judge planner readiness on the athlete, not on every sport

PlanReadiness.ready was an all() over target disciplines, so a triathlete
with one swim got no plan at all, including for the running and cycling that
were nearly ready. Each sport is now classified WELL_EVIDENCED, THIN or NONE
and the floor is three sessions on two days across all sports together."
```

---

### Task 4: Tell the coach which sports are thin

The evidence state is computed and persisted but never reaches the model (`app/services/weekly_planning/service.py:299-300` copies only `recent_evidence` and `baselines` out of the snapshot). The prompt must also say what the states mean, and the Telegram refusal message still describes the old per-sport rule and tells the athlete to upload a file.

**Files:**
- Modify: `app/services/weekly_planning/service.py:299-300`
- Modify: `app/workflows/prompts/weekly_planning.py`
- Modify: `app/bot/messages.py:254-270`
- Modify test: `tests/unit/test_weekly_planning_prompt.py`

**Interfaces:**
- Consumes: `PlanReadiness` and `DisciplineEvidenceState` from Task 3.
- Produces: `prompt_context["evidence_state"]`, a `dict[str, str]` mapping discipline value to state value. `WEEKLY_PLANNER_PROMPT_VERSION == 2`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_weekly_planning_prompt.py`:

```python
def test_system_prompt_explains_every_evidence_state() -> None:
    from app.domain.enums import DisciplineEvidenceState
    from app.workflows.prompts.weekly_planning import (
        WEEKLY_PLANNER_PROMPT_VERSION,
        build_weekly_planner_messages,
    )

    assert WEEKLY_PLANNER_PROMPT_VERSION == 2
    system = str(build_weekly_planner_messages({"week_start": "2026-08-31"})[0].content)
    for state in DisciplineEvidenceState:
        assert state.value in system
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest tests/unit/test_weekly_planning_prompt.py -v"
```
Expected: FAIL, because the version is 1 and the state names do not appear.

- [ ] **Step 3: Update the prompt**

In `app/workflows/prompts/weekly_planning.py`, set `WEEKLY_PLANNER_PROMPT_VERSION: Final = 2` and replace the system prompt:

```python
_WEEKLY_PLANNER_SYSTEM_PROMPT: Final = """You are an endurance coach creating one
safe, concise weekly plan.
Return only the required structured schema for the requested Monday-to-Sunday week.
Use the athlete's target disciplines, recent aggregated evidence, immutable baselines,
availability, available equipment/access, and stated training limitations. Do not make
medical claims. Every day must be present. Rest days have no sessions and a brief rest
note. Training sessions must have an existing discipline, clear objective, duration,
intensity, and a concise structure. Do not invent measurements not in the context.

evidence_state tells you how much recent history exists for each target discipline.
Respect it:
WELL_EVIDENCED: enough recent history to plan normally for this discipline.
THIN: very little recent history. Give it one short, easy, clearly introductory
session. Do not prescribe HARD intensity for it.
NONE: no recent history at all. The athlete's goal still requires this discipline, so
include one short, easy, introductory session. Do not prescribe HARD intensity for it.

Plan only the disciplines present in evidence_state. An athlete may have one target
discipline or several."""
```

- [ ] **Step 4: Pass the state into the context**

In `app/services/weekly_planning/service.py`, in the `prompt_context` dictionary, add after the `"baselines"` entry:

```python
                "evidence_state": {
                    row.discipline.value: row.state.value
                    for row in readiness.disciplines
                },
```

- [ ] **Step 5: Fix the refusal message**

In `app/bot/messages.py`, replace `weekly_plan_readiness` entirely:

```python
def weekly_plan_readiness(readiness: PlanReadiness) -> str:
    """Explain the deterministic evidence gate without exposing workout details."""

    return (
        "I need a little more recent training history before I can create a "
        "personalized plan for next week.\n\n"
        "Across all your sports together I need at least 3 sessions on at "
        "least 2 different days in the last 30 days. Right now I have "
        f"{readiness.total_session_count} "
        f"session{'' if readiness.total_session_count == 1 else 's'} on "
        f"{readiness.total_active_day_count} "
        f"day{'' if readiness.total_active_day_count == 1 else 's'}.\n\n"
        "Sync your iPhone, or import an Apple Health export or TCX file, then "
        "try again."
    )
```

- [ ] **Step 6: Run the whole suite**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest -q"
```
Expected: all PASS.

- [ ] **Step 7: Lint, type check and commit**

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && ruff check . && ruff format --check . && mypy app"
git add app/services/weekly_planning/service.py app/workflows/prompts/weekly_planning.py \
        app/bot/messages.py tests/unit/test_weekly_planning_prompt.py
git commit -m "Tell the coach which sports are thin, and say what that means

The evidence state was computed and persisted but never sent to the model,
so it planned a barely-trained sport exactly like a well-trained one. Prompt
version 2 defines all three states. The refusal message now describes the
whole-athlete floor instead of the removed per-sport rule."
```

---

### Task 5: Send the schema to the model

Measured against live DeepSeek on 2026-08-28: today's call returns a reply that fails `WeeklyPlan` validation with **21 errors**. The model invents `day_of_week` and `rest_day` and returns an out-of-range `intensity`, because `method="json_mode"` binds only `response_format: {"type": "json_object"}` and never sends the schema. The same test with `method="function_calling"` returned a valid plan.

**Files:**
- Modify: `app/integrations/llm/live.py:90-93`
- Create test: `tests/unit/test_live_llm_structured_output.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `OpenAICompatibleOnboardingModel.ainvoke_structured` requests structured output via `method="function_calling"`. Its return type, `StructuredModelResponse`, is unchanged.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_live_llm_structured_output.py`:

```python
"""The live adapter must tell the model the output shape it wants."""

from __future__ import annotations

import inspect

from app.integrations.llm.live import OpenAICompatibleOnboardingModel


def test_structured_output_sends_the_schema_to_the_model() -> None:
    """json_mode never sends the schema, so the model has to guess it.

    Measured against live DeepSeek: guessing produced 21 validation errors.
    """

    source = inspect.getsource(OpenAICompatibleOnboardingModel.ainvoke_structured)

    assert 'method="function_calling"' in source
    assert 'method="json_mode"' not in source
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest tests/unit/test_live_llm_structured_output.py -v"
```
Expected: FAIL, because the source still contains `method="json_mode"`.

- [ ] **Step 3: Change the method**

In `app/integrations/llm/live.py`, replace:

```python
        runnable = model.with_structured_output(
            schema,
            method="json_mode",
            include_raw=True,
        )
```

with:

```python
        # json_mode binds only response_format={"type": "json_object"} and never
        # sends the schema, so the model must guess every field name. Measured
        # against live DeepSeek on 2026-08-28, guessing produced 21 validation
        # errors on the weekly plan. function_calling sends the schema as a tool
        # definition and returned a valid plan.
        runnable = model.with_structured_output(
            schema,
            method="function_calling",
            include_raw=True,
        )
```

- [ ] **Step 4: Run the whole suite**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest -q"
```
Expected: all PASS. The onboarding tests use `DeterministicFakeOnboardingModel` and do not touch this adapter.

- [ ] **Step 5: Verify against the real provider**

This is the one change that cannot be proven by unit test. With `LLM_API_KEY` set in `.env`, run one real call and confirm a valid `WeeklyPlan` comes back. Record the outcome in the commit message. If DeepSeek rejects `function_calling`, fall back to writing `schema.model_json_schema()` into the prompt, which was also measured working, following the existing pattern at `app/workflows/catalog_expansion/nodes.py:47`.

- [ ] **Step 6: Lint, type check and commit**

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && ruff check . && ruff format --check . && mypy app"
git add app/integrations/llm/live.py tests/unit/test_live_llm_structured_output.py
git commit -m "Send the output schema to the model

json_mode asks for valid JSON and nothing else, so the model had to guess
every field name. Measured against live DeepSeek, the weekly planner reply
failed validation with 21 errors: it invented day_of_week and rest_day and
returned an intensity outside the permitted set. function_calling sends the
schema as a tool definition and returns a valid plan."
```

---

### Task 6: Tell a broken plan apart from a broken provider

`generate_next_week` wraps the call and the validation in one `except Exception` that reports `unavailable`. A schema failure and a network outage are indistinguishable from outside, which is exactly why the bug in Task 5 survived unnoticed.

**Files:**
- Modify: `app/services/weekly_planning/service.py:142-159`
- Modify test: `tests/use_cases/test_weekly_planning.py`

**Interfaces:**
- Consumes: `WeeklyPlanningResult` unchanged; `kind` still returns `"unavailable"` for both failures, because the Telegram surface has no separate state for them.
- Produces: distinct log records. Validation failures log at `error` with the logger name `app.services.weekly_planning.service` and the message `weekly_plan_response_invalid`; provider failures log `weekly_plan_provider_error`.

- [ ] **Step 1: Write the failing test**

The fakes for both failures already exist: `FakeLLMScenario.MALFORMED` returns a
`StructuredModelResponse` whose output fails validation, and
`FakeLLMScenario.PROVIDER_FAILURE` raises `LLMProviderError`
(`app/integrations/llm/mock.py:75-81`). Append to
`tests/use_cases/test_weekly_planning.py`:

```python
async def _seed_ready_runner(session: AsyncSession) -> uuid.UUID:
    """A single-sport athlete clearing the floor: 3 sessions on 3 days."""

    athlete_id = await _seed_target_goal(session)
    for days_ago in (1, 3, 5):
        await _add_running(
            session, athlete_id=athlete_id, started_at=NOW - timedelta(days=days_ago)
        )
    return athlete_id


@pytest.mark.asyncio
async def test_an_unusable_reply_is_logged_differently_from_an_outage(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A reply with the wrong shape must not look like the provider being down."""

    _, factory = database
    monkeypatch.setattr("app.services.weekly_planning.service.utc_now", lambda: NOW)
    async with factory.begin() as session:
        await _seed_ready_runner(session)

    with caplog.at_level("ERROR"):
        result = await _service(
            factory, scenario=FakeLLMScenario.MALFORMED
        ).generate_next_week(_identity())

    assert result.kind == "unavailable"
    assert "weekly_plan_response_invalid" in caplog.text
    assert "weekly_plan_provider_error" not in caplog.text


@pytest.mark.asyncio
async def test_an_outage_is_logged_as_a_provider_error(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, factory = database
    monkeypatch.setattr("app.services.weekly_planning.service.utc_now", lambda: NOW)
    async with factory.begin() as session:
        await _seed_ready_runner(session)

    with caplog.at_level("ERROR"):
        result = await _service(
            factory, scenario=FakeLLMScenario.PROVIDER_FAILURE
        ).generate_next_week(_identity())

    assert result.kind == "unavailable"
    assert "weekly_plan_provider_error" in caplog.text
    assert "weekly_plan_response_invalid" not in caplog.text
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest tests/use_cases/test_weekly_planning.py -k logged -v"
```
Expected: both FAIL, because neither log line exists yet.

- [ ] **Step 3: Split the failure paths**

In `app/services/weekly_planning/service.py`, add at the top:

```python
import logging

from pydantic import ValidationError

logger = logging.getLogger(__name__)
```

Replace the try block in `generate_next_week`:

```python
        try:
            response = await self._model.ainvoke_structured(
                # Existing provider protocol is shared with onboarding. Feature
                # metadata below distinguishes this non-onboarding call safely.
                step=OnboardingStep.TRAINING_HISTORY_IMPORT,
                schema=WeeklyPlan,
                messages=build_weekly_planner_messages(prepared.prompt_context),
                config={"run_name": "weekly_training_plan"},
            )
        except Exception:  # Provider adapters surface vendor-specific errors.
            logger.exception(
                "weekly_plan_provider_error", extra={"athlete_id": str(prepared.athlete_id)}
            )
            await self._record_usage(
                athlete_id=prepared.athlete_id,
                status=LLMUsageStatus.PROVIDER_ERROR,
                prompt_tokens=None,
                completion_tokens=None,
            )
            return WeeklyPlanningResult(kind="unavailable")

        try:
            plan = WeeklyPlan.model_validate(response.output)
        except ValidationError as error:
            # The reply arrived and was unusable. This is a prompt or schema
            # defect, not an outage, and must not be reported as one.
            logger.error(
                "weekly_plan_response_invalid",
                extra={
                    "athlete_id": str(prepared.athlete_id),
                    "error_count": len(error.errors()),
                    "malformed": response.malformed,
                },
            )
            await self._record_usage(
                athlete_id=prepared.athlete_id,
                status=LLMUsageStatus.PROVIDER_ERROR,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
            )
            return WeeklyPlanningResult(kind="unavailable")
```

Never log the validation error message itself: it embeds the model's reply, which
carries athlete context. Log the error count, not the errors.

Leave the `return await self._persist_generated(...)` call that follows unchanged. It
still reads `response.prompt_tokens` and `response.completion_tokens`, which are bound
by the first try block.

- [ ] **Step 4: Run the whole suite**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest -q"
```
Expected: all PASS.

- [ ] **Step 5: Lint, type check and commit**

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && ruff check . && ruff format --check . && mypy app"
git add app/services/weekly_planning/service.py tests/use_cases/test_weekly_planning.py
git commit -m "Separate an unusable plan from an unreachable provider

Both failures were caught by one except Exception and reported as
unavailable, so a schema defect looked exactly like an outage. That is why
the planner could be broken for every request without anyone noticing."
```

---

### Task 7: Freeze the baseline from the window that authorised it

The gate looks back 30 days from now. `BaselineAssessmentService` then builds its own window: 14 days ending at the athlete's most recent workout. So an athlete can clear a 30 day check and have their permanent baseline frozen from a much thinner slice. The coach is also shown both sets of numbers with dates attached to only one.

**Files:**
- Modify: `app/services/fitness/service.py:101-178`
- Modify: `app/services/weekly_planning/service.py:235-243`
- Modify: `app/services/weekly_planning/evidence.py` (`build_evidence_snapshot`)
- Modify test: `tests/use_cases/test_weekly_planning.py`

**Interfaces:**
- Consumes: `PlanReadiness` from Task 3.
- Produces: `create_missing_baselines_for_disciplines_in_session` gains two optional keyword arguments, `window_started_at: datetime | None = None` and `window_ended_at: datetime | None = None`. When both are given it uses them verbatim. When either is `None` it keeps today's behaviour of anchoring on the latest workout, which the file-import path relies on. Each entry in the snapshot's `baselines` list gains `analysis_started_at` and `analysis_ended_at`.

- [ ] **Step 1: Write the failing test**

Append to `tests/use_cases/test_weekly_planning.py`:

```python
@pytest.mark.asyncio
async def test_the_frozen_baseline_uses_the_window_the_gate_evaluated(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three runs spread across the 30 day window must all reach the baseline.

    Under the old 14-days-before-the-latest-workout window only the most
    recent run fell inside, so a permanent number was frozen from one session
    after a check that had seen three.
    """

    _, factory = database
    monkeypatch.setattr("app.services.weekly_planning.service.utc_now", lambda: NOW)
    async with factory.begin() as session:
        athlete_id = await _seed_target_goal(session)
        for days_ago in (28, 20, 2):
            await _add_running(
                session,
                athlete_id=athlete_id,
                started_at=NOW - timedelta(days=days_ago),
            )

    result = await _service(factory).generate_next_week(_identity())

    assert result.kind == "created"
    async with factory() as session:
        baseline = await session.scalar(
            select(AthleteBaselineAssessment).where(
                AthleteBaselineAssessment.athlete_id == athlete_id
            )
        )
    assert baseline is not None
    assert baseline.session_count == 3
    assert baseline.analysis_started_at is not None
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest tests/use_cases/test_weekly_planning.py -k frozen_baseline -v"
```
Expected: FAIL with `session_count == 1`. The baseline window is the 14 days before
the most recent workout, so only the day-2 run falls inside it, even though the gate
that authorised the baseline saw all three.

- [ ] **Step 3: Let the caller supply the window**

In `app/services/fitness/service.py`, change the signature of `create_missing_baselines_for_disciplines_in_session`:

```python
    async def create_missing_baselines_for_disciplines_in_session(
        self,
        session: AsyncSession,
        *,
        athlete_id: uuid.UUID,
        disciplines: tuple[Discipline, ...],
        calculated_at: datetime | None = None,
        owner_locked: bool = False,
        window_started_at: datetime | None = None,
        window_ended_at: datetime | None = None,
    ) -> tuple[Discipline, ...]:
        """Create baselines for an explicit, already-resolved discipline scope.

        The planner passes the window it just evaluated, so the frozen baseline
        reflects the evidence that authorised it rather than a narrower slice.
        Callers that omit the window keep the latest-workout anchoring, which
        preserves imported historical evidence for the file-import path.
        """
```

Inside the per-discipline loop, replace the window derivation:

```python
            latest_started_at = await repository.latest_workout_started_at(
                athlete_id=athlete_id,
                discipline=discipline,
            )
            if latest_started_at is None:
                continue

            window_ended_at = _as_utc(latest_started_at)
            window_started_at = window_ended_at - timedelta(
                days=self._settings.fitness_window_days
            )
```

with:

```python
            if window_started_at is not None and window_ended_at is not None:
                discipline_started_at = _as_utc(window_started_at)
                discipline_ended_at = _as_utc(window_ended_at)
            else:
                latest_started_at = await repository.latest_workout_started_at(
                    athlete_id=athlete_id,
                    discipline=discipline,
                )
                if latest_started_at is None:
                    continue
                discipline_ended_at = _as_utc(latest_started_at)
                discipline_started_at = discipline_ended_at - timedelta(
                    days=self._settings.fitness_window_days
                )
```

Then replace every later use of `window_started_at` and `window_ended_at` inside that loop, in the `workouts_for_window` call and the `calculate_baseline_window` call, with `discipline_started_at` and `discipline_ended_at`. Rebinding the parameters directly would corrupt the next iteration.

- [ ] **Step 4: Pass the planner's window**

In `app/services/weekly_planning/service.py`, change the baseline call:

```python
            await BaselineAssessmentService(
                settings=self._settings
            ).create_missing_baselines_for_disciplines_in_session(
                session,
                athlete_id=user.id,
                disciplines=disciplines,
                calculated_at=now,
                owner_locked=True,
                window_started_at=window_started_at,
                window_ended_at=now,
            )
```

- [ ] **Step 5: Label the windows in the snapshot**

In `app/services/weekly_planning/evidence.py`, add the two bounds to each entry of the `baselines` list in `build_evidence_snapshot`, immediately after `"discipline"`:

```python
                "analysis_started_at": item.analysis_started_at.isoformat(),
                "analysis_ended_at": item.analysis_ended_at.isoformat(),
```

Without this the model receives two different session counts for the same sport with dates attached to only one of them, and cannot tell them apart.

- [ ] **Step 6: Run the whole suite**

Run:
```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest -q"
```
Expected: all PASS, including the file-import baseline tests, which pass no window and keep the old behaviour.

- [ ] **Step 7: Lint, type check and commit**

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && ruff check . && ruff format --check . && mypy app"
git add app/services/fitness/service.py app/services/weekly_planning/service.py \
        app/services/weekly_planning/evidence.py tests/use_cases/test_weekly_planning.py
git commit -m "Freeze the baseline from the window that authorised it

The gate looked back 30 days from now while the baseline it then created
looked back 14 days from the most recent workout, so a permanent number
could be frozen from far weaker evidence than the check that allowed it.
The planner now passes its own window; file import keeps latest-workout
anchoring. Baseline entries in the snapshot now carry their window bounds."
```

---

## Final verification

- [ ] **Run everything**

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim bash -c \
  "pip install -q '.[dev]' && python -m pytest -q && ruff check . && \
   ruff format --check . && mypy app"
```

- [ ] **Prove it against the real athlete**

Start the database, then re-run the readiness probe from the design session. The athlete has 2 rides, 2 runs and 1 swim on five distinct days.

```bash
docker compose up -d db
```

Expected after this plan: `PlanReadiness.ready` is `True`, all three sports report `THIN`, cycling reports an unknown speed rather than `0.0 km/h`, and cycling confidence is roughly `0.46` rather than `0.62`.

- [ ] **Prove one real end-to-end plan**

With `LLM_API_KEY` set, request a plan for the real athlete through the bot and confirm a valid seven-day plan is stored in `weekly_training_plans`. This is the first time the planner will ever have succeeded.

---

## What this plan deliberately leaves out

These are designed in the spec and belong to later plans. Do not build them here.

| Deferred | Spec section |
|---|---|
| The briefing, the phase calculation, the week intent field | 4.2, 4.7, 4.10 |
| Splitting the prompt into a cached fixed instruction and a variable briefing. Task 4 updates the prompt text but leaves it as one block. | 4.3 |
| Per-request constrained schema, post-generation checks, the volume safety net | 4.4, 4.5, 4.6 |
| Reasoning enabled for the planner call, which needs an adapter change | 4.8 |
| Heart rate, indoor and swim-location fields, backfill, the sample-count request cap | 3.6, 3.7 |
| Feedback collection | 6 |
| Session identity, mid-week changes, watching the week | 7 |
| The Telegram mini app | 8 |
