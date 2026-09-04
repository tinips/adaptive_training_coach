"""Centralized English user-facing messages and renderers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from html import escape
from typing import Any

from app.domain.enums import ProfileSettingsStep
from app.schemas.capabilities import CapabilityReview, GoalExecutionAssessment
from app.schemas.weekly_plans import FirstWeekPlan, PlanReadiness, WeeklyPlan

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
GOAL_INTAKE = "Which sport is your goal in?"
GOAL_TEMPLATE_PROMPT = "Which goal best matches what you're training for?"
GOAL_SWIMMING_TYPE_PROMPT = "Where will your main swim goal take place?"
GOAL_EVENT_DATE_PROMPT = (
    "When is your race? Send the date as YYYY-MM-DD (e.g. 2027-07-11) or "
    "DD/MM/YYYY (e.g. 11/07/2027), or tap below if you don't have a date yet."
)


def goal_metric_prompt(field: str) -> str:
    prompts = {
        "running_distance": (
            "What distance are you targeting? Send kilometres, for example 12.5."
        ),
        "running_pace": (
            "What is your target pace? Send min:sec per km, for example 5:30, "
            "or tap Skip."
        ),
        "elevation": (
            "What elevation gain are you targeting? Send metres, for example 1200, "
            "or tap Skip."
        ),
        "cycling_distance": (
            "What distance are you targeting? Send kilometres, for example 100."
        ),
        "cycling_average_speed": (
            "What average speed are you targeting? Send km/h, for example 28.5, "
            "or tap Skip."
        ),
        "swimming_distance": (
            "What distance are you targeting? Send metres, for example 1500."
        ),
        "swimming_pace": (
            "What is your target pace? Send min:sec per 100 m, for example 2:05, "
            "or tap Skip."
        ),
        "triathlon_finish_time": (
            "What total finish time are you targeting? Send hours and minutes as "
            "H:MM, for example 2:45, or tap Skip."
        ),
    }
    return prompts[field]


GOAL_SUPPORT_PROMPT = (
    "Would you like to add a supporting goal, such as maintaining strength? "
    "You can skip this."
)
COACH_HELP = (
    "How can Adaptive Endurance Coach help me?\n\n"
    "The coach builds your athlete profile from your goal, availability, equipment, "
    "and training limitations. Add completed workouts by sending a screenshot.\n\n"
    "This product is not medical advice and does not generate training plans in this "
    "version."
)
PRIVACY_SAFETY = (
    "Privacy & safety\n\n"
    "Adaptive Endurance Coach is a training support tool. It does not provide "
    "medical advice and must not be used for emergencies.\n\n"
    "You choose what information to provide. This may include injury history or "
    "other health-related training limitations.\n\n"
    "Workout screenshots are optional.\n\n"
    "You can cancel the setup at any time, update your information later or request "
    "deletion of your stored data."
)
WELCOME_BACK = "Welcome back. I found your saved progress."
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
    "profile and add workout screenshots.\n\n"
    "This product is not medical advice and must not be used for emergencies. "
    "No training plan is generated in this version."
)
ACCOUNT_ACTIONS = "Account actions"
PROFILE_INCOMPLETE = (
    "Your athlete profile is not complete yet. Resume onboarding to continue from "
    "your saved step."
)
PROFILE_DISCARD_CHANGES = (
    "You have an availability draft that has not been saved. Discard it, or keep "
    "editing?"
)
WEEKLY_PLAN_UNAVAILABLE = (
    "I could not create a weekly plan right now. No plan was saved. Please try again "
    "in a moment."
)
WEEKLY_PLAN_NOT_FOUND = (
    "There is no saved plan for next week yet. Choose Start your first week "
    "when you are ready."
)
WEEKLY_PLAN_DELETE_CONFIRM = (
    "Delete this weekly plan? You can generate a new one immediately afterwards."
)
WEEKLY_PLAN_DELETED = "Weekly plan deleted. You can now start your first week again."
WEEKLY_PLAN_DELETE_NOT_FOUND = "There is no weekly plan to delete."
_ONBOARDING_FIELD_LABELS = {
    "birth_year": "birth year",
    "gender": "category",
    "weight_kg": "weight",
    "height_cm": "height",
    "timezone": "timezone",
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
PROFILE_TIMEZONE_INTAKE = (
    "What is your IANA timezone? Send a value such as Europe/Madrid. This lets "
    "the coach use your local Monday when it plans your week."
)
AVAILABILITY_INTAKE = (
    "Tell me about your weekly training availability in your own words.\n\n"
    "Include the days you can train and roughly how much time you have during the week."
    "\n\nFor example: "
    "\n'I can ride for up to two hours on the weekend. "
    "I can swim at a pool on Wednesday and Friday, and I can cycle only on weekends.'"
)
AVAILABILITY_CLARIFICATION = (
    "I couldn't map that to a weekly schedule. Please list the days, activities, "
    "and duration for each day."
)


def availability_review(schedule: object) -> str:
    table = _availability_table(schedule)
    if table is None:
        return GENERIC_ERROR
    return (
        "Review your weekly availability:\n"
        f"{table}\n\nConfirm it, or edit by sending a corrected description."
    )


def profile_availability_prompt(schedule: object) -> str:
    """Show the current structured schedule before accepting a change request."""

    table = _availability_table(schedule)
    if table is None:
        return (
            "You do not have confirmed weekly availability yet. Describe the days, "
            "activities, and approximate time you have for training."
        )
    return (
        "Current weekly availability:\n"
        f"{table}\n\nTell me what you want to change. For example: "
        "'Make Tuesday evening swimming for 45 minutes instead.'"
    )


HEALTH_LIMITATIONS_INTAKE = (
    "Write any current or past injuries, discomfort, or physical limitations that "
    "should influence training, or choose None. This is not medical advice."
)
TRAINING_HISTORY_IMPORT = (
    "Optional: add your workouts from the last 3 months so future coaching can "
    "use your actual training data. Upload a workout file or skip for now. "
    "Only workout data is imported."
)
TRAINING_HISTORY_FILE_PROMPT = (
    "Send a TCX workout file containing workouts from the last 3 months. "
    "Only workout data is imported."
)
CONTEXT_VALIDATION_ERROR = (
    "Please send a short answer for this part of your athlete profile."
)
EQUIPMENT_UNMATCHED = (
    "I do not have a tailored equipment catalog for that goal yet, so equipment "
    "will not block your onboarding."
)


def equipment_review(review: CapabilityReview) -> str:
    """Render goal-scoped equipment and access capability choices."""

    contexts = ", ".join(item.display_name for item in review.contexts)
    rows = [
        (
            "[x]" if item.selected else "[ ]",
            item.display_name,
            item.kind.value.title(),
            "/".join(role.value.title() for role in item.execution_roles),
        )
        for item in review.options
    ]
    message = (
        "Select every resource you can currently use. These choices are relevant "
        f"to: {escape(contexts)}.\n\n"
        + _html_pre_table(
            ("Have", "Resource", "Type", "Role"),
            rows,
            (4, 25, 10, 20),
        )
    )
    return _assert_telegram_length(message)


def equipment_summary(summary: GoalExecutionAssessment) -> str:
    """Render the compact deterministic execution assessment."""

    rows = [
        (
            item.target_display_name,
            item.status.value.replace("_", " ").title(),
            item.default_execution or "\u2014",
            ", ".join(item.missing_required) or "\u2014",
        )
        for item in summary.contexts
    ]
    message = "Equipment & access summary\n\n" + _html_pre_table(
        ("Context", "Status", "Execution", "Missing"),
        rows,
        (18, 23, 18, 22),
    )
    return _assert_telegram_length(message)


ONBOARDING_COMPLETED = (
    "Your onboarding is complete. You can change your profile settings at any time."
)
TRAINING_HISTORY_SKIP_SUGGESTION = (
    "That's fine — we'll start conservatively. You can import a TCX workout later "
    "to give your coaching a more personalized starting point."
)


def weekly_plan_readiness(readiness: PlanReadiness) -> str:
    """Explain the deterministic evidence gate without exposing workout details."""

    return (
        "I need a little more recent training history before I can create a "
        "personalized plan for next week.\n\n"
        "Across all your sports together I need at least 3 sessions on at "
        "least 2 different days in the last 30 days. Right now I have "
        f"{readiness.total_session_count} "
        f"session{'' if readiness.total_session_count == 1 else 's'} on "
        f"{readiness.total_active_day_count} "
        f"day{'' if readiness.total_active_day_count == 1 else 's'}.\n\n"
        "Import a TCX workout file, then try again."
    )


def weekly_plan(plan: WeeklyPlan | FirstWeekPlan) -> str:
    """Render the saved seven-day structure compactly for Telegram."""

    if isinstance(plan, FirstWeekPlan):
        return first_week_menu(plan)

    lines = [f"Your plan for the week of {plan.week_start.isoformat()}"]
    for day in plan.days:
        heading = day.date.strftime("%A %d %b")
        if not day.sessions:
            lines.append(f"\n<b>{heading}</b> — {escape(day.rest_note or 'Rest')}")
            continue
        rendered = []
        for session in day.sessions:
            rpe_range = session.intensity.rpe_range
            rendered.append(
                f"• <b>{escape(session.discipline.value.title())}</b> — "
                f"{escape(session.purpose)} ({session.targets.duration_minutes} min, "
                f"RPE {rpe_range[0]}-{rpe_range[1]})\n"
                f"  {escape(session.objective)}\n"
                f"  {escape(session.intensity.guidance)}\n"
                f"  {escape(session.execution)}"
            )
        lines.append(f"\n<b>{heading}</b>\n" + "\n".join(rendered))
    return _assert_telegram_length("\n".join(lines))


def first_week_menu(plan: FirstWeekPlan) -> str:
    """Render the first-week probe as an athlete-placed session menu."""

    lines = [f"Your first-week training menu ({plan.week_start.isoformat()})"]
    for number, session in enumerate(plan.sessions, start=1):
        rpe_range = session.intensity.rpe_range
        lines.append(
            f"\n<b>{number}. {escape(session.discipline.value.title())}</b> â€” "
            f"{escape(session.purpose)} ({session.targets.duration_minutes} min, "
            f"RPE {rpe_range[0]}-{rpe_range[1]})\n"
            f"  {escape(session.objective)}\n"
            f"  {escape(session.intensity.guidance)}\n"
            f"  {escape(session.execution)}"
        )
    lines.append("\n<b>Placement guardrails</b>")
    lines.extend(f"â€¢ {escape(rule)}" for rule in plan.guardrails)
    lines.append("\n<b>What to log</b>")
    lines.extend(f"â€¢ {escape(item)}" for item in plan.logging_instructions)
    return _assert_telegram_length("\n".join(lines))


PROFILE_SETTINGS_MENU = "Choose a profile setting to change."
PROFILE_SETTINGS_CLOSED = "Done. Your profile settings are closed."
PROFILE_SETTINGS_UNPROMPTED = "Use Change profile to choose what you want to update."
PROFILE_GOAL_MENU = "Choose the training goal field to change."
PROFILE_GOAL_MAIN_SPORT = "Which sport is your new goal in?"
PROFILE_GOAL_MAIN_TEMPLATE = "Which goal best matches what you're training for?"
PROFILE_GOAL_METRICS = "Set the performance targets for this goal."
PROFILE_GOAL_DATE = "When is the event? Send YYYY-MM-DD, or choose Not yet."
PROFILE_GOAL_SECONDARY = (
    "Would you like to add or change a supporting goal, such as maintaining "
    "strength? You can remove it instead."
)
PROFILE_AVAILABILITY = "Describe your weekly training availability."
PROFILE_HEALTH = "Write any training limitations, or choose None."
PROFILE_PERSONAL = "Choose the personal detail to change."
PROFILE_BIRTH_YEAR = "Send your birth year."
PROFILE_WEIGHT = "Send your weight in kilograms."
PROFILE_HEIGHT = "Send your height in centimeters."
PROFILE_TIMEZONE = "Send your IANA timezone, for example Europe/Madrid."
PROFILE_CATEGORY = "Choose your category."
PROFILE_SAVED = "Saved: {field}."
GOAL_CATALOG_EXPANSION_PROGRESS = (
    "Processing your goal...\n\n"
    "I\u2019m preparing the training contexts and equipment/access requirements. "
    "This may take a moment."
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
    "invalid_timezone": "Send a valid IANA timezone, for example Europe/Madrid.",
    "invalid_event_date": ("Enter a future race date as YYYY-MM-DD or DD/MM/YYYY."),
    "import_already_active": ("A training-file import is already in progress."),
    "training_file_not_expected": (
        "Send a completed-workout screenshot to add a workout."
    ),
    "training_file_import_disabled": "Training-file import is currently unavailable.",
    "unsupported_training_file": (
        "That document is not a supported TCX workout file. "
        "The temporary upload was deleted."
    ),
    "training_file_import_failed": (
        "That training file could not be imported safely. The temporary upload "
        "was deleted."
    ),
    "training_file_import_cancelled": (
        "The training-file import was cancelled. The temporary upload was deleted."
    ),
    "training_file_no_workouts": (
        "That file did not contain any supported workouts. Try another file or "
        "choose Skip for now."
    ),
    "import_interrupted": (
        "A previous training-file import was interrupted. Send the file again."
    ),
    "training_file_size_exceeded": (
        "That training file is larger than the allowed limit."
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
    "reading_tcx": "Reading TCX workout",
    "matching_data": "Matching data",
    "saving_activities": "Saving activities",
}

SCREENSHOT_READING = "Reading the screenshot..."
SCREENSHOT_DISABLED = "Workout screenshot import is not enabled yet."
SCREENSHOT_ATHLETE_NOT_FOUND = "This Telegram account is not linked to a Coach profile."
SCREENSHOT_EXTRACTION_FAILED = (
    "Could not read a workout from that image. Try a clearer screenshot "
    "of a single workout summary."
)
SCREENSHOT_DRAFT_HEADER = "Here's what I read from the screenshot:"
SCREENSHOT_CONFIRM_PROMPT = "Save this workout?"
SCREENSHOT_CONFIRM_BUTTON = "Save"
SCREENSHOT_CANCEL_BUTTON = "Discard"
SCREENSHOT_DISCARDED = "Discarded. Nothing was saved."
SCREENSHOT_DRAFT_EXPIRED = "This confirmation has expired. Send the screenshot again."
SCREENSHOT_IMPORT_INVALID = (
    "That workout could not be saved — some of the extracted values were not valid."
)
SCREENSHOT_SAVED = "Saved as a new workout."
SCREENSHOT_UPDATED = "Updated the matching existing workout."
SCREENSHOT_UNCHANGED = "Already saved — nothing changed."


def training_file_result(
    *,
    file_format: object,
    activities_imported: int,
    activities_updated: int,
    activities_skipped: int,
) -> str:
    return "\n".join(
        [
            "TCX workout imported",
            "",
            f"Activities imported: {activities_imported}",
            f"Activities updated: {activities_updated}",
            f"Activities skipped: {activities_skipped}",
            "",
            "Your training history was updated.",
        ]
    )


def onboarding_history_imported(
    *,
    file_format: object,
    activities_imported: int,
    activities_updated: int,
    activities_skipped: int,
) -> str:
    return "\n".join(
        [
            training_file_result(
                file_format=file_format,
                activities_imported=activities_imported,
                activities_updated=activities_updated,
                activities_skipped=activities_skipped,
            ),
            "",
            ONBOARDING_COMPLETED,
        ]
    )


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
        for key in ("health_limitations_text",)
        if isinstance((value := profile.get(key)), str) and value
    ]
    if isinstance(training_goal, Mapping):
        free_text_values.extend(
            value
            for key in (
                "main_goal",
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
            f"Timezone: {_display(profile.get('timezone'))}",
        ]
        availability = _availability_table(profile.get("weekly_availability"))
        if availability is not None:
            lines.extend(["", "<b>Weekly availability</b>", availability])
        for label, key in (("Training limitations", "health_limitations_text"),):
            value = profile.get(key)
            if isinstance(value, str) and value:
                lines.append(f"{label}: {_profile_free_text(value, free_text_cap)}")
        if isinstance(training_goal, Mapping):
            lines.extend(["", "<b>Training goal</b>"])
            for label, key in (
                ("Main goal", "main_goal"),
                ("Primary template", "primary_template"),
                ("Secondary priority", "secondary_priority"),
                ("Supporting template", "supporting_template"),
            ):
                value = training_goal.get(key)
                rendered = (
                    _profile_free_text(value, free_text_cap)
                    if isinstance(value, str) and value
                    else "Not set"
                )
                lines.append(f"{label}: {rendered}")
            target_lines = _performance_target_lines(training_goal)
            if target_lines:
                lines.append("Performance targets: " + "; ".join(target_lines))
            lines.append(
                f"Event date: {_readable_date(training_goal.get('event_date'))}"
            )
        if isinstance(equipment, (list, tuple)) and equipment:
            rows: list[tuple[str, str, str]] = []
            for item in equipment:
                if isinstance(item, Mapping):
                    rows.append(
                        (
                            _plain_display(item.get("kind")),
                            str(item.get("display_name") or "Not set"),
                            str(item.get("code") or "Not set"),
                        )
                    )
                else:
                    rows.append(("Not set", str(item), "Not set"))
            lines.extend(
                [
                    "",
                    "<b>Equipment &amp; access</b>",
                    _html_pre_table(
                        ("Type", "Resource", "Code"),
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


def _performance_target_lines(goal: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    distance = goal.get("target_distance_km")
    if isinstance(distance, (int, float)):
        lines.append(f"distance {distance:g} km")
    elevation = goal.get("target_elevation_m")
    if isinstance(elevation, (int, float)):
        lines.append(f"elevation {elevation:g} m")
    pace = goal.get("target_pace_seconds_per_km")
    if isinstance(pace, (int, float)):
        lines.append(f"run pace {_duration(int(pace))}/km")
    swim_pace = goal.get("target_swim_pace_seconds_per_100m")
    if isinstance(swim_pace, (int, float)):
        lines.append(f"swim pace {_duration(int(swim_pace))}/100 m")
    speed = goal.get("target_average_speed_kph")
    if isinstance(speed, (int, float)):
        lines.append(f"average speed {speed:g} km/h")
    finish = goal.get("target_finish_time_seconds")
    if isinstance(finish, int):
        lines.append(f"finish time {_duration(finish)}")
    return lines


def _availability_table(schedule: object) -> str | None:
    if not isinstance(schedule, Mapping):
        return None
    days = schedule.get("days")
    if not isinstance(days, Mapping):
        return None
    rows: list[tuple[str, str, str, str]] = []
    for day in (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ):
        details = days.get(day)
        if not isinstance(details, Mapping) or not details.get("available"):
            rows.append((day.title()[:3], "Off", "-", "-"))
            continue
        windows = details.get("time_windows")
        if not isinstance(windows, (list, tuple)):
            return None
        slots: list[str] = []
        duration = 0
        for window in windows:
            if not isinstance(window, Mapping):
                return None
            minutes = window.get("duration_minutes")
            if (
                not isinstance(minutes, int)
                or isinstance(minutes, bool)
                or minutes <= 0
            ):
                return None
            duration += minutes
            time_of_day = window.get("time_of_day")
            slots.append(
                {
                    "morning": "AM",
                    "afternoon": "PM",
                    "evening": "Eve",
                    "night": "Night",
                }.get(str(time_of_day), "Any")
            )
        disciplines = details.get("disciplines")
        if not isinstance(disciplines, (list, tuple)) or not disciplines:
            return None
        sports = "/".join(
            {
                "running": "Run",
                "cycling": "Bike",
                "swimming": "Swim",
                "strength_training": "Str",
            }.get(str(item), _plain_display(item))
            for item in disciplines
        )
        rows.append(
            (
                day.title()[:3],
                "/".join(slots),
                f"{duration}m",
                sports,
            )
        )
    return (
        _html_pre_table(("Day", "When", "Min", "Sports"), rows, (3, 10, 5, 18))
        + "\nRun=running · Bike=cycling · Swim=swimming · Str=strength"
    )


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
        ProfileSettingsStep.GOAL_DATE: "Current event date",
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
