"""Centralized Telegram presentation tests."""

from __future__ import annotations

from datetime import UTC, date, datetime

from telegram import InlineKeyboardMarkup

from app.bot import keyboards, messages
from app.domain.enums import OnboardingStep, SyncStatus


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
            keyboards.summary_keyboard(),
            keyboards.resume_keyboard(),
            keyboards.cancel_confirmation_keyboard(),
            keyboards.deletion_confirmation_keyboard(),
            keyboards.strava_keyboard(connected=True),
            keyboards.disconnect_confirmation_keyboard(),
            keyboards.state_menu("ready", connected=True),
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


def test_state_aware_menu_exposes_only_valid_import_actions() -> None:
    labels = _button_labels(
        keyboards.state_menu("importing", connected=True, syncing=True)
    )

    assert labels == ["View sync status", "View profile", "Help"]
    assert "Sync now" not in labels


def test_ready_disconnected_menu_offers_reconnect_without_sync_actions() -> None:
    labels = _button_labels(keyboards.state_menu("ready", connected=False))

    assert labels == [
        "Reconnect Strava",
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
