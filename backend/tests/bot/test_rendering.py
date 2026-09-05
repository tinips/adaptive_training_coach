"""Telegram presentation tests for the retained onboarding and import flow."""

from __future__ import annotations

import uuid
from datetime import date

from telegram import InlineKeyboardMarkup, ReplyKeyboardMarkup

from app.bot import keyboards, messages
from app.domain.enums import (
    CapabilityImportance,
    CapabilityKind,
    ContextAssessmentStatus,
    Discipline,
    ExecutionOptionRole,
    GoalContextRole,
    ProfileSettingsStep,
)
from app.schemas.capabilities import (
    CapabilityOption,
    CapabilityReview,
    CapabilityReviewContext,
    ContextExecutionAssessment,
    GoalExecutionAssessment,
)
from app.schemas.weekly_plans import FirstWeekPlan, PlanSession


def _weekly_availability() -> dict[str, object]:
    days: dict[str, object] = {
        day: {"available": False, "disciplines": [], "time_windows": []}
        for day in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
    }
    days["saturday"] = {
        "available": True,
        "disciplines": ["running", "cycling", "swimming", "strength_training"],
        "time_windows": [{"time_of_day": "morning", "duration_minutes": 120}],
    }
    return {"schema_version": 2, "status": "confirmed", "days": days}


def _button_pairs(markup: InlineKeyboardMarkup) -> list[tuple[str, str]]:
    return [
        (button.text, button.callback_data)
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data is not None
    ]


def test_first_week_menu_renders_utf8_glyphs_and_fallback_note() -> None:
    plan = FirstWeekPlan(
        week_start=date(2026, 9, 7),
        sessions=(
            PlanSession(
                discipline="RUNNING",
                purpose="Easy aerobic run.",
                intensity={
                    "metric": "RPE",
                    "target_range": [2, 3],
                    "rpe_range": [2, 3],
                    "guidance": "Conversational.",
                },
                objective="Finish relaxed.",
                targets={"duration_minutes": 30},
                execution="Keep breathing easy.",
            ),
        ),
        guardrails=("Keep it comfortable.",),
        logging_instructions=("Log RPE.",),
        sessions_per_discipline={"RUNNING": 1},
        total_minutes_per_discipline={"RUNNING": 30},
    )

    rendered = messages.first_week_menu(plan, generation_source="fallback")

    assert "—" in rendered
    assert "• Keep it comfortable." in rendered
    assert "• Log RPE." in rendered
    assert "safe fallback menu" in rendered
    assert "No fixed schedule" in rendered
    assert "Purpose: Easy aerobic run." in rendered
    assert "Finish relaxed." not in rendered
    assert "Keep breathing easy." not in rendered


def test_first_week_menu_splits_over_limit_at_sessions_and_sections() -> None:
    sessions = tuple(
        PlanSession(
            discipline="RUNNING",
            purpose=f"Controlled session {number}: " + "purpose " * 12,
            intensity={
                "metric": "RPE",
                "target_range": [3, 5],
                "rpe_range": [3, 5],
                "guidance": "Steady and controlled. " * 9,
            },
            objective="Build useful baseline signal. " * 6,
            targets={"duration_minutes": 45},
            execution="Stay in control throughout. " * 12,
        )
        for number in range(1, 9)
    )
    plan = FirstWeekPlan(
        week_start=date(2026, 9, 7),
        sessions=sessions,
        guardrails=tuple(
            f"Guardrail {number}: " + "keep this detail in the same section " * 7
            for number in range(1, 13)
        ),
        logging_instructions=tuple(
            f"Metric {number}: " + "log this unique detail after the session " * 7
            for number in range(1, 9)
        ),
        sessions_per_discipline={"RUNNING": 8},
        total_minutes_per_discipline={"RUNNING": 360},
    )

    chunks = messages.first_week_menu_messages(plan)

    assert len(chunks) > 1
    assert all(len(chunk) <= messages.TELEGRAM_CHUNK_LIMIT for chunk in chunks)
    assert chunks[0].startswith("Your first-week training menu (2026-09-07)")
    assert all("Your first-week training menu" not in chunk for chunk in chunks[1:])
    assert all("\u2014" in chunk or "\u2022" in chunk for chunk in chunks)
    for number in range(1, 9):
        marker = f"<b>{number}. Running</b>"
        assert sum(marker in chunk for chunk in chunks) == 1
    guardrails_chunk = next(
        chunk for chunk in chunks if "Placement guardrails" in chunk
    )
    logging_chunk = next(chunk for chunk in chunks if "What to log" in chunk)
    assert "\u2022 Guardrail 1:" in guardrails_chunk
    assert "\u2022 Guardrail 12:" in guardrails_chunk
    assert "\u2022 Metric 1:" in logging_chunk
    assert "\u2022 Metric 8:" in logging_chunk


def test_first_week_menu_compacts_metric_cards_and_deduplicates_logging() -> None:
    plan = FirstWeekPlan(
        week_start=date(2026, 9, 7),
        sessions=(
            PlanSession(
                discipline="RUNNING",
                purpose="Calibrate easy aerobic feel.",
                intensity={
                    "metric": "PACE_SECONDS_PER_KM",
                    "target_range": [330, 375],
                    "rpe_range": [3, 4],
                    "guidance": "Long guidance stays in the saved session.",
                },
                objective="This redundant objective is hidden.",
                targets={"duration_minutes": 45},
                execution="This long execution paragraph is hidden from the menu.",
            ),
        ),
        guardrails=("Keep it comfortable.",),
        logging_instructions=(
            "Log duration and RPE.",
            "Record time and perceived effort.",
            "Log any pain or unusual fatigue.",
            "Log the actual day and time.",
            "Record actual day and time completed.",
        ),
        sessions_per_discipline={"RUNNING": 1},
        total_minutes_per_discipline={"RUNNING": 45},
    )

    rendered = messages.first_week_menu(plan)

    assert "<b>1. Running</b> · 45 min · Easy (RPE 3-4, 5:30-6:15/km)" in rendered
    assert "Purpose: Calibrate easy aerobic feel." in rendered
    assert "…" not in rendered
    assert "redundant objective" not in rendered
    assert "long execution paragraph" not in rendered
    assert rendered.count("Log duration and RPE.") == 1
    assert "Record time and perceived effort." not in rendered
    assert "Log any pain or unusual fatigue." in rendered
    assert "Log the actual day and time." in rendered
    assert "Record actual day and time completed." not in rendered


def test_first_week_menu_interleaves_disciplines() -> None:
    running = PlanSession(
        discipline="RUNNING",
        purpose="Build easy running familiarity.",
        intensity={
            "metric": "RPE",
            "target_range": [2, 3],
            "rpe_range": [2, 3],
            "guidance": "Easy.",
        },
        objective="Run easily.",
        targets={"duration_minutes": 30},
        execution="Keep it easy.",
    )
    cycling = running.model_copy(update={"discipline": Discipline.CYCLING})

    interleaved = messages._interleaved_menu_sessions(
        (running, running, cycling, cycling)
    )

    assert [session.discipline for session in interleaved] == [
        Discipline.RUNNING,
        Discipline.CYCLING,
        Discipline.RUNNING,
        Discipline.CYCLING,
    ]


def test_onboarding_keyboards_expose_only_supported_actions() -> None:
    assert _button_pairs(keyboards.welcome_keyboard()) == [
        ("Let's go", "nav:v1:consent"),
        ("How can this coach help me?", "nav:v1:help"),
        ("Privacy & safety", "nav:v1:privacy"),
    ]
    assert _button_pairs(keyboards.profile_gender_keyboard()) == [
        ("Male", "ob:v1:profile:gender:MALE"),
        ("Female", "ob:v1:profile:gender:FEMALE"),
        ("Cancel", "ob:v1:cancel"),
    ]
    assert _button_pairs(keyboards.profile_settings_text_keyboard()) == [
        ("Done", "ps:v1:done"),
    ]


def test_goal_menu_keyboards_expose_only_supported_actions() -> None:
    assert _button_pairs(keyboards.goal_sport_keyboard(["RUNNING", "TRIATHLON"])) == [
        ("Running", "ob:v1:goal:sport:RUNNING"),
        ("Triathlon", "ob:v1:goal:sport:TRIATHLON"),
        ("Cancel", "ob:v1:cancel"),
    ]
    assert _button_pairs(
        keyboards.goal_template_keyboard(
            [("MARATHON", "Marathon"), ("HALF_MARATHON", "Half marathon")]
        )
    ) == [
        ("Marathon", "ob:v1:goal:template:MARATHON"),
        ("Half marathon", "ob:v1:goal:template:HALF_MARATHON"),
        ("Back", "ob:v1:goal:back"),
        ("Cancel", "ob:v1:cancel"),
    ]
    assert _button_pairs(
        keyboards.supporting_goal_keyboard(
            [("STRENGTH_MAINTENANCE", "Maintain strength")]
        )
    ) == [
        ("Maintain strength", "ob:v1:support:STRENGTH_MAINTENANCE"),
        ("No supporting goal", "ob:v1:support:none"),
        ("Cancel", "ob:v1:cancel"),
    ]


def test_profile_settings_goal_menu_keyboards_expose_only_supported_actions() -> None:
    """Mirrors the onboarding goal menu, with its own ps:v1: callback prefix."""

    assert _button_pairs(
        keyboards.profile_goal_sport_keyboard(["RUNNING", "TRIATHLON"])
    ) == [
        ("Running", "ps:v1:goal:sport:RUNNING"),
        ("Triathlon", "ps:v1:goal:sport:TRIATHLON"),
        ("Back", "ps:v1:goal:back"),
    ]
    assert _button_pairs(
        keyboards.profile_goal_template_keyboard(
            [("MARATHON", "Marathon"), ("HALF_MARATHON", "Half marathon")]
        )
    ) == [
        ("Marathon", "ps:v1:goal:template:MARATHON"),
        ("Half marathon", "ps:v1:goal:template:HALF_MARATHON"),
        ("Back", "ps:v1:goal:main:back"),
    ]
    assert _button_pairs(
        keyboards.profile_supporting_goal_keyboard(
            [("STRENGTH_MAINTENANCE", "Maintain strength")]
        )
    ) == [
        ("Maintain strength", "ps:v1:goal:support:STRENGTH_MAINTENANCE"),
        ("No supporting goal", "ps:v1:goal:support:none"),
        ("Back", "ps:v1:goal:back"),
    ]


def test_callback_values_fit_telegram_limit() -> None:
    samples = [
        keyboards.welcome_keyboard(),
        keyboards.information_keyboard(),
        keyboards.consent_keyboard(),
        keyboards.setup_introduction_keyboard(),
        keyboards.goal_sport_keyboard(["RUNNING", "CYCLING", "SWIMMING", "TRIATHLON"]),
        keyboards.goal_template_keyboard(
            [("TRIATHLON_FULL_DISTANCE", "Full-distance triathlon")]
        ),
        keyboards.supporting_goal_keyboard(
            [("STRENGTH_MAINTENANCE", "Maintain strength")]
        ),
        keyboards.profile_goal_sport_keyboard(
            ["RUNNING", "CYCLING", "SWIMMING", "TRIATHLON"]
        ),
        keyboards.profile_goal_template_keyboard(
            [("TRIATHLON_FULL_DISTANCE", "Full-distance triathlon")]
        ),
        keyboards.profile_supporting_goal_keyboard(
            [("STRENGTH_MAINTENANCE", "Maintain strength")]
        ),
        keyboards.profile_gender_keyboard(),
        keyboards.equipment_intake_keyboard(),
        keyboards.health_limitations_keyboard(),
        keyboards.profile_discard_changes_keyboard(),
    ]
    callbacks = [
        callback for markup in samples for _, callback in _button_pairs(markup)
    ]
    assert callbacks
    assert max(len(value.encode("utf-8")) for value in callbacks) <= 64


def test_lifecycle_reply_keyboards_expose_exact_account_actions() -> None:
    start = keyboards.start_keyboard()
    onboarding = keyboards.onboarding_keyboard()
    completed = keyboards.completed_onboarding_keyboard()

    assert isinstance(start, ReplyKeyboardMarkup)
    assert [[button.text for button in row] for row in start.keyboard] == [["Start"]]
    assert [[button.text for button in row] for row in onboarding.keyboard] == [
        ["Resume"],
        ["Delete"],
    ]
    assert [[button.text for button in row] for row in completed.keyboard] == [
        ["Profile", "Change profile"],
        ["Start your first week"],
        ["Delete"],
    ]
    completed_with_plan = keyboards.completed_onboarding_keyboard(plan_available=True)
    assert [
        [button.text for button in row] for row in completed_with_plan.keyboard
    ] == [
        ["Profile", "Change profile"],
        ["View weekly plan"],
        ["Delete weekly plan"],
        ["Delete"],
    ]
    assert all(
        keyboard.is_persistent is True for keyboard in (start, onboarding, completed)
    )


def test_completed_keyboard_adds_history_mini_app_for_configured_public_url() -> None:
    completed = keyboards.completed_onboarding_keyboard(
        workout_history_url="https://coach.example/webapp/workout-history"
    )

    history = completed.keyboard[1][0]
    assert history.text == "Workout history"
    assert history.web_app is not None
    assert history.web_app.url == "https://coach.example/webapp/workout-history"


def test_equipment_messages_render_escaped_bounded_tables() -> None:
    item_id = uuid.uuid4()
    review = CapabilityReview(
        contexts=(
            CapabilityReviewContext(
                code="cycling_road",
                display_name="Road cycling",
                role=GoalContextRole.TARGET,
            ),
        ),
        options=(
            CapabilityOption(
                id=item_id,
                code="bike",
                display_name="Bike <all-purpose>",
                kind=CapabilityKind.EQUIPMENT,
                importance=CapabilityImportance.REQUIRED,
                execution_roles=(
                    ExecutionOptionRole.PREFERRED,
                    ExecutionOptionRole.SUBSTITUTE,
                ),
                target_context_codes=("cycling_road",),
                selected=True,
            ),
        ),
    )
    summary = GoalExecutionAssessment(
        contexts=(
            ContextExecutionAssessment(
                target_context="cycling_road",
                target_display_name="Road cycling",
                status=ContextAssessmentStatus.LIMITED,
                missing_required=("Bike <all-purpose>",),
                missing_recommended=("Road bike",),
            ),
        ),
    )

    review_message = messages.equipment_review(review)
    summary_message = messages.equipment_summary(summary)

    assert "<pre>" in review_message
    assert "Have" in review_message
    assert "Resource" in review_message
    assert "Bike &lt;all-purpose&gt;" in review_message
    assert "Limited" in summary_message
    assert "Bike &lt;all-purpose&gt;" in summary_message
    assert len(review_message) <= messages.TELEGRAM_MESSAGE_LIMIT
    assert len(summary_message) <= messages.TELEGRAM_MESSAGE_LIMIT


def test_profile_setting_current_values_are_readable_escaped_and_bounded() -> None:
    health = "<pain & stiffness> " * 500
    rendered = messages.profile_setting_prompt(
        ProfileSettingsStep.HEALTH,
        health,
        messages.PROFILE_HEALTH,
    )
    missing_date = messages.profile_setting_prompt(
        ProfileSettingsStep.GOAL_DATE,
        None,
        messages.PROFILE_GOAL_DATE,
    )
    weight = messages.profile_setting_prompt(
        ProfileSettingsStep.PERSONAL_WEIGHT,
        62.5,
        messages.PROFILE_WEIGHT,
    )

    assert "&lt;pain &amp; stiffness&gt;" in rendered
    assert "[truncated for Telegram]" in rendered
    assert len(rendered) <= messages.TELEGRAM_MESSAGE_LIMIT
    assert "Not set" in missing_date
    assert "62.5 kg" in weight


def test_import_and_profile_messages_do_not_reference_removed_features() -> None:
    imported = messages.training_file_result(
        file_format="TCX",
        activities_imported=1,
        activities_updated=2,
        activities_skipped=3,
    )
    profile = messages.persisted_profile(
        {
            "birth_year": 1988,
            "gender": "FEMALE",
            "weight_kg": 62.5,
            "height_cm": 168,
            "weekly_availability": _weekly_availability(),
        }
    )

    assert "Your training history was updated." in imported
    assert "Birth year: 1988" in profile
    assert "Weekly availability" in profile
    assert "Sat" in profile
    assert "Run/Bike/Swim/Str" in profile


def test_profile_equipment_table_and_long_context_fit_telegram() -> None:
    profile = messages.persisted_profile(
        {
            "birth_year": 1988,
            "gender": "FEMALE",
            "weight_kg": 62.5,
            "height_cm": 168,
            "weekly_availability": _weekly_availability(),
            "health_limitations_text": "<weekend> " * 500,
            "training_goal": {
                "main_goal": "Run a marathon <safely>",
                "event_date": date(2027, 4, 18),
                "secondary_priority": None,
            },
            "equipment_access": (
                {
                    "kind": CapabilityKind.EQUIPMENT,
                    "code": "running_shoes",
                    "display_name": "Running shoes",
                },
            ),
        }
    )

    assert "<pre>" in profile
    assert "Resource" in profile
    assert "Running shoes" in profile
    assert "&lt;weekend&gt;" in profile
    assert "<b>Training goal</b>" in profile
    assert "Run a marathon &lt;safely&gt;" in profile
    assert "April 18, 2027 (2027-04-18)" in profile
    assert "Secondary priority: Not set" in profile
    assert "[truncated for Telegram]" in profile
    assert len(profile) <= messages.TELEGRAM_MESSAGE_LIMIT


def test_goal_settings_menu_exposes_every_editable_goal_field() -> None:
    # Main goal and secondary priority are chosen from the same deterministic
    # catalog menu as onboarding (ps:v1:goal:main / ps:v1:goal:secondary),
    # not typed as free text.
    assert _button_pairs(keyboards.profile_goal_keyboard()) == [
        ("Main goal: Not set", "ps:v1:goal:main"),
        ("Performance targets: Not set", "ps:v1:goal:metrics"),
        ("Event date: Not set", "ps:v1:goal:date"),
        ("Supporting goal: Not set", "ps:v1:goal:secondary"),
        ("Back", "ps:v1:goal:back"),
    ]
