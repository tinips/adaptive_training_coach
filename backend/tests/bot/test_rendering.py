"""Telegram presentation tests for the retained onboarding and import flow."""

from __future__ import annotations

import uuid
from datetime import date

from telegram import InlineKeyboardMarkup, ReplyKeyboardMarkup

from app.bot import keyboards, messages
from app.domain.enums import Discipline, EquipmentImportance, ProfileSettingsStep
from app.schemas.equipment import (
    EquipmentOption,
    EquipmentReview,
    EquipmentSuggestionSummary,
    MissingEssential,
    MissingRecommended,
)
from tests.equipment_seed import CATALOG


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
    assert _button_pairs(keyboards.goal_confirmation_keyboard()) == [
        ("Continue", "ob:v1:goal:confirm"),
        ("Cancel", "ob:v1:cancel"),
    ]
    assert _button_pairs(keyboards.profile_gender_keyboard()) == [
        ("Male", "ob:v1:profile:gender:MALE"),
        ("Female", "ob:v1:profile:gender:FEMALE"),
        ("Cancel", "ob:v1:cancel"),
    ]
    assert _button_pairs(keyboards.goal_date_clarification_keyboard()) == [
        ("Not yet", "ob:v1:goal:choice:NOT_YET"),
        ("Cancel", "ob:v1:cancel"),
    ]
    assert _button_pairs(keyboards.add_workout_keyboard()) == [
        ("Cancel", "ob:v1:cancel"),
    ]
    assert _button_pairs(keyboards.profile_settings_text_keyboard()) == [
        ("Back / Done", "ps:v1:done"),
    ]


def test_callback_values_fit_telegram_limit() -> None:
    samples = [
        keyboards.welcome_keyboard(),
        keyboards.information_keyboard(),
        keyboards.consent_keyboard(),
        keyboards.setup_introduction_keyboard(),
        keyboards.goal_input_keyboard(),
        keyboards.goal_confirmation_keyboard(),
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
        ["Delete"],
    ]
    assert all(
        keyboard.is_persistent is True for keyboard in (start, onboarding, completed)
    )


def test_equipment_messages_render_escaped_bounded_tables() -> None:
    item_id = uuid.uuid4()
    review = EquipmentReview(
        disciplines=(Discipline.CYCLING,),
        options=(
            EquipmentOption(
                id=item_id,
                discipline=Discipline.CYCLING,
                equipment="bike",
                display_name="Bike <all-purpose>",
                importance=EquipmentImportance.ESSENTIAL,
                substitutions=("Mountain bike", "Stationary bike"),
                selected=True,
            ),
        ),
    )
    summary = EquipmentSuggestionSummary(
        can_start=False,
        missing_essentials=(
            MissingEssential(
                discipline=Discipline.CYCLING,
                display_name="Bike <all-purpose>",
                substitutions=("Stationary bike",),
            ),
        ),
        missing_recommended=(
            MissingRecommended(
                discipline=Discipline.CYCLING,
                display_name="Road bike",
            ),
        ),
    )

    review_message = messages.equipment_review(review)
    summary_message = messages.equipment_summary(summary)

    assert "<pre>" in review_message
    assert "Have" in review_message
    assert "Alternatives" in review_message
    assert "Bike &lt;all-purpose&gt;" in review_message
    assert "Essential" in summary_message
    assert "Recommended" in summary_message
    assert len(review_message) <= messages.TELEGRAM_MESSAGE_LIMIT
    assert len(summary_message) <= messages.TELEGRAM_MESSAGE_LIMIT


def test_full_seed_catalog_review_fits_one_telegram_message() -> None:
    display_by_key = {
        (discipline, equipment): display_name
        for discipline, equipment, display_name, _, _ in CATALOG
    }
    review = EquipmentReview(
        disciplines=tuple(
            dict.fromkeys(discipline for discipline, _, _, _, _ in CATALOG)
        ),
        options=tuple(
            EquipmentOption(
                id=uuid.UUID(int=index + 1),
                discipline=discipline,
                equipment=equipment,
                display_name=display_name,
                importance=importance,
                substitutions=tuple(
                    display_by_key[(discipline, key)] for key in substitutions
                ),
                selected=index % 2 == 0,
            )
            for index, (
                discipline,
                equipment,
                display_name,
                importance,
                substitutions,
            ) in enumerate(CATALOG)
        ),
    )

    rendered = messages.equipment_review(review)

    assert all(
        discipline.value.title() in rendered for discipline in review.disciplines
    )
    assert len(rendered) <= messages.TELEGRAM_MESSAGE_LIMIT


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
                    "discipline": Discipline.RUNNING,
                    "display_name": "Running shoes",
                    "importance": EquipmentImportance.ESSENTIAL,
                },
            ),
        }
    )

    assert "<pre>" in profile
    assert "Discipline" in profile
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


def test_secondary_goal_edit_prompt_shows_current_value() -> None:
    secondary = messages.profile_setting_prompt(
        ProfileSettingsStep.GOAL_SECONDARY,
        None,
        messages.PROFILE_GOAL_SECONDARY,
    )
    assert "Current secondary priority" in secondary
    assert "Not set" in secondary
    assert _button_pairs(keyboards.profile_goal_secondary_keyboard()) == [
        ("None", "ps:v1:goal:no-secondary"),
        ("Back", "ps:v1:goal:back"),
    ]


def test_goal_settings_menu_exposes_every_editable_goal_field() -> None:
    assert _button_pairs(keyboards.profile_goal_keyboard()) == [
        ("Main goal", "ps:v1:goal:main"),
        ("Target outcome", "ps:v1:goal:outcome"),
        ("Event date", "ps:v1:goal:date"),
        ("Secondary priority", "ps:v1:goal:secondary"),
        ("Back", "ps:v1:goal:back"),
    ]
