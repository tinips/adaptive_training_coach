"""Centralized English user-facing messages and renderers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from html import escape
from typing import Any

from app.domain.enums import OnboardingStep, SyncStatus

GENERIC_ERROR = (
    "Something went wrong. Your saved progress is safe. Please try again in a moment."
)
NOT_FOUND = "I could not find that saved item for your account."
CALLBACK_EXPIRED = (
    "That button is no longer valid. Use /start to return to your current step."
)
WELCOME_NEW = (
    "Welcome to Adaptive Endurance Coach. I will help you create an athlete "
    "profile and establish a data-based baseline. This version does not generate "
    "training plans."
)
WELCOME_BACK = "Welcome back. I found your saved progress."
ONBOARDING_COMPLETE = (
    "Your profile is saved. You can now establish or inspect your athletic baseline."
)
PROFILE_ALREADY_COMPLETE = "Your athlete profile is already complete."
PARSE_RATE_LIMITED = (
    "You have reached the hourly free-text interpretation limit. You can still use "
    "all predefined buttons, or try free text again later."
)
PARSE_PROVIDER_ERROR = (
    "I could not interpret that answer right now. No value was saved. You can try "
    "again or return to the predefined options."
)
PARSE_FALLBACK = (
    "I could not safely structure that answer. No value was saved. Please write it "
    "again more specifically or return to the predefined options."
)
PARSE_RETRY = "Write your answer again. I will ask you to confirm before saving it."
PARSE_BACK = "No interpreted value was saved. Choose one of the predefined options."
PARSE_DISPLAY_MISSING = "Structured answer"
CANCEL_CONFIRM = (
    "Cancel the current onboarding? Your staged answers will remain unavailable "
    "until you restart onboarding."
)
CANCELLED = "Onboarding was cancelled. Use /start whenever you want to restart."
DELETE_CONFIRM = (
    "Delete your account and personal application data? This cannot be undone. "
    "Your Strava authorization will be revoked when possible."
)
DELETED = (
    "Your application account and personal data were deleted. Any Strava "
    "authorization was revoked when the provider was reachable."
)
DELETE_FAILED = (
    "I could not commit the local account deletion. External authorization may "
    "already have been revoked. Please try again later."
)
ACCOUNT_KEPT = "Your account was not deleted."
HELP = (
    "Commands:\n"
    "/start — start or resume\n"
    "/profile — view your athlete profile\n"
    "/baseline — view your current data baseline\n"
    "/strava — view or manage Strava\n"
    "/cancel — cancel active onboarding\n"
    "/delete_me — request account deletion\n\n"
    "This product is not medical advice and must not be used for emergencies. "
    "No training plan is generated in this version."
)
PROFILE_INCOMPLETE = (
    "Your athlete profile is not complete yet. Resume onboarding to continue from "
    "your saved step."
)
BASELINE_NOT_READY = (
    "No calculated baseline is available yet. Connect Strava and import activities, "
    "or keep your selected manual/calibration option for a future milestone."
)
BASELINE_MANUAL_PENDING = (
    "Manual baseline collection is selected but is not implemented in this "
    "milestone. No baseline values have been fabricated."
)
BASELINE_CALIBRATION_PENDING = (
    "Calibration period is selected but will be implemented in the next milestone. "
    "No baseline values have been fabricated."
)
BASELINE_SELECTION_UNAVAILABLE = (
    "That baseline option is no longer available from this screen. Use /start to "
    "open your current account menu."
)
STRAVA_CONNECT_EXPLANATION = (
    "Connecting Strava is optional. The authorization page requests activity read "
    "access so recent activities can be imported for your baseline. You can "
    "disconnect later."
)
STRAVA_NOT_CONNECTED = "Strava is not connected."
STRAVA_CONNECTION_UNHEALTHY = (
    "Strava authorization is stored but cannot currently be used. You can reconnect "
    "or disconnect to revoke access and erase the local tokens."
)
STRAVA_SYNC_STARTED = (
    "Strava synchronization started. Use /strava to check its current status."
)
STRAVA_SYNC_COMPLETE = (
    "Strava synchronization completed and your baseline was recalculated."
)
STRAVA_SYNC_PARTIAL = (
    "Strava synchronization stopped before every activity page was processed. "
    "Activities already imported were retained, and the baseline was refreshed "
    "when usable new data was saved."
)
STRAVA_SYNC_RATE_LIMITED = (
    "Strava paused this synchronization because its read limit was reached. "
    "Activities already imported were retained. Please try again later."
)
STRAVA_SYNC_FAILED = (
    "Strava synchronization failed before it could complete. Your previously saved "
    "activities and baseline remain available."
)
STRAVA_SYNC_CONCURRENT = "A Strava synchronization is already running."
STRAVA_SYNC_COOLDOWN = (
    "A manual synchronization completed recently. Please wait before syncing again."
)
STRAVA_DISCONNECT_CONFIRM = (
    "Disconnect Strava? Provider authorization will be revoked and encrypted tokens "
    "will be erased. Previously imported activity summaries will remain until you "
    "delete your account."
)
STRAVA_DISCONNECTED = (
    "Strava authorization was revoked and local OAuth tokens were erased. Previously "
    "imported activity summaries remain stored for your baseline and audit history."
)
STRAVA_DISCONNECTED_LOCAL_ONLY = (
    "Local Strava tokens were erased, but provider revocation could not be confirmed. "
    "Remove this application in your Strava settings if it is still listed. "
    "Previously imported activity summaries remain stored until account deletion."
)
STRAVA_KEPT = "Strava authorization remains stored."
STRAVA_CONFIGURATION_MISSING = (
    "Strava is not configured on this installation. An administrator must add the "
    "application credentials first."
)
RECALCULATION_COMPLETE = "Your deterministic baseline was recalculated."
RECALCULATION_FAILED = (
    "The baseline could not be recalculated. Your previous baseline remains available."
)
READY_MENU = "Your profile and baseline are ready."
IMPORTING_MENU = "Your Strava activities are currently being imported."
BASELINE_SETUP_MENU = "Choose how you want to establish your athletic baseline."
RESTART_OFFER = "Your previous onboarding was cancelled. Restart from the beginning?"
FREE_TEXT_REQUEST = (
    "Write your answer in any language. I will interpret it and ask you to confirm "
    "before anything is saved."
)
HEALTH_FREE_TEXT_NOTICE = (
    "Describe only the training limitation you want the coach to consider. Do not "
    "include a diagnosis. I will ask you to confirm the interpretation."
)


def strava_sync_outcome(status: SyncStatus) -> str:
    """Render every persisted synchronization outcome without overstating success."""

    return {
        SyncStatus.REQUESTED: STRAVA_SYNC_STARTED,
        SyncStatus.RUNNING: STRAVA_SYNC_STARTED,
        SyncStatus.SUCCEEDED: STRAVA_SYNC_COMPLETE,
        SyncStatus.PARTIAL: STRAVA_SYNC_PARTIAL,
        SyncStatus.RATE_LIMITED: STRAVA_SYNC_RATE_LIMITED,
        SyncStatus.FAILED: STRAVA_SYNC_FAILED,
    }[status]


EVENT_NAME_REQUEST = (
    "Write the event name exactly as you want it displayed. It will be stored as "
    "entered."
)
EVENT_DATE_REQUEST = "Enter the event date as DD/MM/YYYY or YYYY-MM-DD."
AGE_REQUEST = "Enter your age as a whole number from 16 to 100."
HEIGHT_REQUEST = "Enter your height in centimetres (120-230), or choose Skip."
WEIGHT_REQUEST = "Enter your weight in kilograms (35-250), or choose Skip."

CONSENT = (
    "Before we begin:\n\n"
    "• This product is not medical advice and must not be used for emergencies.\n"
    "• Injury or health-limitation information you choose to provide is stored.\n"
    "• Strava data is optional.\n"
    "• This version establishes a profile and baseline; it does not generate a "
    "training plan.\n"
    "• You can cancel onboarding or delete your data.\n\n"
    "Continue only if you understand and accept this."
)

STEP_PROMPTS: dict[OnboardingStep, str] = {
    OnboardingStep.CONSENT: CONSENT,
    OnboardingStep.PRIMARY_SPORT: "What is your primary sport?",
    OnboardingStep.GOAL_TYPE: "What is your main training goal?",
    OnboardingStep.EVENT_STATUS: "Do you have a specific target event?",
    OnboardingStep.EVENT_NAME: EVENT_NAME_REQUEST,
    OnboardingStep.EVENT_DATE: EVENT_DATE_REQUEST,
    OnboardingStep.GOAL_PRIORITY: "What matters most for this goal?",
    OnboardingStep.AGE: AGE_REQUEST,
    OnboardingStep.HEIGHT: HEIGHT_REQUEST,
    OnboardingStep.WEIGHT: WEIGHT_REQUEST,
    OnboardingStep.TRAINING_DAYS: (
        "Select every day you can usually train, then choose Continue."
    ),
    OnboardingStep.WEEKDAY_DURATION: (
        "How much time can you usually train on a weekday?"
    ),
    OnboardingStep.WEEKEND_DURATION: (
        "How much time can you usually train on a weekend day?"
    ),
    OnboardingStep.EQUIPMENT: (
        "Select all equipment you can use, then choose Continue."
    ),
    OnboardingStep.POOL_ACCESS: (
        "Select your regular pool-access days, or choose an irregular/no-access option."
    ),
    OnboardingStep.BIKE_ACCESS: (
        "Select your regular bike-access days, or choose an irregular/no-access option."
    ),
    OnboardingStep.HEALTH_AREAS: (
        "Select any areas that currently or historically limit training. Choose None "
        "if there are no constraints, then choose Continue."
    ),
    OnboardingStep.HEALTH_TIMING: "When does this limitation apply?",
    OnboardingStep.HEALTH_DESCRIPTION: (
        "You may add a short limitation description, or skip it."
    ),
    OnboardingStep.COACH_TONE: "Which coaching tone do you prefer?",
    OnboardingStep.COACH_DETAIL: "How much explanation do you prefer?",
    OnboardingStep.BASELINE_SOURCE: (
        "How would you like to establish your athletic baseline?"
    ),
    OnboardingStep.SUMMARY: "Review your profile before confirming it.",
}

VALIDATION_ERRORS: dict[str, str] = {
    "invalid_age": "Enter a whole-number age from 16 to 100.",
    "invalid_height": "Enter a height from 120 to 230 centimetres, or choose Skip.",
    "invalid_weight": "Enter a weight from 35 to 250 kilograms, or choose Skip.",
    "invalid_date": "Use DD/MM/YYYY or YYYY-MM-DD.",
    "invalid_date_format": "Use DD/MM/YYYY or YYYY-MM-DD.",
    "past_date": "The target event date must be in the future.",
    "event_date_in_past": "The target event date must be in the future.",
    "selection_required": "Select at least one option before continuing.",
    "invalid_option": "Choose one of the available options.",
    "invalid_number": "Enter a valid number.",
    "number_out_of_range": "That number is outside the allowed range.",
    "integer_required": "Enter a whole number.",
    "number_required": "This number is required.",
    "invalid_action": CALLBACK_EXPIRED,
    "stale_action": CALLBACK_EXPIRED,
    "onboarding_not_active": CALLBACK_EXPIRED,
    "restart_not_allowed": CALLBACK_EXPIRED,
    "parsed_value_missing": CALLBACK_EXPIRED,
    "parse_in_progress": (
        "I am still interpreting your previous answer. Please wait for that result."
    ),
    "incomplete_profile": (
        "Some required answers are missing. Your progress is safe; resume onboarding "
        "to complete them."
    ),
}


def step_prompt(step: OnboardingStep) -> str:
    """Return the centralized question for an onboarding step."""

    return STEP_PROMPTS[step]


def validation_error(code: str) -> str:
    """Map a safe application error code to English copy."""

    return VALIDATION_ERRORS.get(code, GENERIC_ERROR)


def interpreted_answer(display_value: str) -> str:
    """Render the mandatory pre-confirmation interpretation."""

    return f"I interpreted your answer as:\n\n{escape(display_value)}"


def clarification(question: str | None) -> str:
    """Render one English clarification without storing a parsed value."""

    if question:
        return escape(question)
    return "Please give one more specific detail so I can interpret your answer safely."


def selected_values(values: Sequence[str]) -> str:
    """Render selected multi-choice values beneath a prompt."""

    if not values:
        return "Selected: none"
    return "Selected: " + ", ".join(_display(value) for value in values)


def onboarding_summary(answers: Mapping[str, Any]) -> str:
    """Render staged answers for final confirmation."""

    event = "No target event"
    if answers.get("event_status") is True:
        event_name = answers.get("event_name") or "Unnamed event"
        event_date = answers.get("event_date") or "date not provided"
        event = f"{event_name} — {event_date}"

    lines = [
        "Review your athlete profile:",
        "",
        f"Sport: {_answer_display(answers, 'primary_sport')}",
        f"Goal: {_answer_display(answers, 'goal_type')}",
        f"Event: {escape(str(event))}",
        f"Goal priority: {_answer_display(answers, 'goal_priority')}",
        f"Age: {_display(answers.get('age'))}",
        f"Height: {_optional_metric(answers.get('height'), 'cm')}",
        f"Weight: {_optional_metric(answers.get('weight'), 'kg')}",
        f"Training days: {_display_list(answers.get('training_days'))}",
        (
            "Weekday availability: "
            f"{_display_availability(answers.get('weekday_duration'))}"
        ),
        (
            "Weekend availability: "
            f"{_display_availability(answers.get('weekend_duration'))}"
        ),
        f"Equipment: {_answer_list_display(answers, 'equipment')}",
        f"Pool access: {_display_access(answers.get('pool_access'))}",
        f"Bike access: {_display_access(answers.get('bike_access'))}",
        f"Health constraints: {_answer_list_display(answers, 'health_areas')}",
        f"Constraint timing: {_display(answers.get('health_timing'))}",
        f"Constraint description: {_display(answers.get('health_description'))}",
        (
            "Coach style: "
            f"{_display(answers.get('coach_tone'))}; "
            f"{_display(answers.get('coach_detail'))}"
        ),
        f"Baseline source: {_display(answers.get('baseline_source'))}",
        "",
        "Confirm only if this is correct.",
    ]
    return "\n".join(lines)


def persisted_profile(profile: Mapping[str, Any]) -> str:
    """Render normalized persisted profile data."""

    lines = [
        "Your saved athlete profile:",
        "",
        f"Sport: {_answer_display(profile, 'primary_sport')}",
        f"Goal: {_answer_display(profile, 'goal_type')}",
        f"Event: {_display_free_text(profile.get('event_name'))}",
        f"Event date: {_display(profile.get('event_date'))}",
        f"Goal priority: {_answer_display(profile, 'goal_priority')}",
        f"Age: {_display(profile.get('age'))}",
        f"Height: {_optional_metric(profile.get('height_cm'), 'cm')}",
        f"Weight: {_optional_metric(profile.get('weight_kg'), 'kg')}",
        f"Training days: {_display_list(profile.get('training_days'))}",
        (
            "Weekday availability: "
            f"{_display_availability(profile.get('weekday_duration'))}"
        ),
        (
            "Weekend availability: "
            f"{_display_availability(profile.get('weekend_duration'))}"
        ),
    ]
    equipment_lines = _equipment_access_lines(profile.get("equipment_access"))
    if equipment_lines:
        lines.append("Equipment and access:")
        lines.extend(equipment_lines)
    else:
        lines.append(
            f"Equipment: {_answer_list_display(profile, 'equipment')}",
        )

    health_lines = _health_constraint_lines(
        profile.get("health_constraint_details"),
    )
    if health_lines:
        lines.append("Health constraints:")
        lines.extend(health_lines)
    else:
        lines.append(
            (
                "Health constraints: "
                f"{_display_free_text_list(profile.get('health_constraints'))}"
            ),
        )

    lines.extend(
        [
            (
                "Coach style: "
                f"{_display(profile.get('coach_tone'))}; "
                f"{_display(profile.get('detail_level'))}"
            ),
            f"Baseline source: {_display(profile.get('baseline_source'))}",
        ]
    )
    return "\n".join(lines)


def baseline_summary(data: Mapping[str, Any]) -> str:
    """Render persisted baseline metrics without medical claims."""

    start = _date_value(data.get("analysis_start"))
    end = _date_value(data.get("analysis_end"))
    lines = [
        "Your activity-data baseline:",
        "",
        f"Source: {_display(data.get('source'))}",
        f"Analysis period: {start} to {end}",
        f"Imported activities: {_display(data.get('activity_count'))}",
        f"Data freshness: {_freshness(data.get('data_freshness_days'))}",
        f"Overall confidence: {_percent(data.get('overall_confidence'))}",
        "",
    ]
    disciplines = data.get("disciplines") or []
    for item in disciplines:
        if not isinstance(item, Mapping):
            continue
        lines.extend(
            [
                f"{_display(item.get('discipline'))}",
                (
                    f"  Level: {_display(item.get('level_label'))} "
                    "(provisional product heuristic)"
                ),
                f"  Confidence: {_percent(item.get('confidence'))}",
                (
                    f"  Sessions: {_display(item.get('sessions_count'))}; "
                    f"active weeks: {_display(item.get('active_weeks'))}; "
                    f"recent sessions: {_display(item.get('recent_session_count'))}"
                ),
                (
                    "  Average weekly duration: "
                    f"{_duration(item.get('average_weekly_duration_seconds'))}"
                ),
                (
                    "  Longest session: "
                    f"{_duration(item.get('longest_session_seconds'))}"
                ),
            ]
        )
        if item.get("average_weekly_distance_meters") is not None:
            lines.append(
                "  Average weekly distance: "
                f"{_distance(item.get('average_weekly_distance_meters'))}"
            )
        lines.append("")
    lines.append(
        "Levels and confidence describe available activity data; they are not a "
        "medical or physiological diagnosis."
    )
    return "\n".join(lines)


def strava_status(data: Mapping[str, Any]) -> str:
    """Render connection and synchronization state."""

    if not data.get("connected") and not data.get("can_disconnect"):
        return STRAVA_NOT_CONNECTED
    scopes = data.get("accepted_scopes") or []
    heading = (
        "Strava connection:" if data.get("connected") else STRAVA_CONNECTION_UNHEALTHY
    )
    return "\n".join(
        [
            heading,
            "",
            f"Status: {_display(data.get('connection_status'))}",
            f"Accepted scopes: {_display_list(scopes)}",
            f"Last successful sync: {_date_time(data.get('last_successful_sync_at'))}",
            f"Current sync: {_display(data.get('sync_status'))}",
        ]
    )


def oauth_success_page(initial_sync_started: bool) -> str:
    """Return an English OAuth success HTML page."""

    sync_text = (
        "Your initial activity import has started."
        if initial_sync_started
        else "You can return to Telegram and use Sync now."
    )
    return _html_page(
        "Strava connected",
        f"Strava authorization was saved securely. {sync_text} You may close this tab.",
    )


def oauth_failure_page(reason: str) -> str:
    """Return a safe English OAuth failure HTML page."""

    safe_reasons = {
        "access_denied": "You declined Strava access. No connection was created.",
        "expired_state": "This connection link expired. Request a new one in Telegram.",
        "consumed_state": "This connection link was already used.",
        "invalid_state": "This connection link is invalid.",
        "insufficient_scope": (
            "Required activity-read access was not granted. No usable connection "
            "was created."
        ),
        "provider_error": (
            "Strava could not complete the connection. Return to Telegram and try "
            "again."
        ),
    }
    return _html_page(
        "Strava connection not completed",
        safe_reasons.get(reason, safe_reasons["provider_error"]),
    )


def _html_page(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{escape(title)}</title></head><body><main><h1>{escape(title)}</h1>"
        f"<p>{escape(body)}</p></main></body></html>"
    )


def _display(value: Any) -> str:
    if value is None or value == "":
        return "Not provided"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return escape(str(value).replace("_", " ").title())


def _display_list(value: Any) -> str:
    if not value:
        return "None"
    if isinstance(value, str):
        return _display(value)
    return ", ".join(_display(item) for item in value)


def _display_free_text(value: Any) -> str:
    if value is None or value == "":
        return "Not provided"
    return escape(str(value))


def _display_free_text_list(value: Any) -> str:
    if not value:
        return "None"
    if isinstance(value, str):
        return _display_free_text(value)
    return ", ".join(_display_free_text(item) for item in value)


def _optional_metric(value: Any, unit: str) -> str:
    return "Not provided" if value is None else f"{escape(str(value))} {unit}"


def _display_availability(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value} min"
    labels = {
        "OVER_90": "Over 90 min",
        "OVER_180": "Over 180 min",
        "VARIABLE": "Variable",
    }
    return labels.get(str(value), _display(value))


def _display_access(value: Any) -> str:
    if not isinstance(value, Mapping):
        return _display_list(value)
    access_type = _display(value.get("type"))
    days = _display_list(value.get("days"))
    return access_type if not value.get("days") else f"{access_type}: {days}"


def _equipment_access_lines(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    lines: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        equipment_type = _display(item.get("equipment_type"))
        access_type = _display(item.get("access_type"))
        detail = f"{equipment_type} — {access_type}"
        if item.get("access_days"):
            detail += f": {_display_list(item.get('access_days'))}"
        if item.get("notes"):
            detail += f"; notes: {escape(str(item.get('notes')))}"
        lines.append(f"  - {detail}")
    return lines


def _health_constraint_lines(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    lines: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        body_area = _display(item.get("body_area"))
        timing = _display(item.get("constraint_type"))
        detail = f"{body_area} — {timing}"
        if item.get("description"):
            detail += f": {escape(str(item.get('description')))}"
        lines.append(f"  - {detail}")
    return lines


def _answer_display(values: Mapping[str, Any], key: str) -> str:
    other = values.get(f"{key}_other_description")
    value = values.get(key)
    return escape(str(other)) if other and _contains_other(value) else _display(value)


def _answer_list_display(values: Mapping[str, Any], key: str) -> str:
    value = values.get(key)
    rendered = _display_list(value)
    other = values.get(f"{key}_other_description")
    if not other or not _contains_other(value):
        return rendered
    return f"{rendered} ({escape(str(other))})"


def _contains_other(value: Any) -> bool:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(str(item).upper() == "OTHER" for item in value)
    return str(value).upper() == "OTHER"


def _percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "Unknown"


def _duration(value: Any) -> str:
    try:
        minutes = round(float(value) / 60)
    except (TypeError, ValueError):
        return "Unknown"
    hours, remaining = divmod(minutes, 60)
    return f"{hours} h {remaining} min" if hours else f"{remaining} min"


def _distance(value: Any) -> str:
    try:
        return f"{float(value) / 1000:.1f} km"
    except (TypeError, ValueError):
        return "Unknown"


def _freshness(value: Any) -> str:
    try:
        days = float(value)
    except (TypeError, ValueError):
        return "No recent activity data"
    if days < 1:
        return "Less than one day"
    return f"{days:.0f} days since the latest imported activity"


def _date_value(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    return _display(value)


def _date_time(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return _display(value)
