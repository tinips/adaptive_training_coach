# Stage 1: coaching style + desired sessions per week Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thread `coaching_style` and `desired_sessions_per_week` through onboarding and into the existing Stage 1 weekly planner, so the first week the planner ever generates already reflects both, and an athlete is told immediately if desired sessions don't fit their confirmed availability.

**Architecture:** Stage 1 (baseline → first week) already exists and works
(`WeeklyPlanningService.generate_next_week`, `build_plan_readiness`,
`build_weekly_planner_messages`) — nothing here replaces it. This plan only
extends it: two new onboarding fields captured deterministically (no LLM),
persisted on `AthleteProfile`, checked against confirmed availability at
capture time, and threaded into the existing planner's prompt context and
system prompt. Coaching style's effect on Stage 1 is injected as prompt
guidance for the model to apply, not as a hardcoded numeric constant in
Python — consistent with how the existing `SELF_REPORTED`/`THIN`/`NONE`
evidence-state rules already work in `workflows/prompts/weekly_planning.py`.

**Tech Stack:** Python, FastAPI/async SQLAlchemy, Alembic, Pydantic v2,
pytest + pytest-asyncio, SQLite in-memory for repository/use-case tests.

**Spec:** `docs/superpowers/specs/2026-09-03-multi-horizon-planning-design.md`
(see "What we collect, once, at onboarding" and "Stage 1 — Week one, before
any workout exists"). This plan implements only Stage 1's two new fields;
Stages 2–7 are not in scope.

## Global Constraints

- No deletions. `WeeklyPlanningService`, `build_plan_readiness`, and the
  existing `SELF_REPORTED`/`THIN`/`NONE` prompt rules stay exactly as they
  are; every change here is additive.
- `desired_sessions_per_week` is a soft target ("aiming for, not locked to"
  per the spec) — it must never become a hard constraint the planner is
  required to satisfy exactly.
- The absolute safety floor for `SELF_REPORTED`/`THIN`/`NONE` disciplines
  (no HARD intensity, duration ranges not exact pace/power, zero volume ≠
  maintain-zero) does not vary by coaching style. Only how assertive the
  plan is *within* that floor varies by style.
- Coaching style's Stage-1 effect is expressed as system-prompt guidance for
  the model to apply, not as a hardcoded per-style numeric ceiling in
  Python. No new numeric constants (percentages, minute caps) are added to
  this codebase for Stage 1.
- Editing `coaching_style` / `desired_sessions_per_week` later via the
  post-onboarding profile-settings menu (`ProfileSettingsStep`,
  `profile_settings_sessions`) is explicitly **out of scope** for this plan
  — the spec calls it out ("editable later") but this plan only covers
  first-capture at onboarding plus planner wiring, so the plan is
  independently shippable and testable.
- Work happens directly on `main` (no feature branch), per standing
  instruction.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/domain/enums.py` | Add `CoachingStyle` enum; add `COACHING_STYLE_INTAKE`, `DESIRED_SESSIONS_INTAKE` to `OnboardingStep`. |
| `app/schemas/profile.py` | Add `DesiredSessionsPerWeek` model; add both fields to `PersistedMandatoryProfileData`. |
| `app/schemas/availability.py` | Add `available_day_count_for_discipline()`, a pure helper both the new onboarding check and (optionally, future) `_plan_fits_availability` can share. |
| `app/db/models.py` | Add `coaching_style`, `desired_sessions_per_week_jsonb` columns to `AthleteProfile`. |
| `alembic/versions/0047_coaching_style_desired_sessions.py` | Migration: new columns + extended `onboarding_step`/`llm_usage.onboarding_step` check constraints. |
| `app/repositories/profiles.py` | Widen `AthleteProfileContext` + `get_athlete_profile_context`; widen `update_athlete_profile_context_fields` allow-list. |
| `app/services/onboarding/desired_sessions_form.py` (new) | Deterministic field registry + integer parsing for desired-sessions capture, mirrors `baseline_form.py`. |
| `app/services/onboarding/service.py` | `confirm_availability` now transitions to `COACHING_STYLE_INTAKE`; new `choose_coaching_style`, `_start_desired_sessions`, `submit_desired_sessions_form`; new `_result()` branches. |
| `app/schemas/onboarding_service.py` | New `OnboardingResultKind` literals. |
| `app/bot/messages.py` | Copy for the two new steps + the availability-conflict message. |
| `app/bot/keyboards.py` | `coaching_style_keyboard()`. |
| `app/bot/service.py` | Callback dispatch for coaching-style buttons; `_render_onboarding` branches for both new steps. |
| `app/workflows/prompts/weekly_planning.py` | Coaching-style guidance paragraph; prompt version bump to 6. |
| `app/services/weekly_planning/service.py` | `_prepare()` reads and forwards `coaching_style` / `desired_sessions_per_week`. |
| Tests | `tests/unit/test_profile_schemas.py` (new), `tests/unit/test_desired_sessions_form.py` (new), `tests/unit/test_weekly_planning_prompt.py` (extend), `tests/use_cases/test_mandatory_profile_onboarding.py` (extend), `tests/bot/test_context_onboarding_service.py` (extend). |

---

### Task 1: Domain enum + schema layer

**Files:**
- Modify: `app/domain/enums.py`
- Modify: `app/schemas/profile.py`
- Test: `tests/unit/test_profile_schemas.py` (new)

**Interfaces:**
- Produces: `CoachingStyle` (StrEnum: `CONSERVATIVE`, `NORMAL`, `DEMANDING`), `OnboardingStep.COACHING_STYLE_INTAKE`, `OnboardingStep.DESIRED_SESSIONS_INTAKE`, `DesiredSessionsPerWeek` (pydantic model with `running: int | None`, `cycling: int | None`, `swimming: int | None`, each `Field(ge=0, le=14)`), `PersistedMandatoryProfileData.coaching_style: CoachingStyle | None`, `PersistedMandatoryProfileData.desired_sessions_per_week: DesiredSessionsPerWeek | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_profile_schemas.py
"""Validation tests for the coaching-style and desired-sessions profile fields."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.enums import CoachingStyle
from app.schemas.profile import DesiredSessionsPerWeek, PersistedMandatoryProfileData


def test_desired_sessions_per_week_accepts_zero_to_fourteen_per_discipline() -> None:
    value = DesiredSessionsPerWeek(running=3, cycling=2, swimming=3)
    assert value.running == 3
    assert value.cycling == 2
    assert value.swimming == 3


def test_desired_sessions_per_week_rejects_out_of_range() -> None:
    with pytest.raises(ValidationError):
        DesiredSessionsPerWeek(running=15)


def test_persisted_mandatory_profile_accepts_coaching_style_and_desired_sessions() -> None:
    data = PersistedMandatoryProfileData(
        birth_year=1990,
        gender="MALE",  # type: ignore[arg-type]
        weight_kg=74,
        height_cm=179,
        coaching_style=CoachingStyle.NORMAL,
        desired_sessions_per_week=DesiredSessionsPerWeek(
            running=3, cycling=2, swimming=3
        ),
    )
    assert data.coaching_style is CoachingStyle.NORMAL
    assert data.desired_sessions_per_week is not None
    assert data.desired_sessions_per_week.swimming == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_profile_schemas.py -v`
Expected: FAIL with `ImportError: cannot import name 'CoachingStyle'` (or
`DesiredSessionsPerWeek`), since neither exists yet.

- [ ] **Step 3: Write minimal implementation**

In `app/domain/enums.py`, add near `AthleteGender`:

```python
class CoachingStyle(StrEnum):
    """How assertively the planner is allowed to push, chosen at onboarding."""

    CONSERVATIVE = "CONSERVATIVE"
    NORMAL = "NORMAL"
    DEMANDING = "DEMANDING"
```

In the same file's `OnboardingStep`, insert two members between
`AVAILABILITY_REVIEW` and `EQUIPMENT_RECOMMENDATION` (declaration order is
cosmetic only — see Task 3 for the actual runtime transition wiring):

```python
    AVAILABILITY_REVIEW = "AVAILABILITY_REVIEW"
    COACHING_STYLE_INTAKE = "COACHING_STYLE_INTAKE"
    DESIRED_SESSIONS_INTAKE = "DESIRED_SESSIONS_INTAKE"
    EQUIPMENT_RECOMMENDATION = "EQUIPMENT_RECOMMENDATION"
```

In `app/schemas/profile.py`, add the model and the two new fields:

```python
from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import AthleteGender, CoachingStyle


class DesiredSessionsPerWeek(BaseModel):
    """A soft per-discipline session target stated at onboarding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    running: int | None = Field(default=None, ge=0, le=14)
    cycling: int | None = Field(default=None, ge=0, le=14)
    swimming: int | None = Field(default=None, ge=0, le=14)


class PersistedMandatoryProfileData(BaseModel):
    birth_year: int
    gender: AthleteGender
    weight_kg: float
    height_cm: float
    timezone: str | None = None
    weekly_availability: ConfirmedWeeklyAvailability | None = None
    equipment_access: tuple[CapabilityAccessItem, ...] = ()
    health_limitations_text: str | None = None
    coaching_style: CoachingStyle | None = None
    desired_sessions_per_week: DesiredSessionsPerWeek | None = None
    training_goal: PersistedTrainingGoalData | None = None
```

(`ConfigDict`/`Field` import already needed; add them to the existing
`from pydantic import BaseModel` line rather than duplicating it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_profile_schemas.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/domain/enums.py app/schemas/profile.py tests/unit/test_profile_schemas.py
git commit -m "feat: add CoachingStyle and DesiredSessionsPerWeek schemas"
```

---

### Task 2: DB columns, migration, repository plumbing

**Files:**
- Modify: `app/db/models.py`
- Create: `alembic/versions/0047_coaching_style_desired_sessions.py`
- Modify: `app/repositories/profiles.py`
- Test: `tests/use_cases/test_mandatory_profile_onboarding.py` (extend)

**Interfaces:**
- Consumes: `CoachingStyle` from Task 1.
- Produces: `AthleteProfile.coaching_style: CoachingStyle | None`,
  `AthleteProfile.desired_sessions_per_week_jsonb: dict[str, object] | None`,
  `AthleteProfileContext.coaching_style: CoachingStyle | None`,
  `AthleteProfileContext.desired_sessions_per_week_jsonb: dict[str, object] | None`,
  `ProfileRepository.update_athlete_profile_context_fields()` now also
  accepts `coaching_style` and `desired_sessions_per_week_jsonb` in its
  payload.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/use_cases/test_mandatory_profile_onboarding.py

from app.domain.enums import CoachingStyle


@pytest.mark.asyncio
async def test_coaching_style_and_desired_sessions_persist_through_context_fields(
    profile_database: async_sessionmaker[AsyncSession],
) -> None:
    identity = _identity()
    async with profile_database.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=identity.telegram_user_id,
            telegram_username=identity.telegram_username,
            first_name=identity.first_name,
        )
        profiles = ProfileRepository(session)
        await profiles.upsert_mandatory_athlete_profile(
            user_id=user.id,
            birth_year=1990,
            gender=AthleteGender.MALE,
            weight_kg=74,
            height_cm=179,
        )
        await profiles.update_athlete_profile_context_fields(
            user_id=user.id,
            payload={
                "coaching_style": CoachingStyle.NORMAL,
                "desired_sessions_per_week_jsonb": {
                    "running": 3,
                    "cycling": 2,
                    "swimming": 3,
                },
            },
        )
        context = await profiles.get_athlete_profile_context(user_id=user.id)

    assert context is not None
    assert context.coaching_style is CoachingStyle.NORMAL
    assert context.desired_sessions_per_week_jsonb == {
        "running": 3,
        "cycling": 2,
        "swimming": 3,
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/use_cases/test_mandatory_profile_onboarding.py::test_coaching_style_and_desired_sessions_persist_through_context_fields -v`
Expected: FAIL with `ValueError: unsupported athlete profile update field`
(the payload keys aren't in the allow-list yet) or an `AttributeError` on
`AthleteProfile.coaching_style` (the column doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

In `app/db/models.py`, in `AthleteProfile` right after
`health_limitations_text`:

```python
    coaching_style: Mapped[CoachingStyle | None] = mapped_column(
        persisted_enum(CoachingStyle, name="coaching_style", length=16),
        nullable=True,
    )
    desired_sessions_per_week_jsonb: Mapped[dict[str, object] | None] = (
        mapped_column(json_document(), nullable=True)
    )
```

Add `CoachingStyle` to the `app.domain.enums` import list at the top of
`models.py`.

New migration `alembic/versions/0047_coaching_style_desired_sessions.py`:

```python
"""Add coaching style and desired sessions per week to athlete profiles.

Revision ID: 0047_coaching_style_desired_sessions
Revises: 0046_structured_availability
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047_coaching_style_desired_sessions"
down_revision: str | None = "0046_structured_availability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ONBOARDING_STEPS = (
    "'CONSENT','SETUP_INTRODUCTION','GOAL_INTAKE','GOAL_SWIMMING_TYPE',"
    "'GOAL_METRIC_INTAKE','GOAL_EVENT_DATE','GOAL_CONFIRMED',"
    "'PROFILE_BIRTH_YEAR_INTAKE','PROFILE_GENDER_INTAKE',"
    "'PROFILE_WEIGHT_INTAKE','PROFILE_HEIGHT_INTAKE','PROFILE_TIMEZONE_INTAKE',"
    "'AVAILABILITY_INTAKE','AVAILABILITY_REVIEW','COACHING_STYLE_INTAKE',"
    "'DESIRED_SESSIONS_INTAKE','EQUIPMENT_RECOMMENDATION',"
    "'EQUIPMENT_INTAKE','HEALTH_LIMITATIONS_INTAKE','BASELINE_INTAKE',"
    "'TRAINING_HISTORY_IMPORT'"
)
_PREVIOUS_ONBOARDING_STEPS = (
    "'CONSENT','SETUP_INTRODUCTION','GOAL_INTAKE','GOAL_SWIMMING_TYPE',"
    "'GOAL_METRIC_INTAKE','GOAL_EVENT_DATE','GOAL_CONFIRMED',"
    "'PROFILE_BIRTH_YEAR_INTAKE','PROFILE_GENDER_INTAKE',"
    "'PROFILE_WEIGHT_INTAKE','PROFILE_HEIGHT_INTAKE','PROFILE_TIMEZONE_INTAKE',"
    "'AVAILABILITY_INTAKE','AVAILABILITY_REVIEW','EQUIPMENT_RECOMMENDATION',"
    "'EQUIPMENT_INTAKE','HEALTH_LIMITATIONS_INTAKE','BASELINE_INTAKE',"
    "'TRAINING_HISTORY_IMPORT'"
)


def _replace_check(table: str, column: str, name: str, values: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(op.f(f"ck_{table}_{name}"), type_="check")
        batch.create_check_constraint(name, f"{column} IN ({values})")


def upgrade() -> None:
    _replace_check(
        "onboarding_sessions", "current_step", "onboarding_step", _ONBOARDING_STEPS
    )
    _replace_check(
        "llm_usage", "onboarding_step", "llm_onboarding_step", _ONBOARDING_STEPS
    )
    with op.batch_alter_table("athlete_profiles") as batch:
        batch.add_column(
            sa.Column("coaching_style", sa.String(length=16), nullable=True)
        )
        batch.create_check_constraint(
            op.f("ck_athlete_profiles_coaching_style"),
            "coaching_style IS NULL OR coaching_style IN "
            "('CONSERVATIVE','NORMAL','DEMANDING')",
        )
        batch.add_column(
            sa.Column("desired_sessions_per_week_jsonb", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    op.execute(
        "UPDATE onboarding_sessions SET current_step = 'AVAILABILITY_REVIEW' "
        "WHERE current_step IN ('COACHING_STYLE_INTAKE', 'DESIRED_SESSIONS_INTAKE')"
    )
    with op.batch_alter_table("athlete_profiles") as batch:
        batch.drop_column("desired_sessions_per_week_jsonb")
        batch.drop_constraint(
            op.f("ck_athlete_profiles_coaching_style"), type_="check"
        )
        batch.drop_column("coaching_style")
    _replace_check(
        "llm_usage",
        "onboarding_step",
        "llm_onboarding_step",
        _PREVIOUS_ONBOARDING_STEPS,
    )
    _replace_check(
        "onboarding_sessions",
        "current_step",
        "onboarding_step",
        _PREVIOUS_ONBOARDING_STEPS,
    )
```

In `app/repositories/profiles.py`:

```python
@dataclass(frozen=True, slots=True)
class AthleteProfileContext:
    weekly_availability_jsonb: dict[str, object] | None
    health_limitations_text: str | None
    coaching_style: CoachingStyle | None
    desired_sessions_per_week_jsonb: dict[str, object] | None
```

```python
    async def get_athlete_profile_context(
        self, *, user_id: uuid.UUID
    ) -> AthleteProfileContext | None:
        profile = await self.get_athlete_profile(user_id=user_id)
        if profile is None:
            return None
        return AthleteProfileContext(
            weekly_availability_jsonb=profile.weekly_availability_jsonb,
            health_limitations_text=profile.health_limitations_text,
            coaching_style=profile.coaching_style,
            desired_sessions_per_week_jsonb=profile.desired_sessions_per_week_jsonb,
        )
```

```python
    async def update_athlete_profile_context_fields(
        self, *, user_id: uuid.UUID, payload: Mapping[str, object]
    ) -> AthleteProfile:
        return await self._update_profile(
            user_id,
            payload,
            {
                "weekly_availability_jsonb",
                "health_limitations_text",
                "coaching_style",
                "desired_sessions_per_week_jsonb",
            },
        )
```

Add `from app.domain.enums import AthleteGender, CoachingStyle` to the
existing import line.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/use_cases/test_mandatory_profile_onboarding.py -v`
Expected: PASS, including the new test and every pre-existing one in the
file (regression check on the shared fixture/model).

Then apply the migration against a real Postgres if one is configured for
this repo's dev environment: `uv run alembic upgrade head` — confirm it
runs clean, then `uv run alembic downgrade -1` to confirm the downgrade
path also runs clean, then `uv run alembic upgrade head` again.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/db/models.py app/repositories/profiles.py \
  alembic/versions/0047_coaching_style_desired_sessions.py \
  tests/use_cases/test_mandatory_profile_onboarding.py
git commit -m "feat: persist coaching_style and desired_sessions_per_week on AthleteProfile"
```

---

### Task 3: Coaching-style onboarding step (callback choice)

**Files:**
- Modify: `app/services/onboarding/service.py`
- Modify: `app/schemas/onboarding_service.py`
- Modify: `app/bot/messages.py`
- Modify: `app/bot/keyboards.py`
- Modify: `app/bot/service.py`
- Test: `tests/use_cases/test_mandatory_profile_onboarding.py` (extend)

**Interfaces:**
- Consumes: `CoachingStyle` (Task 1), `ProfileRepository.update_athlete_profile_context_fields` (Task 2).
- Produces: `OnboardingService.choose_coaching_style(identity, choice: str) -> OnboardingServiceResult`, advancing `current_step` from `COACHING_STYLE_INTAKE` to `DESIRED_SESSIONS_INTAKE` (Task 4 defines what that step needs prepared — Task 3 stops at the transition).

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/use_cases/test_mandatory_profile_onboarding.py

@pytest.mark.asyncio
async def test_choose_coaching_style_persists_and_advances_past_availability_review(
    profile_database: async_sessionmaker[AsyncSession],
) -> None:
    identity = _identity()
    async with profile_database.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=identity.telegram_user_id,
            telegram_username=identity.telegram_username,
            first_name=identity.first_name,
        )
        user.status = UserStatus.ONBOARDING_IN_PROGRESS
        await OnboardingRepository(session).get_or_create(
            user_id=user.id,
            current_step=OnboardingStep.COACHING_STYLE_INTAKE,
        )
        await ProfileRepository(session).upsert_mandatory_athlete_profile(
            user_id=user.id,
            birth_year=1990,
            gender=AthleteGender.MALE,
            weight_kg=74,
            height_cm=179,
        )

    service = OnboardingService(
        session_factory=profile_database, settings=Settings(llm_mode="mock")
    )

    result = await service.choose_coaching_style(identity, "NORMAL")

    assert result.current_step is OnboardingStep.DESIRED_SESSIONS_INTAKE
    async with profile_database() as session:
        profile = await ProfileRepository(session).get_athlete_profile(
            user_id=user.id
        )
    assert profile is not None
    assert profile.coaching_style is CoachingStyle.NORMAL


@pytest.mark.asyncio
async def test_choose_coaching_style_rejects_unknown_value(
    profile_database: async_sessionmaker[AsyncSession],
) -> None:
    identity = _identity()
    async with profile_database.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=identity.telegram_user_id,
            telegram_username=identity.telegram_username,
            first_name=identity.first_name,
        )
        user.status = UserStatus.ONBOARDING_IN_PROGRESS
        await OnboardingRepository(session).get_or_create(
            user_id=user.id,
            current_step=OnboardingStep.COACHING_STYLE_INTAKE,
        )

    service = OnboardingService(
        session_factory=profile_database, settings=Settings(llm_mode="mock")
    )

    with pytest.raises(OnboardingApplicationError):
        await service.choose_coaching_style(identity, "AGGRESSIVE")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/use_cases/test_mandatory_profile_onboarding.py -k coaching_style -v`
Expected: FAIL with `AttributeError: 'OnboardingService' object has no
attribute 'choose_coaching_style'`.

- [ ] **Step 3: Write minimal implementation**

In `app/services/onboarding/service.py`, edit `confirm_availability` (the
method shown in full during design review — currently ends by setting
`current_step=OnboardingStep.EQUIPMENT_RECOMMENDATION`) to instead read:

```python
        onboarding = await OnboardingRepository(session).save_progress(
            user_id=user.id,
            current_step=OnboardingStep.COACHING_STYLE_INTAKE,
            answers=cast(dict[str, object], answers),
        )
    return self._result(user, onboarding)
```

(Drop the trailing `return await self._resume_capability_review(...)` call
from `confirm_availability` — that now happens at the end of Task 4's
`submit_desired_sessions_form`, once `EQUIPMENT_RECOMMENDATION` is actually
reached.)

Add a new method, placed near `choose_gender`:

```python
    async def choose_coaching_style(
        self,
        identity: TelegramIdentity,
        choice: str,
    ) -> OnboardingServiceResult:
        """Persist one deterministic coaching-style callback selection."""

        try:
            coaching_style = CoachingStyle(choice)
        except ValueError as exc:
            raise OnboardingApplicationError("invalid_action") from exc
        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.COACHING_STYLE_INTAKE:
                raise OnboardingApplicationError("stale_action")
            await ProfileRepository(session).update_athlete_profile_context_fields(
                user_id=user.id,
                payload={"coaching_style": coaching_style},
            )
            return await self._start_desired_sessions(
                session=session, user=user, onboarding=onboarding
            )
```

`_start_desired_sessions` is defined in Task 4 — Task 3's test suite will
only go green once Task 4 exists, since the method call above is part of
the same transition. Land Task 3 and Task 4 as one combined commit if
running them back-to-back; keep them as separate plan tasks only because
they have independently reviewable deliverables (style capture vs. session
capture + the availability check).

Add `CoachingStyle` to the `app.domain.enums` import in `service.py`.

In `app/schemas/onboarding_service.py`, add to `OnboardingResultKind`:

```python
    "coaching_style_intake",
    "desired_sessions_intake",
```

In `_result()` (`service.py`), add before the final `else: kind =
"goal_intake"`:

```python
        elif onboarding.current_step is OnboardingStep.COACHING_STYLE_INTAKE:
            kind = "coaching_style_intake"
        elif onboarding.current_step is OnboardingStep.DESIRED_SESSIONS_INTAKE:
            kind = "desired_sessions_intake"
```

In `app/bot/messages.py`, next to `PROFILE_GENDER_INTAKE`:

```python
COACHING_STYLE_INTAKE = (
    "How do you want me to coach you? Conservative, normal, or demanding."
)
```

In `app/bot/keyboards.py`, next to `profile_gender_keyboard`:

```python
def coaching_style_keyboard() -> InlineKeyboardMarkup:
    """Build the deterministic coaching-style choices."""

    return _rows(
        [
            [(LABELS["coaching_style_conservative"], "ob:v1:coaching_style:CONSERVATIVE")],
            [(LABELS["coaching_style_normal"], "ob:v1:coaching_style:NORMAL")],
            [(LABELS["coaching_style_demanding"], "ob:v1:coaching_style:DEMANDING")],
            [(LABELS["cancel"], "ob:v1:cancel")],
        ]
    )
```

Add the three new `coaching_style_*` entries to the `LABELS` dict at the
top of `keyboards.py`, mirroring how `gender_male`/`gender_female` are
defined there.

In `app/bot/service.py`, next to the `ob:v1:profile:gender:` dispatch:

```python
        if callback_data.startswith("ob:v1:coaching_style:"):
            result = await self._onboarding.choose_coaching_style(
                identity, callback_data.removeprefix("ob:v1:coaching_style:")
            )
            return await self._render_onboarding(identity, result)
```

And in `_render_onboarding`, next to the `PROFILE_GENDER_INTAKE` render
branch:

```python
        if result.kind == "coaching_style_intake":
            return TelegramResponse(
                messages.COACHING_STYLE_INTAKE,
                keyboards.coaching_style_keyboard(),
            )
```

- [ ] **Step 4: Run test to verify it passes**

This step's tests only fully pass once Task 4 exists (see note above).
Complete Task 4's Step 3 before running this. Then:

Run: `cd backend && uv run pytest tests/use_cases/test_mandatory_profile_onboarding.py -k coaching_style -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/onboarding/service.py \
  app/schemas/onboarding_service.py app/bot/messages.py app/bot/keyboards.py \
  app/bot/service.py tests/use_cases/test_mandatory_profile_onboarding.py
git commit -m "feat: add coaching-style onboarding step"
```

---

### Task 4: Desired-sessions-per-week form step + availability fit check

**Files:**
- Create: `app/services/onboarding/desired_sessions_form.py`
- Modify: `app/schemas/availability.py`
- Modify: `app/services/onboarding/service.py`
- Modify: `app/schemas/onboarding_service.py`
- Modify: `app/bot/messages.py`
- Test: `tests/unit/test_desired_sessions_form.py` (new), `tests/use_cases/test_mandatory_profile_onboarding.py` (extend)

**Interfaces:**
- Consumes: `Discipline`, `fields_for_disciplines`-style pattern from
  `baseline_form.py` (mirrored, not imported — this form only has 3
  possible fields, one per discipline, no sub-fields), `ConfirmedWeeklyAvailability`
  (Task-independent, already exists).
- Produces: `desired_sessions_form.fields_for_disciplines(disciplines: tuple[Discipline, ...]) -> tuple[str, ...]`,
  `desired_sessions_form.parse_answer(key: str, text: str) -> int`,
  `available_day_count_for_discipline(availability: ConfirmedWeeklyAvailability, discipline: AvailabilityDiscipline) -> int`,
  `OnboardingService._start_desired_sessions(*, session, user, onboarding) -> OnboardingServiceResult`,
  `OnboardingService.submit_desired_sessions_form(identity, values: Mapping[str, object]) -> OnboardingServiceResult`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_desired_sessions_form.py
"""Field registry and parsing for the desired-sessions-per-week form."""

from __future__ import annotations

import pytest

from app.domain.enums import Discipline
from app.services.onboarding.desired_sessions_form import (
    fields_for_disciplines,
    parse_answer,
)


def test_fields_for_disciplines_returns_one_field_per_requested_discipline() -> None:
    fields = fields_for_disciplines(
        (Discipline.RUNNING, Discipline.CYCLING, Discipline.SWIMMING)
    )
    assert fields == ("running", "cycling", "swimming")


def test_fields_for_disciplines_is_ordered_and_filtered() -> None:
    fields = fields_for_disciplines((Discipline.SWIMMING, Discipline.RUNNING))
    assert fields == ("running", "swimming")


def test_parse_answer_accepts_zero_to_fourteen() -> None:
    assert parse_answer(key="running", text="3") == 3
    assert parse_answer(key="swimming", text="0") == 0


def test_parse_answer_rejects_out_of_range_or_non_integer() -> None:
    with pytest.raises(ValueError):
        parse_answer(key="running", text="15")
    with pytest.raises(ValueError):
        parse_answer(key="running", text="three")
```

```python
# tests/unit/test_availability_fit.py
"""Pure availability-fit counting used by the desired-sessions check."""

from __future__ import annotations

from app.schemas.availability import (
    AvailabilityDay,
    AvailabilityWindow,
    ConfirmedWeeklyAvailability,
    available_day_count_for_discipline,
)

_UNAVAILABLE = AvailabilityDay(available=False)


def _swim_day() -> AvailabilityDay:
    return AvailabilityDay(
        available=True,
        disciplines=("swimming",),
        time_windows=(AvailabilityWindow(duration_minutes=45),),
    )


def test_counts_only_days_that_list_the_discipline() -> None:
    availability = ConfirmedWeeklyAvailability(
        days={
            "monday": _swim_day(),
            "tuesday": _UNAVAILABLE,
            "wednesday": _swim_day(),
            "thursday": _UNAVAILABLE,
            "friday": _UNAVAILABLE,
            "saturday": _UNAVAILABLE,
            "sunday": _UNAVAILABLE,
        }
    )
    assert available_day_count_for_discipline(availability, "swimming") == 2
    assert available_day_count_for_discipline(availability, "running") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_desired_sessions_form.py tests/unit/test_availability_fit.py -v`
Expected: FAIL with `ModuleNotFoundError:
app.services.onboarding.desired_sessions_form` and
`ImportError: cannot import name 'available_day_count_for_discipline'`.

- [ ] **Step 3: Write minimal implementation**

New file `app/services/onboarding/desired_sessions_form.py`:

```python
"""Deterministic question registry and parsing for desired sessions per week."""

from __future__ import annotations

from app.domain.enums import Discipline

_DISPLAY_DISCIPLINE_ORDER = (
    Discipline.RUNNING,
    Discipline.CYCLING,
    Discipline.SWIMMING,
)
_FIELD_BY_DISCIPLINE = {
    Discipline.RUNNING: "running",
    Discipline.CYCLING: "cycling",
    Discipline.SWIMMING: "swimming",
}


def fields_for_disciplines(disciplines: tuple[Discipline, ...]) -> tuple[str, ...]:
    """Return the soft-target field required for the athlete's active goal."""

    selected = set(disciplines)
    return tuple(
        _FIELD_BY_DISCIPLINE[discipline]
        for discipline in _DISPLAY_DISCIPLINE_ORDER
        if discipline in selected
    )


def parse_answer(*, key: str, text: str) -> int:
    """Convert one desired-sessions answer to a validated integer 0 to 14."""

    if key not in _FIELD_BY_DISCIPLINE.values():
        raise ValueError("unknown desired-sessions field")
    value = text.strip()
    if not value.isdigit():
        raise ValueError("invalid integer")
    parsed = int(value)
    if not 0 <= parsed <= 14:
        raise ValueError("integer out of range")
    return parsed
```

In `app/schemas/availability.py`, add:

```python
def available_day_count_for_discipline(
    availability: ConfirmedWeeklyAvailability, discipline: AvailabilityDiscipline
) -> int:
    """Count confirmed days that list this discipline as available."""

    return sum(
        1
        for day in availability.days.values()
        if day.available and discipline in day.disciplines
    )
```

In `app/services/onboarding/service.py`, add `_start_desired_sessions`
(called from Task 3's `choose_coaching_style`, so it must already be
present when Task 3's Step 4 runs):

```python
    async def _start_desired_sessions(
        self,
        *,
        session: AsyncSession,
        user: User,
        onboarding: OnboardingSession,
    ) -> OnboardingServiceResult:
        """Prepare the soft session-count form for the athlete's disciplines."""

        goal = await ProfileRepository(session).get_training_goal(user_id=user.id)
        if goal is None or goal.goal_template_id is None:
            raise OnboardingApplicationError("stale_action")
        expected_roles = {goal.goal_template_id: GoalContextRole.TARGET}
        if goal.supporting_goal_template_id is not None:
            expected_roles[goal.supporting_goal_template_id] = (
                GoalContextRole.SUPPORTING
            )
        catalog = TrainingCatalogRepository(session)
        rows = await catalog.contexts_for_goals(goal_template_ids=expected_roles.keys())
        disciplines = tuple(
            sorted(
                {
                    context.discipline
                    for relation, context in rows
                    if expected_roles.get(relation.goal_template_id) is relation.role
                    and context.discipline
                    in {Discipline.RUNNING, Discipline.CYCLING, Discipline.SWIMMING}
                },
                key=lambda item: item.value,
            )
        )
        fields = desired_sessions_form.fields_for_disciplines(disciplines)
        if not fields:
            raise OnboardingApplicationError("desired_sessions_not_supported")
        answers = self._answers(onboarding)
        answers[_DESIRED_SESSIONS_FIELDS_KEY] = list(fields)
        onboarding = await OnboardingRepository(session).save_progress(
            user_id=user.id,
            current_step=OnboardingStep.DESIRED_SESSIONS_INTAKE,
            answers=cast(dict[str, object], answers),
        )
        return self._result(user, onboarding)
```

Add `_DESIRED_SESSIONS_FIELDS_KEY = "desired_sessions_fields"` near
`_BASELINE_FIELDS_KEY`, and `from app.services.onboarding import
desired_sessions_form` (module import, matching how `baseline_form` is
already imported) to the top of `service.py`.

Add `submit_desired_sessions_form`, placed near `submit_baseline_form`:

```python
    async def submit_desired_sessions_form(
        self, identity: TelegramIdentity, values: Mapping[str, object]
    ) -> OnboardingServiceResult:
        """Validate desired sessions, check them against confirmed availability."""

        async with self._session_factory.begin() as session:
            user, onboarding = await self._locked_state(session, identity)
            self._require_active(onboarding)
            if onboarding.current_step is not OnboardingStep.DESIRED_SESSIONS_INTAKE:
                raise OnboardingApplicationError("stale_action")
            answers = self._answers(onboarding)
            fields = answers.get(_DESIRED_SESSIONS_FIELDS_KEY)
            if not isinstance(fields, list):
                raise OnboardingApplicationError("stale_action")
            parsed: dict[str, int] = {}
            invalid: list[str] = []
            for field in fields:
                if not isinstance(field, str):
                    raise OnboardingApplicationError("stale_action")
                raw = values.get(field)
                text = raw if isinstance(raw, str) else ""
                try:
                    parsed[field] = desired_sessions_form.parse_answer(
                        key=field, text=text
                    )
                except ValueError:
                    invalid.append(field)
            if invalid:
                return self._result(
                    user,
                    onboarding,
                    kind="desired_sessions_validation_error",
                    error_code=invalid[0],
                )
            profile = await ProfileRepository(session).get_athlete_profile_context(
                user_id=user.id
            )
            availability = (
                ConfirmedWeeklyAvailability.model_validate(
                    profile.weekly_availability_jsonb
                )
                if profile is not None and profile.weekly_availability_jsonb
                else None
            )
            if availability is not None:
                shortfalls = {
                    field: count
                    for field, desired in parsed.items()
                    if desired > 0
                    and (
                        count := available_day_count_for_discipline(
                            availability, field  # type: ignore[arg-type]
                        )
                    )
                    < desired
                }
                if shortfalls:
                    answers["desired_sessions_shortfalls"] = cast(
                        JsonValue, shortfalls
                    )
                    return self._result(
                        user,
                        onboarding,
                        kind="desired_sessions_availability_conflict",
                    )
            await ProfileRepository(session).update_athlete_profile_context_fields(
                user_id=user.id,
                payload={"desired_sessions_per_week_jsonb": parsed},
            )
            onboarding = await OnboardingRepository(session).save_progress(
                user_id=user.id,
                current_step=OnboardingStep.EQUIPMENT_RECOMMENDATION,
                answers=cast(dict[str, object], answers),
            )
        return await self._resume_capability_review(
            identity=identity, user_id=user.id
        )
```

Add `from app.schemas.availability import available_day_count_for_discipline`
alongside the existing `ConfirmedWeeklyAvailability` import in `service.py`.

In `app/schemas/onboarding_service.py`, add to `OnboardingResultKind`:

```python
    "desired_sessions_validation_error",
    "desired_sessions_availability_conflict",
```

In `app/bot/messages.py`:

```python
DESIRED_SESSIONS_INTAKE = (
    "How many sessions per week would you like for each? Send a number "
    "0 to 14 for each discipline I ask about."
)
DESIRED_SESSIONS_AVAILABILITY_CONFLICT = (
    "That's more sessions than your confirmed availability allows for at "
    "least one discipline. Adjust the desired count, or go back and open "
    "more availability first."
)
```

Wire the two new result kinds into `_render_onboarding` in
`app/bot/service.py`, mirroring the existing
`"baseline_validation_error"` branch for the validation-error kind, and a
new plain-message branch (no keyboard beyond retry) for the
availability-conflict kind, using `messages.DESIRED_SESSIONS_AVAILABILITY_CONFLICT`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_desired_sessions_form.py tests/unit/test_availability_fit.py -v`
Expected: PASS (6 tests)

Then re-run Task 3's tests, which depend on this task's
`_start_desired_sessions`:

Run: `cd backend && uv run pytest tests/use_cases/test_mandatory_profile_onboarding.py -k "coaching_style or desired_sessions" -v`
Expected: PASS

Add and run the availability-conflict end-to-end case:

```python
# Append to tests/use_cases/test_mandatory_profile_onboarding.py

@pytest.mark.asyncio
async def test_desired_sessions_rejects_a_count_the_availability_cannot_hold(
    profile_database: async_sessionmaker[AsyncSession],
) -> None:
    identity = _identity()
    async with profile_database.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=identity.telegram_user_id,
            telegram_username=identity.telegram_username,
            first_name=identity.first_name,
        )
        user.status = UserStatus.ONBOARDING_IN_PROGRESS
        await OnboardingRepository(session).get_or_create(
            user_id=user.id,
            current_step=OnboardingStep.DESIRED_SESSIONS_INTAKE,
            answers={"desired_sessions_fields": ["swimming"]},
        )
        await ProfileRepository(session).upsert_mandatory_athlete_profile(
            user_id=user.id,
            birth_year=1990,
            gender=AthleteGender.MALE,
            weight_kg=74,
            height_cm=179,
        )
        await ProfileRepository(session).update_athlete_profile_context_fields(
            user_id=user.id,
            payload={
                "weekly_availability_jsonb": ConfirmedWeeklyAvailability(
                    days={
                        "monday": AvailabilityDay(
                            available=True,
                            disciplines=("swimming",),
                            time_windows=(AvailabilityWindow(duration_minutes=45),),
                        ),
                        **{
                            day: AvailabilityDay(available=False)
                            for day in (
                                "tuesday",
                                "wednesday",
                                "thursday",
                                "friday",
                                "saturday",
                                "sunday",
                            )
                        },
                    }
                ).model_dump(mode="json")
            },
        )

    service = OnboardingService(
        session_factory=profile_database, settings=Settings(llm_mode="mock")
    )

    result = await service.submit_desired_sessions_form(identity, {"swimming": "3"})

    assert result.kind == "desired_sessions_availability_conflict"
    async with profile_database() as session:
        profile = await ProfileRepository(session).get_athlete_profile_context(
            user_id=user.id
        )
    assert profile is not None
    assert profile.desired_sessions_per_week_jsonb is None
```

(Add `AvailabilityDay`, `AvailabilityWindow`, `ConfirmedWeeklyAvailability`
to the test file's imports from `app.schemas.availability`.)

Run: `cd backend && uv run pytest tests/use_cases/test_mandatory_profile_onboarding.py -v`
Expected: PASS, full file, no regressions.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/onboarding/desired_sessions_form.py \
  app/schemas/availability.py app/services/onboarding/service.py \
  app/schemas/onboarding_service.py app/bot/messages.py app/bot/service.py \
  tests/unit/test_desired_sessions_form.py tests/unit/test_availability_fit.py \
  tests/use_cases/test_mandatory_profile_onboarding.py
git commit -m "feat: add desired-sessions-per-week step with availability fit check"
```

---

### Task 5: Thread both fields into the Stage 1 planner prompt

**Files:**
- Modify: `app/services/weekly_planning/service.py`
- Modify: `app/workflows/prompts/weekly_planning.py`
- Test: `tests/unit/test_weekly_planning_prompt.py` (extend)

**Interfaces:**
- Consumes: `AthleteProfileContext.coaching_style`,
  `AthleteProfileContext.desired_sessions_per_week_jsonb` (Task 2).
- Produces: `prompt_context["coaching_style"]: str | None`,
  `prompt_context["desired_sessions_per_week"]: dict[str, object] | None`,
  `WEEKLY_PLANNER_PROMPT_VERSION == 6`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/unit/test_weekly_planning_prompt.py

def test_prompt_context_carries_coaching_style_and_desired_sessions() -> None:
    context = {
        "week_start": "2026-09-07",
        "evidence_state": {"running": "SELF_REPORTED"},
        "coaching_style": "NORMAL",
        "desired_sessions_per_week": {"running": 3, "cycling": 2, "swimming": 3},
    }
    messages = build_weekly_planner_messages(context)
    human = messages[1].content
    assert '"coaching_style":"NORMAL"' in human
    assert '"desired_sessions_per_week"' in human


def test_system_prompt_gives_coaching_style_guidance_without_a_numeric_ceiling() -> None:
    system_text = build_weekly_planner_messages(
        {"week_start": "2026-09-07", "evidence_state": {}}
    )[0].content
    assert "coaching_style" in system_text
    assert "%" not in system_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_weekly_planning_prompt.py -v`
Expected: The first test passes already (the function just serializes
whatever dict it's given), but the second fails with `AssertionError`
since the system prompt has no `coaching_style` guidance yet — confirming
the test actually exercises new behavior, not the existing pass-through.

- [ ] **Step 3: Write minimal implementation**

In `app/workflows/prompts/weekly_planning.py`, bump the version and extend
the system prompt:

```python
WEEKLY_PLANNER_PROMPT_VERSION: Final = 6
```

Append to `_WEEKLY_PLANNER_SYSTEM_PROMPT`, after the existing
`evidence_state` paragraph and before the `target_contexts` paragraph:

```python

coaching_style is the athlete's own chosen intensity: CONSERVATIVE, NORMAL,
or DEMANDING. It never overrides the evidence_state rules above — a
SELF_REPORTED or NONE discipline still gets no HARD intensity and no exact
pace or power at any coaching_style. Within that floor, coaching_style sets
how assertive to be: for a THIN, SELF_REPORTED, or NONE discipline,
CONSERVATIVE means introduce it very gradually and stay well under any
stated typical volume; NORMAL means introduce it steadily, close to but
not exceeding stated typical volume; DEMANDING means introduce it
steadily and slightly faster, up to stated typical volume. desired_sessions_per_week
is a soft target the athlete asked for at onboarding, per discipline — aim
for it when evidence and availability allow, but never treat it as a
requirement you must hit exactly."""
```

In `app/services/weekly_planning/service.py`, inside `_prepare()`, after
the `profile = await profiles.get_athlete_profile_context(...)` line
already there:

```python
            prompt_context = {
                ...  # existing keys unchanged
                "coaching_style": (
                    profile.coaching_style.value
                    if profile is not None and profile.coaching_style is not None
                    else None
                ),
                "desired_sessions_per_week": (
                    profile.desired_sessions_per_week_jsonb
                    if profile is not None
                    else None
                ),
            }
```

(Insert these two keys into the existing dict literal alongside
`"health_limitations_text"` — do not create a second dict.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_weekly_planning_prompt.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/workflows/prompts/weekly_planning.py \
  app/services/weekly_planning/service.py tests/unit/test_weekly_planning_prompt.py
git commit -m "feat: thread coaching_style and desired_sessions_per_week into the Stage 1 prompt"
```

---

### Task 6: End-to-end regression pass

**Files:**
- Test only, no production code changes: `tests/use_cases/test_mandatory_profile_onboarding.py`, `tests/unit/test_weekly_planning_prompt.py`, `tests/bot/test_context_onboarding_service.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: nothing new — this is a verification task.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/use_cases/test_mandatory_profile_onboarding.py

@pytest.mark.asyncio
async def test_full_onboarding_path_from_availability_review_through_equipment(
    profile_database: async_sessionmaker[AsyncSession],
) -> None:
    """Availability confirm -> coaching style -> desired sessions -> equipment."""

    identity = _identity()
    async with profile_database.begin() as session:
        user, _ = await UserRepository(session).get_or_create(
            telegram_user_id=identity.telegram_user_id,
            telegram_username=identity.telegram_username,
            first_name=identity.first_name,
        )
        user.status = UserStatus.ONBOARDING_IN_PROGRESS
        await OnboardingRepository(session).get_or_create(
            user_id=user.id,
            current_step=OnboardingStep.AVAILABILITY_REVIEW,
            answers={
                "availability_draft": {
                    "days": {
                        day: {"available": False}
                        for day in (
                            "monday", "tuesday", "wednesday", "thursday",
                            "friday", "saturday",
                        )
                    }
                    | {
                        "sunday": {
                            "available": True,
                            "disciplines": ["running"],
                            "time_windows": [{"duration_minutes": 40}],
                        }
                    }
                }
            },
        )
        await ProfileRepository(session).upsert_mandatory_athlete_profile(
            user_id=user.id,
            birth_year=1990,
            gender=AthleteGender.MALE,
            weight_kg=74,
            height_cm=179,
        )
        await ProfileRepository(session).upsert_training_goal(
            user_id=user.id,
            main_goal="10K race",
            event_date=None,
            secondary_priority=None,
            goal_template_id=catalog_id("goal", "RUNNING_10K"),
        )

    service = OnboardingService(
        session_factory=profile_database, settings=Settings(llm_mode="mock")
    )

    after_availability = await service.confirm_availability(identity)
    assert after_availability.current_step is OnboardingStep.COACHING_STYLE_INTAKE

    after_style = await service.choose_coaching_style(identity, "CONSERVATIVE")
    assert after_style.current_step is OnboardingStep.DESIRED_SESSIONS_INTAKE

    after_sessions = await service.submit_desired_sessions_form(
        identity, {"running": "1"}
    )
    assert after_sessions.current_step is OnboardingStep.EQUIPMENT_RECOMMENDATION

    async with profile_database() as session:
        context = await ProfileRepository(session).get_athlete_profile_context(
            user_id=user.id
        )
    assert context is not None
    assert context.coaching_style is CoachingStyle.CONSERVATIVE
    assert context.desired_sessions_per_week_jsonb == {"running": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/use_cases/test_mandatory_profile_onboarding.py::test_full_onboarding_path_from_availability_review_through_equipment -v`
Expected: FAIL at whichever assertion the current state of Tasks 1–5 hasn't
completed yet — this test is the integration check that the four
individually-tested tasks actually compose. If Tasks 1–5 are complete, it
should already pass; this step exists to catch anything the per-task tests
didn't (e.g. `EQUIPMENT_RECOMMENDATION` render/dispatch expecting the
result of `_resume_capability_review`).

- [ ] **Step 3: Fix whatever composition gap the failure points at**

No new code is pre-written for this step, because its entire purpose is to
surface an integration gap the earlier tasks' unit-level tests couldn't
see. Fix inline; do not add a workaround in `service.py` if the actual gap
is in an earlier task's design (per the design-review skill's "revisit
earlier steps" rule) — go back and correct that task.

- [ ] **Step 4: Run the full backend suite**

Run: `cd backend && uv run pytest -v`
Expected: PASS, zero regressions anywhere in the suite (bot layer, use
cases, unit).

- [ ] **Step 5: Commit**

```bash
cd backend && git add -A
git commit -m "test: end-to-end coverage for coaching-style + desired-sessions onboarding"
```

---

## Self-Review

**Spec coverage:**
- "Desired sessions per week, per discipline — a soft target" → Task 1
  (schema), Task 4 (capture + persistence, kept explicitly soft in the
  prompt guidance in Task 5).
- "Checked on the spot against his stated availability" → Task 4's
  `submit_desired_sessions_form` shortfall check.
- "Coaching style: conservative, normal, or demanding" → Task 1 (enum),
  Task 3 (capture).
- "Coaching style... and desired sessions per discipline — two separate
  fields on the athlete's profile... editable later" → placement matches
  (`AthleteProfile`); "editable later" is explicitly out of scope per
  Global Constraints, flagged rather than silently built.
- Coaching-style table's "introducing a new sport" row → Task 5's system
  prompt paragraph is the only row of that table Stage 1 can act on (no
  prior week exists yet for the jump-% or deload-cadence rows), which
  matches the design-review conversation's conclusion.

**Placeholder scan:** no `TBD`/`TODO`/"handle edge cases" strings in any
task; every step has literal code or a literal shell command.

**Type consistency:** `CoachingStyle` (Task 1) is the same enum used in
`AthleteProfile.coaching_style` (Task 2), `choose_coaching_style`'s
parameter (Task 3), and `AthleteProfileContext.coaching_style` (Task 2) /
`prompt_context["coaching_style"]` (Task 5, read via `.value`, matching
the existing `evidence_state` dict's `.value` pattern in `service.py`).
`desired_sessions_form.fields_for_disciplines` (Task 4) returns the same
three field names (`running`/`cycling`/`swimming`) that
`DesiredSessionsPerWeek` (Task 1) and `available_day_count_for_discipline`
(Task 4) expect.

**Open item carried forward, not resolved here:** `_plan_fits_availability`
in `weekly_planning/service.py` and the new
`available_day_count_for_discipline` check independently reimplement
"does this discipline fit this day's availability." Task 4 adds the new
function without refactoring the older one — flagged as a reasonable
future cleanup, not bundled into this plan since it touches Stage 4/5
code this plan doesn't otherwise need to change.
