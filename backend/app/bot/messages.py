"""Centralized English user-facing messages and renderers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from html import escape
from typing import Any

from app.domain.enums import SyncStatus

GENERIC_ERROR = (
    "Something went wrong. Your saved progress is safe. Please try again in a moment."
)
NOT_FOUND = "I could not find that saved item for your account."
CALLBACK_EXPIRED = "I refreshed the conversation so you can continue from here."
WELCOME = (
    "Welcome to Adaptive Endurance Coach 👋\n\n"
    "I\u2019m glad you\u2019re here.\n\n"
    "Every meaningful goal starts with understanding where you are today.\n\n"
    "I\u2019m here to help you train with more clarity, make better decisions and "
    "build "
    "a realistic path towards your fitness goals — one session at a time.\n\n"
    "We\u2019ll begin by learning about you, your experience, your availability and "
    "your recent training. From there, we\u2019ll establish your current fitness "
    "baseline and "
    "build the foundation for everything that comes next.\n\n"
    "Let\u2019s get started."
)
WELCOME_NEW = WELCOME
COACH_HELP = (
    "How can Adaptive Endurance Coach help me?\n\n"
    "The coach starts by understanding you as an athlete — not just your latest "
    "workout.\n\n"
    "It considers:\n\n"
    "• Your fitness and race goals\n"
    "• Your experience in each sport\n"
    "• Your weekly availability\n"
    "• Your training environment and equipment\n"
    "• Your injury history and physical limitations\n"
    "• Your recent training data\n"
    "• How your sessions feel, not only the numbers\n\n"
    "Using this information, the coach builds your athlete profile and establishes "
    "your current fitness baseline.\n\n"
    "Your baseline is a structured picture of where you are today. It includes your "
    "recent training volume, consistency, experience, current capabilities and any "
    "limitations that should influence your training.\n\n"
    "You can strengthen this baseline by:\n\n"
    "• Connecting Strava\n"
    "• Importing Apple Health data\n"
    "• Uploading TCX workout files\n"
    "• Recording workouts manually\n"
    "• Adding effort, fatigue and discomfort feedback\n\n"
    "This foundation will help the coach understand your progress and support better "
    "training decisions.\n\n"
    "The current version focuses on building your profile, importing your training "
    "history and establishing a reliable baseline. Adaptive planning, calendars and "
    "progress dashboards will be introduced in future versions."
)
PRIVACY_SAFETY = (
    "Privacy & safety\n\n"
    "Adaptive Endurance Coach is a training support tool. It does not provide "
    "medical advice and must not be used for emergencies.\n\n"
    "You choose what information to provide. This may include injury history or "
    "other health-related training limitations.\n\n"
    "Training-data connections and imports, including Strava, are optional.\n\n"
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
    "Adaptive Endurance Coach uses visible buttons to guide setup, show your saved "
    "profile and baseline, import workouts and manage optional Strava access.\n\n"
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
    "That baseline option is no longer available from this screen. Return to your "
    "current account menu to continue."
)
STRAVA_CONNECT_EXPLANATION = (
    "Connecting Strava is optional. Never enter your Strava username or password "
    "in Telegram. Use the Connect Strava button to open the secure Strava "
    "authorization page, where you can grant read-only profile and activity "
    "access. You can disconnect later."
)
STRAVA_DISABLED = "Strava connection is currently disabled."
STRAVA_NOT_CONNECTED = "Strava is not connected."
STRAVA_CONNECTION_UNHEALTHY = (
    "Strava authorization is stored but cannot currently be used. You can reconnect "
    "or disconnect to revoke access and erase the local tokens."
)
STRAVA_SYNC_STARTED = (
    "Strava synchronization started. Use the sync-status button to check progress."
)
STRAVA_SYNC_COMPLETE = (
    "Strava synchronization completed and your baseline was recalculated."
)
STRAVA_INITIAL_IMPORT_COMPLETE = (
    "Strava is connected and your initial activity import is complete. "
    "Use the baseline button to review your data baseline."
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
ADD_WORKOUT_REQUEST = (
    "Send a TCX workout file or an Apple Health export ZIP.\n\n"
    "TCX is recommended for a single new workout.\n"
    "Apple Health ZIP can update or enrich your history."
)
HEART_RATE_MISSING = (
    "Heart-rate data was not available.\n\n"
    "Would you like to enter your average heart rate manually?"
)
HEART_RATE_ENTRY = "Enter your average heart rate in bpm.\n\nExample: 148"
RPE_QUESTION = "How did the session feel?"
MOBILITY_QUESTION = "Did you do any mobility or stretching?"
DISCOMFORT_QUESTION = "Did you experience pain or unusual discomfort?"
DISCOMFORT_AREA_QUESTION = "Where did you feel it?"
DISCOMFORT_DESCRIPTION_REQUEST = (
    "Briefly describe where you felt it. Do not include a diagnosis. "
    "You will confirm the text before it is saved."
)
DISCOMFORT_SEVERITY_QUESTION = "How strong was the discomfort?"
WORKOUT_FEEDBACK_COMPLETE = "Workout details saved."
WORKOUT_FEEDBACK_CANCELLED = (
    "Workout questions cancelled. The imported activity remains saved."
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


CONSENT = (
    "Before we begin\n\n"
    "To personalise your athlete profile, the coach will store the information you "
    "provide during setup. This may include training history, injuries or physical "
    "limitations.\n\n"
    "Please confirm that:\n\n"
    "• You understand that this is not medical advice.\n"
    "• You agree to the storage of the information you choose to provide.\n"
    "• You understand that connected services and training-data imports are "
    "optional.\n"
    "• You can cancel onboarding or request deletion of your stored data.\n\n"
    "Do you want to continue?"
)
SETUP_INTRODUCTION = (
    "You\u2019re in — I\u2019m glad you\u2019ve decided to take the first step.\n\n"
    "Every athlete starts from a different place. Before I can help you move towards "
    "your goal, I need to understand where you are today, what you are training for "
    "and what your real training context looks like.\n\n"
    "We\u2019ll build your athlete profile together, one step at a time.\n\n"
    "It should only take a few minutes, and you\u2019ll be able to update your answers "
    "later.\n\n"
    "Ready to build your athlete profile?"
)
GOAL_INTAKE = (
    "Now let\u2019s talk about your training goal.\n\n"
    "What are you training for, and what would success look like to you?\n\n"
    "Tell me in your own words. You can include a race or challenge, when you "
    "want to do it, the result you are aiming for, and anything important you "
    "want to preserve while training.\n\n"
    "For example:\n"
    "\u201cI want to complete my first Ironman 70.3 next July, finish safely and "
    "maintain muscle.\u201d"
)
GOAL_ADDITION = "Tell me what you would like to add or change."
GOAL_SAVED = (
    "Your goal has been saved.\n\n"
    "This is the first part of your athlete profile. We\u2019ll continue building "
    "the rest of your profile step by step."
)
ONBOARDING_MODIFICATION_FALLBACK = (
    "Tell me which goal, outcome, birth year, category, age, weight, height, "
    "availability, equipment, or training limitation to change."
)

_ONBOARDING_FIELD_LABELS = {
    "main_goal": "goal",
    "target_outcome": "target outcome",
    "event_date": "event date",
    "age": "age",
    "birth_year": "birth year",
    "gender": "competitive category",
    "weight_kg": "weight",
    "height_cm": "height",
    "availability_text": "availability",
    "equipment_text": "equipment",
    "health_limitations_text": "training limitations",
}


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
PROFILE_GENDER_INTAKE = (
    "Which competitive category / biological sex should I use for your athlete profile?"
)
PROFILE_WEIGHT_INTAKE = (
    "What is your current weight in kilograms? Send a number from 40.0 to 200.0."
)
PROFILE_HEIGHT_INTAKE = (
    "What is your height in centimeters? Send a whole number from 120 to 230."
)
AVAILABILITY_INTAKE = (
    "Tell me about your weekly training availability in your own words.\n\n"
    "Include the days you can train, roughly how much time you have, and any places "
    "or facilities you can use if relevant. For example: "
    "'I can ride for up to two hours on the weekend.' "
    "I can swim at a pool on Wednesday and Friday, and I can cycle only on weekends."
)
EQUIPMENT_DETAILS_INTAKE = (
    "Anything else about your equipment or access we should know?\n\n"
    "Example: 'I have an MTB, can use a gym bike, and can only access the pool "
    "on weekends.'"
)
HEALTH_LIMITATIONS_INTAKE = (
    "Do you have any current or past injuries, discomfort, or physical limitations "
    "that should influence training? This is not medical advice."
)
CONTEXT_VALIDATION_ERROR = (
    "Please send a short answer for this part of your athlete profile."
)
EQUIPMENT_RECOMMENDATION_RETRY = (
    "Your availability has been saved, but I could not prepare the equipment "
    "suggestion just now. Send any message to retry."
)


def equipment_recommendation(recommendation: str | None) -> str:
    """Render the saved, goal-specific equipment suggestion safely."""

    suggestion = escape(recommendation or "the basic equipment for your goal")
    return (
        "Based on your goal, here is the essential equipment to consider, plus "
        "useful extras:\n\n"
        f"<pre>{suggestion}</pre>\n\n"
        "Which of these can you currently use? "
        "Select every item that is available to you."
    )


ONBOARDING_COMPLETED = (
    "Your onboarding is complete. You can change your profile settings at any time."
)
PROFILE_SETTINGS_MENU = "Choose a profile setting to change."
PROFILE_SETTINGS_UNPROMPTED = "Use Change profile to choose what you want to update."
PROFILE_GOAL_MAIN = "What is your training goal?"
PROFILE_GOAL_OUTCOME = "What would success or the outcome look like?"
PROFILE_GOAL_DATE = "When is the event? Send YYYY-MM-DD, or choose Not yet."
PROFILE_AVAILABILITY = "Describe your weekly training availability."
PROFILE_HEALTH = "Choose None, or describe limitations in a message."
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
        if hint == "exact_date":
            return "What is the race or target date?"
        return "Do you already have a race or target date?"
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
        "Is there anything else you want me to know about this goal?"
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
    "workout_feedback_disabled": "Workout feedback is currently unavailable.",
    "workout_flow_not_active": (
        "There is no active workout flow. Use the Add workout button to begin."
    ),
    "workout_flow_not_found": (
        "There is no active workout flow. Use the Add workout button to begin."
    ),
    "workout_flow_already_active": (
        "Finish or cancel the current workout questions before adding another."
    ),
    "activity_not_found": "That workout is no longer available.",
    "profile_incomplete": ("Complete onboarding before adding a daily workout."),
    "invalid_manual_heart_rate": (
        "Enter a whole-number average heart rate from 30 to 250 bpm."
    ),
    "manual_heart_rate_out_of_range": (
        "Enter a whole-number average heart rate from 30 to 250 bpm."
    ),
    "invalid_discomfort_description": (
        "Enter a short description of 500 characters or fewer."
    ),
    "feedback_text_too_long": ("Keep the description short (500 characters or fewer)."),
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
    "recalculating_baseline": "Recalculating baseline",
}
APPLE_HEALTH_PROGRESS = TRAINING_FILE_PROGRESS


def apple_health_file_result(
    *,
    activities_imported: int,
    activities_updated: int,
    activities_skipped: int,
    baseline_limited: bool = False,
) -> str:
    """Render one bulk-file outcome for an existing athlete."""

    lines = [
        "Apple Health history imported",
        "",
        f"Activities imported: {activities_imported}",
        f"Activities updated: {activities_updated}",
        f"Activities skipped: {activities_skipped}",
        "",
        "Your history was updated and your deterministic baseline was recalculated.",
    ]
    if baseline_limited:
        lines.extend(
            [
                "",
                "The baseline is partial; disciplines without enough data "
                "remain UNKNOWN.",
            ]
        )
    return "\n".join(lines)


def tcx_workout_result(
    *,
    sport: object,
    started_at: object,
    duration_seconds: int,
    distance_meters: float | None,
    average_heart_rate: float | None,
    baseline_limited: bool = False,
) -> str:
    """Render one canonical TCX workout without inventing missing metrics."""

    if baseline_limited:
        suffix = (
            "The workout is saved and your baseline was recalculated. "
            "The baseline is partial; disciplines without enough data "
            "remain UNKNOWN."
        )
    else:
        suffix = "The workout is saved and your baseline was recalculated."
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


def manual_heart_rate_confirmation(bpm: int) -> str:
    return f"Average heart rate: {bpm} bpm"


def discomfort_description_confirmation(description: str) -> str:
    return f"Discomfort description:\n\n{escape(description)}"


def validation_error(code: str) -> str:
    """Map a safe application error code to English copy."""

    return VALIDATION_ERRORS.get(code, GENERIC_ERROR)


def persisted_profile(profile: Mapping[str, Any]) -> str:
    """Render normalized persisted profile data."""

    if "birth_year" in profile and "gender" in profile:
        lines = [
            "Your saved athlete profile:",
            "",
            f"Birth year: {_display(profile.get('birth_year'))}",
            f"Category: {_display(profile.get('gender'))}",
            f"Weight: {_optional_metric(profile.get('weight_kg'), 'kg')}",
            f"Height: {_optional_metric(profile.get('height_cm'), 'cm')}",
        ]
        raw_context = (
            ("Availability", "availability_text"),
            ("Equipment recommendation", "equipment_recommendation_text"),
            ("Equipment", "equipment_text"),
            ("Training limitations", "health_limitations_text"),
        )
        for label, key in raw_context:
            value = profile.get(key)
            if isinstance(value, str) and value:
                lines.append(f"{label}: {_display_free_text(value)}")
        return "\n".join(lines)

    lines = [
        "Your saved athlete profile:",
        "",
        f"Sport: {_answer_display(profile, 'primary_sport')}",
        f"Goal: {_display_free_text(profile.get('main_goal'))}",
        f"Event date: {_display(profile.get('event_date'))}",
        f"Target outcome: {_display_free_text(profile.get('target_outcome'))}",
        f"Secondary priority: {_display_free_text(profile.get('secondary_priority'))}",
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


def oauth_success_page(
    initial_sync_started: bool,
    *,
    telegram_bot_username: str | None = None,
) -> str:
    """Return an English OAuth success HTML page."""

    sync_text = (
        "Your initial activity import has started."
        if initial_sync_started
        else "You can return to Telegram and use Sync now."
    )
    username = (telegram_bot_username or "").strip().lstrip("@")
    telegram_url = f"https://t.me/{username}" if username else "https://t.me"
    return _html_page(
        "Strava connected",
        f"Strava authorization was saved securely. {sync_text} You may close this tab.",
        action_label="Open Telegram",
        action_url=telegram_url,
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
