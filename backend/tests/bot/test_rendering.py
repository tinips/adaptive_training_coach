"""Centralized Telegram presentation tests."""

from __future__ import annotations

from datetime import UTC, date, datetime

from telegram import InlineKeyboardMarkup

from app.bot import keyboards, messages
from app.domain.enums import SyncStatus, WorkoutFlowStep


def _button_labels(markup: InlineKeyboardMarkup) -> list[str]:
    rows = markup.inline_keyboard
    return [button.text for row in rows for button in row]


def _callback_values(markup: InlineKeyboardMarkup) -> list[str]:
    rows = markup.inline_keyboard
    return [
        button.callback_data
        for row in rows
        for button in row
        if button.callback_data is not None
    ]


def _button_pairs(markup: InlineKeyboardMarkup) -> list[tuple[str, str]]:
    rows = markup.inline_keyboard
    return [
        (button.text, button.callback_data)
        for row in rows
        for button in row
        if button.callback_data is not None
    ]


def test_goal_keyboards_expose_only_retained_actions() -> None:
    assert _button_pairs(keyboards.goal_input_keyboard()) == [
        ("Cancel", "ob:v1:cancel")
    ]
    assert _button_pairs(keyboards.goal_confirmation_keyboard()) == [
        ("No, that\u2019s right", "ob:v1:goal:confirm"),
        ("Yes, add something", "ob:v1:goal:add"),
        ("Start again", "ob:v1:goal:restart"),
        ("Cancel", "ob:v1:cancel"),
    ]
    assert _button_pairs(keyboards.goal_saved_keyboard()) == [
        ("Back to welcome", "nav:v1:welcome")
    ]
    assert _button_pairs(keyboards.profile_gender_keyboard()) == [
        ("Male", "ob:v1:profile:gender:MALE"),
        ("Female", "ob:v1:profile:gender:FEMALE"),
        ("Other / Unspecified", "ob:v1:profile:gender:OTHER_UNSPECIFIED"),
        ("Cancel", "ob:v1:cancel"),
    ]


def test_welcome_consent_and_setup_keyboards_match_visible_contract() -> None:
    assert _button_pairs(keyboards.welcome_keyboard()) == [
        ("Let's go", "nav:v1:consent"),
        ("How can this coach help me?", "nav:v1:help"),
        ("Privacy & safety", "nav:v1:privacy"),
    ]
    assert _button_pairs(keyboards.information_keyboard()) == [
        ("Let's go", "nav:v1:consent"),
        ("Back", "nav:v1:welcome"),
    ]
    assert _button_pairs(keyboards.consent_keyboard()) == [
        ("I understand — continue", "ob:v1:consent"),
        ("Back", "nav:v1:welcome"),
        ("Cancel", "ob:v1:cancel"),
    ]
    assert _button_pairs(keyboards.setup_introduction_keyboard()) == [
        ("Let's build my profile", "ob:v1:profile"),
        ("Cancel", "ob:v1:cancel"),
    ]


def test_user_facing_message_constants_do_not_advertise_slash_commands() -> None:
    command_tokens = {
        "/start",
        "/help",
        "/profile",
        "/baseline",
        "/add_workout",
        "/strava",
        "/cancel",
        "/delete_me",
    }
    rendered_constants = [
        value
        for name, value in vars(messages).items()
        if name.isupper() and isinstance(value, str)
    ]

    assert all(
        token not in text for text in rendered_constants for token in command_tokens
    )


def test_every_callback_value_fits_telegram_limit() -> None:
    samples = [
        keyboards.welcome_keyboard(),
        keyboards.information_keyboard(),
        keyboards.consent_keyboard(),
        keyboards.setup_introduction_keyboard(),
        keyboards.goal_input_keyboard(),
        keyboards.goal_confirmation_keyboard(),
        keyboards.goal_saved_keyboard(),
        keyboards.profile_text_input_keyboard(),
        keyboards.profile_gender_keyboard(),
        keyboards.goal_date_clarification_keyboard(),
        keyboards.goal_main_clarification_keyboard(),
        keyboards.cancelled_keyboard(),
        keyboards.resume_keyboard(),
        keyboards.cancel_confirmation_keyboard(),
        keyboards.deletion_confirmation_keyboard(),
        keyboards.strava_keyboard(connected=True),
        keyboards.disconnect_confirmation_keyboard(),
        keyboards.state_menu("ready", connected=True),
        keyboards.add_workout_keyboard(),
        keyboards.feedback_text_entry_keyboard(),
        keyboards.manual_heart_rate_offer_keyboard(),
        keyboards.manual_heart_rate_confirmation_keyboard(),
        keyboards.rpe_keyboard(),
        keyboards.mobility_keyboard(),
        keyboards.discomfort_keyboard(),
        keyboards.discomfort_area_keyboard(),
        keyboards.discomfort_description_confirmation_keyboard(),
        keyboards.discomfort_severity_keyboard(),
    ]

    callbacks = [
        callback
        for markup in samples
        if markup is not None
        for callback in _callback_values(markup)
    ]

    assert callbacks
    assert max(len(value.encode("utf-8")) for value in callbacks) <= 64


def test_limited_import_copy_names_partial_unknown_baseline() -> None:
    daily_apple = messages.apple_health_file_result(
        activities_imported=1,
        activities_updated=0,
        activities_skipped=0,
        baseline_limited=True,
    )

    assert "finish the import" not in daily_apple
    assert "partial" in daily_apple


def test_ready_menu_exposes_daily_workout_action_and_required_home_actions() -> None:
    markup = keyboards.state_menu(
        "ready",
        connected=False,
        strava_enabled=False,
    )

    assert _button_pairs(markup) == [
        ("Add workout", "menu:v1:add_workout"),
        ("View baseline", "menu:v1:baseline"),
        ("View profile", "menu:v1:profile"),
        ("Help", "menu:v1:help"),
    ]


def test_daily_feedback_keyboards_use_deterministic_callback_namespaces() -> None:
    assert _button_pairs(keyboards.add_workout_keyboard()) == [
        ("Cancel", "wf:v1:cancel"),
        ("Back", "wf:v1:back:waiting_for_file"),
    ]
    assert _button_pairs(keyboards.feedback_text_entry_keyboard()) == [
        ("Back", "wf:v1:back:hr_entry"),
        ("Cancel", "wf:v1:cancel"),
    ]
    assert _button_pairs(
        keyboards.feedback_text_entry_keyboard(state=WorkoutFlowStep.DESCRIPTION_ENTRY)
    ) == [
        ("Back", "wf:v1:back:description_entry"),
        ("Cancel", "wf:v1:cancel"),
    ]
    assert _button_pairs(keyboards.manual_heart_rate_offer_keyboard()) == [
        ("Enter average HR", "wf:v1:hr:enter"),
        ("Continue without HR", "wf:v1:hr:skip"),
        ("Cancel", "wf:v1:cancel"),
    ]
    assert _button_pairs(keyboards.manual_heart_rate_confirmation_keyboard()) == [
        ("Confirm", "wf:v1:hr:confirm"),
        ("Change", "wf:v1:hr:change"),
        ("Skip", "wf:v1:hr:skip"),
    ]
    assert _button_pairs(keyboards.rpe_keyboard()) == [
        ("Very easy", "wf:v1:rpe:very_easy"),
        ("Easy", "wf:v1:rpe:easy"),
        ("Moderate", "wf:v1:rpe:moderate"),
        ("Hard", "wf:v1:rpe:hard"),
        ("Very hard", "wf:v1:rpe:very_hard"),
        ("Skip", "wf:v1:rpe:skip"),
        ("Back", "wf:v1:back:rpe"),
    ]
    assert _button_pairs(keyboards.mobility_keyboard()) == [
        ("Yes", "wf:v1:mobility:yes"),
        ("No", "wf:v1:mobility:no"),
        ("Skip", "wf:v1:mobility:skip"),
        ("Back", "wf:v1:back:mobility"),
    ]
    assert _button_pairs(keyboards.discomfort_keyboard()) == [
        ("No", "wf:v1:discomfort:no"),
        ("Yes", "wf:v1:discomfort:yes"),
        ("Skip", "wf:v1:discomfort:skip"),
        ("Back", "wf:v1:back:discomfort"),
    ]
    assert _button_pairs(keyboards.discomfort_area_keyboard()) == [
        ("Shoulder", "wf:v1:area:shoulder"),
        ("Back", "wf:v1:area:back"),
        ("Hip", "wf:v1:area:hip"),
        ("Knee", "wf:v1:area:knee"),
        ("Ankle or foot", "wf:v1:area:ankle_foot"),
        ("Other", "wf:v1:area:other"),
        ("Skip details", "wf:v1:area:skip"),
        ("Back", "wf:v1:back:body_area"),
    ]
    assert _button_pairs(keyboards.discomfort_description_confirmation_keyboard()) == [
        ("Confirm", "wf:v1:description:confirm"),
        ("Change", "wf:v1:description:change"),
        ("Skip", "wf:v1:description:skip"),
    ]
    assert _button_pairs(keyboards.discomfort_severity_keyboard()) == [
        ("Mild", "wf:v1:severity:mild"),
        ("Moderate", "wf:v1:severity:moderate"),
        ("Severe", "wf:v1:severity:severe"),
        ("Skip", "wf:v1:severity:skip"),
        ("Back", "wf:v1:back:severity"),
    ]


def test_state_aware_menu_exposes_only_valid_import_actions() -> None:
    labels = _button_labels(
        keyboards.state_menu(
            "importing",
            connected=True,
            syncing=True,
            strava_enabled=True,
        )
    )

    assert labels == [
        "Add workout",
        "View sync status",
        "View baseline",
        "View profile",
        "Help",
    ]
    assert "Sync now" not in labels


def test_ready_disconnected_menu_offers_reconnect_without_sync_actions() -> None:
    labels = _button_labels(
        keyboards.state_menu(
            "ready",
            connected=False,
            strava_enabled=True,
        )
    )

    assert labels == [
        "Reconnect Strava",
        "Add workout",
        "View baseline",
        "View profile",
        "Help",
    ]
    assert "Sync now" not in labels
    assert "Recalculate baseline" not in labels


def test_manual_sync_outcome_copy_covers_every_terminal_and_active_state() -> None:
    rendered = {status: messages.strava_sync_outcome(status) for status in SyncStatus}

    assert set(rendered) == set(SyncStatus)
    assert rendered[SyncStatus.SUCCEEDED] == messages.STRAVA_SYNC_COMPLETE
    assert rendered[SyncStatus.PARTIAL] == messages.STRAVA_SYNC_PARTIAL
    assert rendered[SyncStatus.RATE_LIMITED] == messages.STRAVA_SYNC_RATE_LIMITED
    assert rendered[SyncStatus.FAILED] == messages.STRAVA_SYNC_FAILED
    assert rendered[SyncStatus.REQUESTED] == messages.STRAVA_SYNC_STARTED
    assert rendered[SyncStatus.RUNNING] == messages.STRAVA_SYNC_STARTED


def test_profile_rendering_uses_normalized_persisted_values() -> None:
    text = messages.persisted_profile(
        {
            "primary_sport": "triathlon",
            "main_goal": "Complete IRONMAN 70.3 BCN",
            "event_date": date(2027, 5, 2),
            "target_outcome": "Finish safely",
            "secondary_priority": None,
            "age": 38,
            "height_cm": None,
            "weight_kg": 72.4,
            "training_days": ["monday", "saturday"],
            "equipment": ["road_bike", "swimming_pool"],
            "health_constraints": [],
            "coach_tone": "concise_practical",
            "detail_level": "medium",
            "baseline_source": "strava",
        }
    )

    assert "Triathlon" in text
    assert "Complete IRONMAN 70.3 BCN" in text
    assert "Target outcome: Finish safely" in text
    assert "Height: Not provided" in text
    assert "Monday, Saturday" in text


def test_baseline_rendering_states_confidence_and_non_medical_boundary() -> None:
    text = messages.baseline_summary(
        {
            "source": "strava",
            "analysis_start": date(2026, 6, 1),
            "analysis_end": date(2026, 7, 27),
            "activity_count": 12,
            "overall_confidence": 0.76,
            "disciplines": [
                {
                    "discipline": "run",
                    "level_label": "developing",
                    "confidence": 0.71,
                    "sessions_count": 8,
                    "active_weeks": 5,
                    "recent_session_count": 2,
                    "average_weekly_duration_seconds": 5400,
                    "average_weekly_distance_meters": 18000,
                    "longest_session_seconds": 4200,
                }
            ],
        }
    )

    assert "Overall confidence: 76%" in text
    assert "provisional product heuristic" in text
    assert "not a medical or physiological diagnosis" in text


def test_strava_status_does_not_render_tokens() -> None:
    text = messages.strava_status(
        {
            "connected": True,
            "connection_status": "connected",
            "accepted_scopes": ["read", "activity:read"],
            "last_successful_sync_at": datetime(2026, 7, 28, tzinfo=UTC),
            "sync_status": "succeeded",
            "encrypted_access_token": "must-not-appear",
        }
    )

    assert "must-not-appear" not in text
    assert "Activity:Read" in text
