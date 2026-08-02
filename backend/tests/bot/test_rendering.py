"""Centralized Telegram presentation tests."""

from __future__ import annotations

from datetime import UTC, date, datetime

from telegram import InlineKeyboardMarkup

from app.bot import keyboards, messages
from app.domain.enums import OnboardingStep, SyncStatus, WorkoutFlowStep


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


def test_training_day_keyboard_marks_current_selections() -> None:
    markup = keyboards.keyboard_for_step(
        OnboardingStep.TRAINING_DAYS,
        {"training_days": ["MONDAY", "SATURDAY"]},
    )

    assert markup is not None
    labels = _button_labels(markup)
    callbacks = _callback_values(markup)
    assert "✓ Monday" in labels
    assert "✓ Saturday" in labels
    assert "Tuesday" in labels
    assert "ob:v1:multi:remove:TRAINING_DAYS:MONDAY" in callbacks
    assert "ob:v1:multi:add:TRAINING_DAYS:TUESDAY" in callbacks


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
        keyboards.keyboard_for_step(step, {})
        for step in OnboardingStep
        if step not in {OnboardingStep.GOAL_TYPE}
    ]
    samples.append(
        keyboards.keyboard_for_step(
            OnboardingStep.GOAL_TYPE,
            {"primary_sport": "triathlon"},
        )
    )
    samples.extend(
        [
            keyboards.parsed_confirmation_keyboard(),
            keyboards.welcome_keyboard(),
            keyboards.information_keyboard(),
            keyboards.consent_keyboard(),
            keyboards.setup_introduction_keyboard(),
            keyboards.cancelled_keyboard(),
            keyboards.summary_keyboard(),
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
    )

    callbacks = [
        callback
        for markup in samples
        if markup is not None
        for callback in _callback_values(markup)
    ]

    assert callbacks
    assert max(len(value.encode("utf-8")) for value in callbacks) <= 64


def test_unified_onboarding_import_copy_and_buttons_match_product_contract() -> None:
    baseline = keyboards.keyboard_for_step(
        OnboardingStep.BASELINE_SOURCE,
        {},
        strava_enabled=False,
        apple_health_enabled=True,
    )
    waiting = keyboards.keyboard_for_step(
        OnboardingStep.FILE_IMPORT_WAITING,
        {},
    )

    assert baseline is not None
    assert waiting is not None
    assert messages.step_prompt(OnboardingStep.BASELINE_SOURCE) == (
        "How would you like to establish your initial training baseline?"
    )
    assert _button_labels(baseline)[:4] == [
        "Import training history",
        "Enter baseline manually",
        "Decide later",
        "Back",
    ]
    assert _callback_values(baseline)[:4] == [
        "ob:v1:set:BASELINE_SOURCE:FILE_IMPORT",
        "ob:v1:set:BASELINE_SOURCE:MANUAL",
        "ob:v1:set:BASELINE_SOURCE:SKIP_FOR_NOW",
        "ob:v1:back:BASELINE_SOURCE",
    ]
    assert messages.step_prompt(OnboardingStep.FILE_IMPORT_WAITING) == (
        "Send an Apple Health export ZIP or one or more TCX workout files.\n\n"
        "Apple Health ZIP is recommended for importing previous history.\n"
        "TCX is useful for individual workouts.\n\n"
        "You can upload multiple files and finish when you are done."
    )
    assert _button_pairs(waiting) == [
        ("Finish import", "ob:v1:import:finish"),
        ("Choose another method", "ob:v1:apple:choose_other"),
        ("Back", "ob:v1:apple:back"),
    ]


def test_limited_import_copy_names_partial_unknown_baseline() -> None:
    completion = messages.training_import_complete(
        activities_imported=1,
        activities_updated=0,
        activities_skipped=0,
        discipline_counts={"RUNNING": 1},
        baseline_limited=True,
    )
    daily_apple = messages.apple_health_file_result(
        activities_imported=1,
        activities_updated=0,
        activities_skipped=0,
        onboarding=False,
        baseline_limited=True,
    )

    assert "partial" in completion
    assert "UNKNOWN" in completion
    assert "Runs: 1" in completion
    assert "Rides: 0" in completion
    assert "finish the import" not in daily_apple
    assert "partial" in daily_apple


def test_apple_health_summary_uses_canonical_discipline_keys() -> None:
    summary = messages.apple_health_import_success(
        workouts_found=3,
        activities_imported=3,
        activities_updated=0,
        activities_skipped=0,
        heart_rate_records_matched=0,
        warning_count=0,
        discipline_counts={"CYCLING": 2, "HIKING": 1},
    )

    assert "Rides: 2" in summary
    assert "Hikes: 1" in summary
    assert "Runs: 0" in summary


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


def test_baseline_keyboard_respects_flags_and_has_no_calibration() -> None:
    disabled = _button_labels(
        keyboards.keyboard_for_step(
            OnboardingStep.BASELINE_SOURCE,
            {},
            strava_enabled=False,
            apple_health_enabled=True,
        )
    )
    enabled = _button_labels(
        keyboards.keyboard_for_step(
            OnboardingStep.BASELINE_SOURCE,
            {},
            strava_enabled=True,
            apple_health_enabled=True,
        )
    )
    tcx_only = _button_labels(
        keyboards.keyboard_for_step(
            OnboardingStep.BASELINE_SOURCE,
            {},
            strava_enabled=False,
            apple_health_enabled=False,
            tcx_enabled=True,
        )
    )
    file_import_disabled = _button_labels(
        keyboards.keyboard_for_step(
            OnboardingStep.BASELINE_SOURCE,
            {},
            strava_enabled=False,
            apple_health_enabled=False,
            tcx_enabled=False,
        )
    )

    assert disabled[:3] == [
        "Import training history",
        "Enter baseline manually",
        "Decide later",
    ]
    assert tcx_only[:3] == disabled[:3]
    assert "Import training history" not in file_import_disabled
    assert "Connect Strava" not in disabled
    assert enabled[0] == "Connect Strava"
    assert "Calibration period" not in enabled


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
            "goal_type": "ironman_70_3",
            "event_name": "IRONMAN 70.3 BCN",
            "event_date": date(2027, 5, 2),
            "goal_priority": "finish_safely",
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
    assert "IRONMAN 70.3 BCN" in text
    assert "Height: Not provided" in text
    assert "Monday, Saturday" in text


def test_stale_other_descriptions_do_not_override_predefined_answers() -> None:
    text = messages.onboarding_summary(
        {
            "primary_sport": "RUNNING",
            "goal_type": "TEN_K",
            "goal_type_other_description": "Ultra distance",
            "event_status": False,
            "goal_priority": "FINISH_SAFELY",
            "age": 38,
            "training_days": ["MONDAY"],
            "weekday_duration": 60,
            "weekend_duration": 120,
            "equipment": ["RUNNING_SHOES"],
            "equipment_other_description": "Rowing erg",
            "health_areas": ["NONE"],
            "health_areas_other_description": "Elbow",
            "coach_tone": "CONCISE_PRACTICAL",
            "coach_detail": "MEDIUM",
            "baseline_source": "SKIP_FOR_NOW",
        }
    )

    assert "Goal: Ten K" in text
    assert "Equipment: Running Shoes" in text
    assert "Health constraints: None" in text
    assert "Ultra distance" not in text
    assert "Rowing erg" not in text
    assert "Elbow" not in text


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
