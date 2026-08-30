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


def _button_pairs(markup: InlineKeyboardMarkup) -> list[tuple[str, str]]:
    return [
        (button.text, button.callback_data)
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data is not None
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
    assert _button_pairs(keyboards.add_workout_keyboard()) == [
        ("Cancel", "ob:v1:cancel"),
    ]
    assert _button_pairs(keyboards.profile_settings_text_keyboard()) == [
        ("Back / Done", "ps:v1:done"),
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
        keyboards.add_workout_keyboard(),
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
        ["Add workout", "Plan next week"],
        ["Delete"],
    ]
    assert all(
        keyboard.is_persistent is True for keyboard in (start, onboarding, completed)
    )


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
    imported = messages.apple_health_file_result(
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
            "availability_text": "Weekends",
        }
    )

    assert "Your training history was updated." in imported
    assert "Birth year: 1988" in profile
    assert "Availability: Weekends" in profile


def test_profile_equipment_table_and_long_context_fit_telegram() -> None:
    profile = messages.persisted_profile(
        {
            "birth_year": 1988,
            "gender": "FEMALE",
            "weight_kg": 62.5,
            "height_cm": 168,
            "availability_text": "<weekend> " * 500,
            "health_limitations_text": "NONE_REPORTED",
            "training_goal": {
                "main_goal": "Run a marathon <safely>",
                "target_outcome": "Finish comfortably",
                "event_date": date(2027, 4, 18),
                "secondary_priority": None,
                "status": "CONFIRMED",
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
    assert "None reported" in profile
    assert "&lt;weekend&gt;" in profile
    assert "<b>Training goal</b>" in profile
    assert "Run a marathon &lt;safely&gt;" in profile
    assert "April 18, 2027 (2027-04-18)" in profile
    assert "Secondary priority: Not set" in profile
    assert "Status: Confirmed" in profile
    assert "[truncated for Telegram]" in profile
    assert len(profile) <= messages.TELEGRAM_MESSAGE_LIMIT


def test_goal_settings_menu_exposes_every_editable_goal_field() -> None:
    # Main goal and secondary priority are chosen from the same deterministic
    # catalog menu as onboarding (ps:v1:goal:main / ps:v1:goal:secondary),
    # not typed as free text.
    assert _button_pairs(keyboards.profile_goal_keyboard()) == [
        ("Main goal", "ps:v1:goal:main"),
        ("Target outcome", "ps:v1:goal:outcome"),
        ("Event date", "ps:v1:goal:date"),
        ("Secondary priority", "ps:v1:goal:secondary"),
        ("Back", "ps:v1:goal:back"),
    ]
