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
    "That button is no longer valid. Use the visible buttons to continue from "
    "your current screen."
)
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
FILE_IMPORT_REQUEST = (
    "Send an Apple Health export ZIP or one or more TCX workout files.\n\n"
    "Apple Health ZIP is recommended for importing previous history.\n"
    "TCX is useful for individual workouts.\n\n"
    "You can upload multiple files and finish when you are done."
)
FILE_IMPORT_NO_ACTIVITIES = (
    "No valid activities have been imported in this session yet, so the "
    "baseline is still incomplete. Upload a supported file, choose another "
    "method, decide later, or go back."
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
WORKOUT_IMPORT_CANCELLED = "Workout import cancelled."
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
        "How would you like to establish your initial training baseline?"
    ),
    OnboardingStep.FILE_IMPORT_WAITING: FILE_IMPORT_REQUEST,
    OnboardingStep.FILE_IMPORT_PROCESSING: (
        "Your training file is being processed. Your saved progress is safe."
    ),
    OnboardingStep.FILE_IMPORT_COMPLETE: (
        "Your training-history import is ready to finish."
    ),
    OnboardingStep.APPLE_HEALTH_PRIVACY_NOTICE: (
        "Apple Health exports can contain sensitive health information.\n\n"
        "This import reads only workout, heart-rate, distance, energy, and "
        "source data required for training analysis.\n\n"
        "Clinical records and unrelated health categories are ignored. The "
        "uploaded file is deleted after processing."
    ),
    OnboardingStep.APPLE_HEALTH_WAITING_FOR_FILE: (
        "Export your data from the Apple Health app and send the ZIP file here "
        "as a Telegram document.\n\n"
        "On iPhone:\n"
        "Health → profile picture → Export All Health Data"
    ),
    OnboardingStep.APPLE_HEALTH_PROCESSING: (
        "Your Apple Health export is being processed. You can return later; "
        "your progress is saved."
    ),
    OnboardingStep.APPLE_HEALTH_IMPORT_COMPLETE: (
        "Your Apple Health import is complete."
    ),
    OnboardingStep.APPLE_HEALTH_IMPORT_FAILED: (
        "The Apple Health export could not be imported safely. Your uploaded "
        "file was deleted. You can retry with a new export or choose another "
        "baseline method."
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
    "apple_health_file_not_expected": (
        "Choose Import Apple Health data and review the privacy notice before "
        "sending a ZIP file."
    ),
    "apple_health_import_disabled": ("Apple Health import is currently unavailable."),
    "import_already_active": ("An Apple Health import is already in progress."),
    "training_file_not_expected": (
        "Choose Import training history during onboarding, or Add workout from "
        "your account menu, before sending this file."
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
    "no_valid_imported_activities": FILE_IMPORT_NO_ACTIVITIES,
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


def apple_health_import_success(
    *,
    workouts_found: int,
    activities_imported: int,
    activities_updated: int,
    activities_skipped: int,
    heart_rate_records_matched: int,
    warning_count: int,
    discipline_counts: Mapping[str, int],
) -> str:
    """Render honest counters from the persisted import outcome."""

    discipline_order = (
        ("RUNNING", "Runs"),
        ("CYCLING", "Rides"),
        ("SWIMMING", "Swims"),
        ("HIKING", "Hikes"),
        ("STRENGTH", "Strength workouts"),
        ("OTHER", "Other workouts"),
    )
    lines = [
        "Apple Health import complete.",
        "",
        f"Workouts found: {workouts_found}",
        f"Activities imported: {activities_imported}",
        f"Activities updated: {activities_updated}",
        f"Activities skipped: {activities_skipped}",
        f"Heart-rate records matched: {heart_rate_records_matched}",
        f"Warnings: {warning_count}",
        "",
        "Saved activities by discipline:",
    ]
    lines.extend(
        f"{label}: {discipline_counts.get(value, 0)}"
        for value, label in discipline_order
    )
    return "\n".join(lines)


def apple_health_file_result(
    *,
    activities_imported: int,
    activities_updated: int,
    activities_skipped: int,
    onboarding: bool = True,
    baseline_limited: bool = False,
) -> str:
    """Render one bulk-file outcome for onboarding or a daily backfill."""

    lines = [
        "Apple Health history imported",
        "",
        f"Activities imported: {activities_imported}",
        f"Activities updated: {activities_updated}",
        f"Activities skipped: {activities_skipped}",
        "",
        (
            "You can upload TCX files or finish the import."
            if onboarding
            else "Your history was updated and your deterministic baseline "
            "was recalculated."
        ),
    ]
    if not onboarding and baseline_limited:
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
    onboarding: bool,
    baseline_limited: bool = False,
) -> str:
    """Render one canonical TCX workout without inventing missing metrics."""

    if onboarding:
        suffix = "You can upload another file or finish the import."
    elif baseline_limited:
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


def training_import_complete(
    *,
    activities_imported: int,
    activities_updated: int,
    activities_skipped: int,
    discipline_counts: Mapping[str, int],
    baseline_limited: bool = False,
) -> str:
    """Render cumulative onboarding import counts and discipline coverage."""

    labels = (
        ("RUNNING", "Runs"),
        ("CYCLING", "Rides"),
        ("SWIMMING", "Swims"),
        ("HIKING", "Hikes"),
        ("STRENGTH", "Strength workouts"),
        ("OTHER", "Other workouts"),
    )
    lines = [
        "Training history imported",
        "",
        f"Activities imported: {activities_imported}",
        f"Activities updated: {activities_updated}",
        f"Activities skipped: {activities_skipped}",
        "",
        "Imported disciplines:",
    ]
    lines.extend(
        f"{label}: {discipline_counts.get(value, 0)}" for value, label in labels
    )
    lines.extend(
        [
            "",
            "Your deterministic baseline was recalculated from the available data.",
        ]
    )
    if baseline_limited:
        lines.append(
            "The baseline is partial; disciplines without enough data remain UNKNOWN."
        )
    return "\n".join(lines)


def manual_heart_rate_confirmation(bpm: int) -> str:
    return f"Average heart rate: {bpm} bpm"


def discomfort_description_confirmation(description: str) -> str:
    return f"Discomfort description:\n\n{escape(description)}"


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
