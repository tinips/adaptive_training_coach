"""Telegram presentation tests for the retained onboarding and import flow."""

from __future__ import annotations

from telegram import InlineKeyboardMarkup

from app.bot import keyboards, messages


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
        ("No, that\u2019s right", "ob:v1:goal:confirm"),
        ("Yes, add something", "ob:v1:goal:add"),
        ("Start again", "ob:v1:goal:restart"),
        ("Cancel", "ob:v1:cancel"),
    ]
    assert _button_pairs(keyboards.add_workout_keyboard()) == [
        ("Cancel", "ob:v1:cancel"),
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
        keyboards.completed_onboarding_keyboard(),
        keyboards.add_workout_keyboard(),
    ]
    callbacks = [
        callback for markup in samples for _, callback in _button_pairs(markup)
    ]
    assert callbacks
    assert max(len(value.encode("utf-8")) for value in callbacks) <= 64


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
