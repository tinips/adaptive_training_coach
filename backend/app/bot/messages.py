"""Centralized English user-facing messages and renderers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from html import escape
from typing import Any

from app.domain.enums import ProfileSettingsStep
from app.schemas.equipment import EquipmentReview, EquipmentSuggestionSummary

TELEGRAM_MESSAGE_LIMIT = 4096
_TRUNCATED_MARKER = "\u2026 [truncated for Telegram]"

GENERIC_ERROR = (
    "Something went wrong. Your saved progress is safe. Please try again in a moment."
)
NOT_FOUND = "I could not find that saved item for your account."
CALLBACK_EXPIRED = "I refreshed the conversation so you can continue from here."
WELCOME = (
    "Welcome to Adaptive Endurance Coach.\n\n"
    "We will begin by learning about your goal, availability, equipment, and "
    "training limitations.\n\n"
    "Let's get started."
)
WELCOME_NEW = WELCOME
SETUP_INTRODUCTION = (
    "Before we begin, I will collect the details needed for your athlete profile."
)
GOAL_INTAKE = (
    "What are you currently training for, and what would success look like?\n\n"
    "For example: 'I am preparing for an Ironman 70.3. I want to finish in a "
    "decent time while maintaining muscle.'\n\n"
    "Include an event date if you know it."
)
COACH_HELP = (
    "How can Adaptive Endurance Coach help me?\n\n"
    "The coach builds your athlete profile from your goal, availability, equipment, "
    "and training limitations. You can also import Apple Health history or TCX "
    "workout files.\n\n"
    "This product is not medical advice and does not generate training plans in this "
    "version."
)
PRIVACY_SAFETY = (
    "Privacy & safety\n\n"
    "Adaptive Endurance Coach is a training support tool. It does not provide "
    "medical advice and must not be used for emergencies.\n\n"
    "You choose what information to provide. This may include injury history or "
    "other health-related training limitations.\n\n"
    "Training-data imports are optional.\n\n"
    "You can cancel the setup at any time, update your information later or request "
    "deletion of your stored data."
)
WELCOME_BACK = "Welcome back. I found your saved progress."
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
CANCEL_CONFIRM = (
    "Cancel the current onboarding? Your staged answers will remain unavailable "
    "until you restart onboarding."
)
CANCELLED = (
    "Onboarding was cancelled. Your saved information has not been deleted. "
    "Use the buttons below when you are ready."
)
DELETE_CONFIRM = (
    "Delete your account and personal application data? This cannot be undone."
)
DELETED = "Your application account and personal data were deleted."
DELETE_FAILED = "I could not delete the local account data. Please try again later."
ACCOUNT_KEPT = "Your account was not deleted."
HELP = (
    "Adaptive Endurance Coach uses visible buttons to guide setup, show your saved "
    "profile and import workouts.\n\n"
    "This product is not medical advice and must not be used for emergencies. "
    "No training plan is generated in this version."
)
ACCOUNT_ACTIONS = "Account actions"
PROFILE_INCOMPLETE = (
    "Your athlete profile is not complete yet. Resume onboarding to continue from "
    "your saved step."
)
ADD_WORKOUT_REQUEST = (
    "Send a TCX workout file or an Apple Health export ZIP.\n\n"
    "TCX is recommended for a single new workout.\n"
    "Apple Health ZIP can update or enrich your history."
)
_ONBOARDING_FIELD_LABELS = {
    "birth_year": "birth year",
    "gender": "category",
    "weight_kg": "weight",
    "height_cm": "height",
    "availability_text": "availability",
    "health_limitations_text": "training limitations",
}
ONBOARDING_MODIFICATION_FALLBACK = "Your athlete data has been updated."


def onboarding_modification_response(confirmation: str | None) -> str:
    """Retain a safe fallback for callers that receive model text."""

    del confirmation
    return ONBOARDING_MODIFICATION_FALLBACK


def onboarding_fields_updated(field_names: Sequence[str]) -> str:
    """Confirm a deterministic sparse update without echoing personal values."""

    labels = [
        _ONBOARDING_FIELD_LABELS[field]
        for field in field_names
        if field in _ONBOARDING_FIELD_LABELS
    ]
    if not labels:
        return "Your athlete data has been updated."
    if len(labels) == 1:
        if labels[0] == "training limitations":
            return "Your training limitations have been updated."
        return f"Your {labels[0]} has been updated."
    return f"Your {', '.join(labels[:-1])} and {labels[-1]} have been updated."


PROFILE_BIRTH_YEAR_INTAKE = (
    "First, let\u2019s build your athlete profile.\n\n"
    "What year were you born? Send the four-digit year (1940 to 2008)."
)
PROFILE_GENDER_INTAKE = "Which is your sex?"
PROFILE_WEIGHT_INTAKE = (
    "What is your current weight in kilograms? Send a number from 40.0 to 200.0."
)
PROFILE_HEIGHT_INTAKE = (
    "What is your height in centimeters? Send a whole number from 120 to 230."
)
AVAILABILITY_INTAKE = (
    "Tell me about your weekly training availability in your own words.\n\n"
    "Include the days you can train, roughly how much time you have, and any places "
    "or facilities you can use if relevant. \nFor example: "
    "'I can ride for up to two hours on the weekend. "
    "I can swim at a pool on Wednesday and Friday, and I can cycle only on weekends."
)
HEALTH_LIMITATIONS_INTAKE = (
    "Write any current or past injuries, discomfort, or physical limitations that "
    "should influence training, or choose None. This is not medical advice."
)
CONTEXT_VALIDATION_ERROR = (
    "Please send a short answer for this part of your athlete profile."
)
EQUIPMENT_UNMATCHED = (
    "I do not have a tailored equipment catalog for that goal yet, so equipment "
    "will not block your onboarding."
)


def equipment_review(review: EquipmentReview) -> str:
    """Render deterministic catalog importance before selection."""

    sections: list[str] = []
    for discipline in review.disciplines:
        rows = [
            (
                "[x]" if item.selected else "[ ]",
                item.display_name,
                item.importance.value.title(),
                ", ".join(item.substitutions) or "\u2014",
            )
            for item in review.options
            if item.discipline is discipline
        ]
        if rows:
            sections.append(
                f"<b>{escape(discipline.value.title())}</b>\n"
                + _html_pre_table(
                    ("Have", "Equipment", "Importance", "Alternatives"),
                    rows,
                    (4, 24, 11, 25),
                )
            )
    message = (
        "Select every item or facility you can currently use. Essential means "
        "needed to train the discipline; a listed alternative can satisfy it.\n\n"
        + "\n\n".join(sections)
    )
    return _assert_telegram_length(message)


def equipment_summary(summary: EquipmentSuggestionSummary) -> str:
    """Render the compact, non-blocking gap summary."""

    status = (
        "You have access to the essentials needed to start."
        if summary.can_start
        else "You can continue, but there are equipment gaps to address."
    )
    rows = [
        (
            "Essential",
            item.discipline.value.title(),
            item.display_name,
            ", ".join(item.substitutions) or "\u2014",
        )
        for item in summary.missing_essentials
    ]
    rows.extend(
        (
            "Recommended",
            item.discipline.value.title(),
            item.display_name,
            ", ".join(item.substitutions) or "\u2014",
        )
        for item in summary.missing_recommended
    )
    if not rows:
        return status
    message = (
        status
        + "\n\n"
        + _html_pre_table(
            ("Gap", "Discipline", "Equipment", "Alternatives"),
            rows,
            (11, 10, 22, 24),
        )
    )
    return _assert_telegram_length(message)


ONBOARDING_COMPLETED = (
    "Your onboarding is complete. You can change your profile settings at any time."
)
PROFILE_SETTINGS_MENU = "Choose a profile setting to change."
PROFILE_SETTINGS_CLOSED = "Done. Your profile settings are closed."
PROFILE_SETTINGS_UNPROMPTED = "Use Change profile to choose what you want to update."
PROFILE_GOAL_MENU = "Choose the training goal field to change."
PROFILE_GOAL_MAIN = "What is your training goal?"
PROFILE_GOAL_OUTCOME = "What would success or the outcome look like?"
PROFILE_GOAL_DATE = "When is the event? Send YYYY-MM-DD, or choose Not yet."
PROFILE_GOAL_SECONDARY = (
    "What secondary priority should this goal preserve, or choose None?"
)
PROFILE_AVAILABILITY = "Describe your weekly training availability."
PROFILE_HEALTH = "Write any training limitations, or choose None."
PROFILE_PERSONAL = "Choose the personal detail to change."
PROFILE_BIRTH_YEAR = "Send your birth year."
PROFILE_WEIGHT = "Send your weight in kilograms."
PROFILE_HEIGHT = "Send your height in centimeters."
PROFILE_CATEGORY = "Choose your category."
PROFILE_SAVED = "Saved: {field}."
GOAL_OFF_TOPIC = (
    "We can come back to equipment later. Right now, I\u2019m building your athlete "
    "profile.\n\n"
    "What are you currently training for, and what would success look like to you?"
)


def goal_clarification(answers: Mapping[str, Any]) -> str:
    field = answers.get("_goal_clarification_field")
    hint = answers.get("_goal_clarification_hint")
    if field == "main_goal":
        if hint == "race":
            return "Which race or challenge are you preparing for?"
        if hint == "distance":
            return "What distance would you most like to reach?"
        if hint == "pace":
            return "Which distance or pace would you most like to improve?"
        if hint == "other":
            return "Tell me what you would most like to achieve with your training."
        return (
            "I\u2019d like to make that goal a little more specific so I can "
            "understand "
            "what you are working towards.\n\n"
            "What would you most like to achieve with running?"
        )
    if field == "target_outcome":
        return (
            "What would success look like for this goal?\n\n"
            "For example, completing it safely, finishing without stopping, or "
            "reaching a specific time or pace."
        )
    if field == "event_date":
        return "When is the event? Send YYYY-MM-DD, or choose Not yet."
    return "Could you make your goal a little more specific?"


def goal_confirmation(answers: Mapping[str, Any]) -> str:
    raw_draft = answers.get("goal_draft")
    draft = raw_draft if isinstance(raw_draft, Mapping) else {}
    main_goal = escape(str(draft.get("main_goal") or "Not specified"))
    target_outcome = escape(str(draft.get("target_outcome") or "Not specified"))
    secondary = escape(str(draft.get("secondary_priority") or "Not specified"))
    raw_date = draft.get("event_date")
    event_date = "Not set"
    if isinstance(raw_date, str):
        try:
            event_date = date.fromisoformat(raw_date).strftime("%B %d, %Y")
        except ValueError:
            event_date = "Not set"
    return (
        "Here\u2019s what I understood:\n\n"
        f"Main goal\n{main_goal}\n\n"
        f"Event date\n{event_date}\n\n"
        f"Target outcome\n{target_outcome}\n\n"
        f"Secondary priority\n{secondary}\n\n"
        "To change anything just write."
    )


VALIDATION_ERRORS: dict[str, str] = {
    "invalid_action": CALLBACK_EXPIRED,
    "stale_action": CALLBACK_EXPIRED,
    "onboarding_not_active": CALLBACK_EXPIRED,
    "restart_not_allowed": CALLBACK_EXPIRED,
    "parse_in_progress": (
        "I am still interpreting your previous answer. Please wait for that result."
    ),
    "incomplete_profile": (
        "Some required answers are missing. Your progress is safe; resume onboarding "
        "to complete them."
    ),
    "invalid_birth_year": ("Enter a four-digit birth year from 1940 to 2008."),
    "invalid_weight_kg": (
        "Enter your weight in kilograms as a number from 40.0 to 200.0."
    ),
    "invalid_height_cm": (
        "Enter your height in centimeters as a whole number from 120 to 230."
    ),
    "invalid_event_date": "Enter a future event date as YYYY-MM-DD.",
    "apple_health_import_disabled": ("Apple Health import is currently unavailable."),
    "import_already_active": ("An Apple Health import is already in progress."),
    "training_file_not_expected": (
        "Use Add workout from your account menu before sending a training file."
    ),
    "training_file_import_disabled": "Training-file import is currently unavailable.",
    "unsupported_training_file": (
        "That document is not a supported Apple Health ZIP or TCX workout file. "
        "The temporary upload was deleted."
    ),
    "training_file_import_failed": (
        "That training file could not be imported safely. The temporary upload "
        "was deleted."
    ),
    "training_file_import_cancelled": (
        "The training-file import was cancelled. The temporary upload was deleted."
    ),
    "import_interrupted": (
        "A previous training-file import was interrupted. Send the file again."
    ),
    "training_file_size_exceeded": (
        "That training file is larger than the allowed limit."
    ),
    "archive_compressed_size_exceeded": (
        "That Apple Health ZIP is larger than the allowed limit."
    ),
    "archive_not_zip": "That document is not a valid Apple Health ZIP.",
    "archive_empty": "That Apple Health ZIP is empty.",
    "invalid_archive": "That Apple Health ZIP could not be read safely.",
    "health_data_xml_not_found": (
        "That ZIP does not contain an Apple Health export.xml file."
    ),
    "unsafe_xml_entity": "That XML contains unsafe entities and was rejected.",
    "unsafe_external_dtd": "That XML contains an unsafe DTD and was rejected.",
    "unsafe_xml_encoding": (
        "That XML encoding is not supported safely. Export the file as UTF-8."
    ),
    "tcx_import_disabled": "TCX import is currently unavailable.",
    "tcx_size_exceeded": "That TCX file is larger than the allowed limit.",
    "tcx_file_size_exceeded": "That TCX file is larger than the allowed limit.",
    "tcx_file_unavailable": "That TCX file could not be opened safely.",
    "malformed_tcx_xml": "That TCX file could not be read safely.",
    "unsafe_xml_doctype": "That TCX file contains an unsafe DTD and was rejected.",
    "tcx_root_invalid": "That XML document is not a supported TCX file.",
    "tcx_namespace_unsupported": (
        "That TCX namespace is not supported. Export the workout as TCX v1 or v2."
    ),
    "tcx_activity_not_found": ("That TCX file does not contain a workout activity."),
    "tcx_lap_not_found": "That TCX activity does not contain a workout lap.",
    "tcx_activity_identity_missing": (
        "That TCX activity has no usable identifier or start time."
    ),
    "tcx_multiple_activities_not_supported": (
        "Send one workout activity per TCX file."
    ),
}

TRAINING_FILE_PROGRESS = {
    "validating_file": "Validating file",
    "detecting_format": "Detecting file format",
    "validating_archive": "Validating archive",
    "reading_tcx": "Reading TCX workout",
    "reading_workouts": "Reading workouts",
    "reading_heart_rate": "Reading heart-rate records",
    "matching_data": "Matching data",
    "saving_activities": "Saving activities",
}
APPLE_HEALTH_PROGRESS = TRAINING_FILE_PROGRESS


def apple_health_file_result(
    *,
    activities_imported: int,
    activities_updated: int,
    activities_skipped: int,
) -> str:
    """Render one bulk-file outcome for an existing athlete."""

    lines = [
        "Apple Health history imported",
        "",
        f"Activities imported: {activities_imported}",
        f"Activities updated: {activities_updated}",
        f"Activities skipped: {activities_skipped}",
        "",
        "Your training history was updated.",
    ]
    return "\n".join(lines)


def tcx_workout_result(
    *,
    sport: object,
    started_at: object,
    duration_seconds: int,
    distance_meters: float | None,
    average_heart_rate: float | None,
) -> str:
    """Render one canonical TCX workout without inventing missing metrics."""

    suffix = "The workout is saved."
    heart_rate = (
        "Unavailable"
        if average_heart_rate is None
        else f"{round(average_heart_rate)} bpm"
    )
    return "\n".join(
        [
            "Workout imported",
            "",
            f"Sport: {_display(sport)}",
            f"Date: {_date_time(started_at)}",
            f"Duration: {_duration(duration_seconds)}",
            (
                "Distance: Unavailable"
                if distance_meters is None
                else f"Distance: {_distance(distance_meters)}"
            ),
            f"Average heart rate: {heart_rate}",
            "",
            suffix,
        ]
    )


def validation_error(code: str) -> str:
    """Map a safe application error code to English copy."""

    return VALIDATION_ERRORS.get(code, GENERIC_ERROR)


def persisted_profile(profile: Mapping[str, Any]) -> str:
    """Render the current mandatory athlete profile."""

    equipment = profile.get("equipment_access")
    training_goal = profile.get("training_goal")
    free_text_values = [
        value
        for key in ("availability_text", "health_limitations_text")
        if isinstance((value := profile.get(key)), str) and value
    ]
    if isinstance(training_goal, Mapping):
        free_text_values.extend(
            value
            for key in (
                "main_goal",
                "target_outcome",
                "secondary_priority",
            )
            if isinstance((value := training_goal.get(key)), str) and value
        )

    def render(free_text_cap: int | None) -> str:
        lines = [
            "Your saved athlete profile:",
            "",
            f"Birth year: {_display(profile.get('birth_year'))}",
            f"Category: {_display(profile.get('gender'))}",
            f"Weight: {_optional_metric(profile.get('weight_kg'), 'kg')}",
            f"Height: {_optional_metric(profile.get('height_cm'), 'cm')}",
        ]
        for label, key in (
            ("Availability", "availability_text"),
            ("Training limitations", "health_limitations_text"),
        ):
            value = profile.get(key)
            if isinstance(value, str) and value:
                lines.append(f"{label}: {_profile_free_text(value, free_text_cap)}")
        if isinstance(training_goal, Mapping):
            lines.extend(["", "<b>Training goal</b>"])
            for label, key in (
                ("Main goal", "main_goal"),
                ("Target outcome", "target_outcome"),
                ("Secondary priority", "secondary_priority"),
            ):
                value = training_goal.get(key)
                rendered = (
                    _profile_free_text(value, free_text_cap)
                    if isinstance(value, str) and value
                    else "Not set"
                )
                lines.append(f"{label}: {rendered}")
            lines.append(
                f"Event date: {_readable_date(training_goal.get('event_date'))}"
            )
            lines.append(f"Status: {_plain_display(training_goal.get('status'))}")
        if isinstance(equipment, (list, tuple)) and equipment:
            rows: list[tuple[str, str, str]] = []
            for item in equipment:
                if isinstance(item, Mapping):
                    rows.append(
                        (
                            _plain_display(item.get("discipline")),
                            str(item.get("display_name") or "Not set"),
                            _plain_display(item.get("importance")),
                        )
                    )
                else:
                    rows.append(("Not set", str(item), "Not set"))
            lines.extend(
                [
                    "",
                    "<b>Equipment access</b>",
                    _html_pre_table(
                        ("Discipline", "Equipment", "Importance"),
                        rows,
                        (11, 27, 11),
                    ),
                ]
            )
        else:
            lines.extend(["", "Equipment access: Not set"])
        return "\n".join(lines)

    message = render(None)
    if len(message) <= TELEGRAM_MESSAGE_LIMIT:
        return message
    low, high = 0, max(map(len, free_text_values), default=0)
    while low < high:
        midpoint = (low + high + 1) // 2
        if len(render(midpoint)) <= TELEGRAM_MESSAGE_LIMIT:
            low = midpoint
        else:
            high = midpoint - 1
    return _assert_telegram_length(render(low))


def _profile_free_text(value: str, cap: int | None) -> str:
    if value == "NONE_REPORTED":
        return "None reported"
    if cap is not None and len(value) > cap:
        return escape(value[:cap] + _TRUNCATED_MARKER)
    return escape(value)


def profile_setting_prompt(
    step: ProfileSettingsStep,
    current_value: str | int | float | None,
    prompt: str,
) -> str:
    """Add the saved value to an edit prompt without changing stored data."""

    labels = {
        ProfileSettingsStep.GOAL_MAIN: "Current goal",
        ProfileSettingsStep.GOAL_OUTCOME: "Current outcome",
        ProfileSettingsStep.GOAL_DATE: "Current event date",
        ProfileSettingsStep.GOAL_SECONDARY: "Current secondary priority",
        ProfileSettingsStep.AVAILABILITY: "Current availability",
        ProfileSettingsStep.HEALTH: "Current training limitations",
        ProfileSettingsStep.PERSONAL_BIRTH_YEAR: "Current birth year",
        ProfileSettingsStep.PERSONAL_GENDER: "Current category",
        ProfileSettingsStep.PERSONAL_WEIGHT: "Current weight",
        ProfileSettingsStep.PERSONAL_HEIGHT: "Current height",
    }
    label = labels.get(step)
    if label is None:
        return prompt
    display = _profile_setting_value(step, current_value)
    prefix = f"<b>{escape(label)}:</b>\n<pre>"
    suffix = f"</pre>\n\n{prompt}"
    budget = TELEGRAM_MESSAGE_LIMIT - len(prefix) - len(suffix)
    rendered = escape(display)
    if len(rendered) > budget:
        rendered = _escaped_prefix(display, budget - len(_TRUNCATED_MARKER))
        rendered += escape(_TRUNCATED_MARKER)
    return _assert_telegram_length(prefix + rendered + suffix)


def _profile_setting_value(
    step: ProfileSettingsStep, value: str | int | float | None
) -> str:
    if value is None or value == "":
        return "Not set"
    if step is ProfileSettingsStep.GOAL_DATE:
        try:
            parsed = date.fromisoformat(str(value))
        except ValueError:
            return "Not set"
        return f"{parsed.strftime('%B %d, %Y')} ({parsed.isoformat()})"
    if step is ProfileSettingsStep.PERSONAL_WEIGHT:
        return f"{value} kg"
    if step is ProfileSettingsStep.PERSONAL_HEIGHT:
        return f"{value} cm"
    if step is ProfileSettingsStep.PERSONAL_GENDER:
        return _plain_display(value)
    if step is ProfileSettingsStep.HEALTH and str(value) == "NONE_REPORTED":
        return "None reported"
    return str(value)


def _readable_date(value: Any) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return f"{value.strftime('%B %d, %Y')} ({value.isoformat()})"
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            return "Not set"
        return f"{parsed.strftime('%B %d, %Y')} ({parsed.isoformat()})"
    return "Not set"


def _html_pre_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    widths: Sequence[int],
) -> str:
    normalized = [
        [_table_cell(value, width) for value, width in zip(row, widths, strict=True)]
        for row in (headers, *rows)
    ]
    lines = [
        "  ".join(value.ljust(width) for value, width in zip(row, widths, strict=True))
        for row in normalized
    ]
    separator = "  ".join("-" * width for width in widths)
    lines.insert(1, separator)
    return f"<pre>{escape(chr(10).join(lines))}</pre>"


def _table_cell(value: Any, width: int) -> str:
    cleaned = " ".join(str(value).split())
    if len(cleaned) <= width:
        return cleaned
    return cleaned[: max(0, width - 1)] + "\u2026"


def _plain_display(value: Any) -> str:
    if value is None or value == "":
        return "Not set"
    raw = getattr(value, "value", value)
    return str(raw).replace("_", " ").title()


def _escaped_prefix(value: str, budget: int) -> str:
    if budget <= 0:
        return ""
    low, high = 0, len(value)
    while low < high:
        midpoint = (low + high + 1) // 2
        if len(escape(value[:midpoint])) <= budget:
            low = midpoint
        else:
            high = midpoint - 1
    return escape(value[:low])


def _assert_telegram_length(message: str) -> str:
    if len(message) > TELEGRAM_MESSAGE_LIMIT:
        raise ValueError("Telegram message exceeds 4,096 characters")
    return message


def _html_page(
    title: str,
    body: str,
    *,
    action_label: str | None = None,
    action_url: str | None = None,
) -> str:
    action = ""
    if action_label is not None and action_url is not None:
        action = (
            f'<p><a href="{escape(action_url, quote=True)}">'
            f"{escape(action_label)}</a></p>"
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{escape(title)}</title></head><body><main><h1>{escape(title)}</h1>"
        f"<p>{escape(body)}</p>{action}</main></body></html>"
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
