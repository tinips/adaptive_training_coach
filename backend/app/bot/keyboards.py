"""Centralized Telegram keyboard labels and callback-data construction."""

from __future__ import annotations

from collections.abc import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.rendering import TelegramButtonSpec
from app.domain.enums import WorkoutFlowStep


def dynamic_keyboard(
    rows: Sequence[Sequence[TelegramButtonSpec]],
) -> InlineKeyboardMarkup | None:
    """Render graph-provided button metadata without interpreting its meaning."""

    if not rows:
        return None
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    spec.text,
                    callback_data=spec.callback_data,
                    url=spec.url,
                )
                for spec in row
            ]
            for row in rows
        ]
    )


LABELS = {
    "lets_go": "Let's go",
    "coach_help": "How can this coach help me?",
    "privacy_safety": "Privacy & safety",
    "understand_continue": "I understand — continue",
    "build_profile": "Let's build my profile",
    "goal_correct": "No, that\u2019s right",
    "goal_add": "Yes, add something",
    "goal_restart": "Start again",
    "goal_has_date": "Yes, I have a date",
    "goal_no_date": "Not yet",
    "goal_prepare_race": "Prepare for a race",
    "goal_distance": "Reach a specific distance",
    "goal_pace": "Improve my pace",
    "goal_consistency": "Run consistently",
    "goal_something_else": "Something else",
    "gender_male": "Male",
    "gender_female": "Female",
    "gender_other_unspecified": "Other / Unspecified",
    "cancel": "Cancel",
    "back": "Back",
    "skip": "Skip",
    "other": "Other",
    "resume": "Resume onboarding",
    "restart": "Restart onboarding",
    "confirm_cancel": "Yes, cancel",
    "keep_onboarding": "Keep onboarding",
    "confirm_delete": "Yes, delete my data",
    "keep_account": "Keep my account",
    "connect_strava": "Connect Strava",
    "reconnect_strava": "Reconnect Strava",
    "sync_now": "Sync now",
    "recalculate": "Recalculate baseline",
    "disconnect_strava": "Disconnect Strava",
    "confirm_disconnect": "Yes, disconnect",
    "keep_strava": "Keep Strava connected",
    "view_profile": "View profile",
    "view_baseline": "View baseline",
    "view_sync": "View sync status",
    "strava_settings": "Strava settings",
    "manual_baseline": "Manual baseline",
    "add_workout": "Add workout",
    "enter_average_hr": "Enter average HR",
    "continue_without_hr": "Continue without HR",
    "change": "Change",
    "confirm": "Confirm",
    "very_easy": "Very easy",
    "easy": "Easy",
    "moderate": "Moderate",
    "hard": "Hard",
    "very_hard": "Very hard",
    "yes": "Yes",
    "no": "No",
    "shoulder": "Shoulder",
    "back_area": "Back",
    "hip": "Hip",
    "knee": "Knee",
    "ankle_foot": "Ankle or foot",
    "skip_details": "Skip details",
    "mild": "Mild",
    "severe": "Severe",
    "help": "Help",
}


def welcome_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["lets_go"], "nav:v1:consent")],
            [(LABELS["coach_help"], "nav:v1:help")],
            [(LABELS["privacy_safety"], "nav:v1:privacy")],
        ]
    )


def information_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["lets_go"], "nav:v1:consent")],
            [(LABELS["back"], "nav:v1:welcome")],
        ]
    )


def consent_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["understand_continue"], "ob:v1:consent")],
            [(LABELS["back"], "nav:v1:welcome")],
            [(LABELS["cancel"], "ob:v1:cancel")],
        ]
    )


def setup_introduction_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["build_profile"], "ob:v1:profile")],
            [(LABELS["cancel"], "ob:v1:cancel")],
        ]
    )


def goal_input_keyboard() -> InlineKeyboardMarkup:
    return _rows([[(LABELS["cancel"], "ob:v1:cancel")]])


def goal_confirmation_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["goal_correct"], "ob:v1:goal:confirm")],
            [(LABELS["goal_add"], "ob:v1:goal:add")],
            [(LABELS["goal_restart"], "ob:v1:goal:restart")],
            [(LABELS["cancel"], "ob:v1:cancel")],
        ]
    )


def goal_saved_keyboard() -> InlineKeyboardMarkup:
    return _rows([[("Back to welcome", "nav:v1:welcome")]])


def profile_text_input_keyboard() -> InlineKeyboardMarkup:
    """Keep cancellation available during mandatory deterministic text intake."""

    return _rows([[(LABELS["cancel"], "ob:v1:cancel")]])


def profile_gender_keyboard() -> InlineKeyboardMarkup:
    """Build the deterministic competitive-category choices."""

    return _rows(
        [
            [(LABELS["gender_male"], "ob:v1:profile:gender:MALE")],
            [(LABELS["gender_female"], "ob:v1:profile:gender:FEMALE")],
            [
                (
                    LABELS["gender_other_unspecified"],
                    "ob:v1:profile:gender:OTHER_UNSPECIFIED",
                )
            ],
            [(LABELS["cancel"], "ob:v1:cancel")],
        ]
    )


def goal_date_clarification_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["goal_has_date"], "ob:v1:goal:choice:HAS_DATE")],
            [(LABELS["goal_no_date"], "ob:v1:goal:choice:NOT_YET")],
            [(LABELS["cancel"], "ob:v1:cancel")],
        ]
    )


def goal_main_clarification_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["goal_prepare_race"], "ob:v1:goal:choice:PREPARE_RACE")],
            [
                (
                    LABELS["goal_distance"],
                    "ob:v1:goal:choice:SPECIFIC_DISTANCE",
                )
            ],
            [(LABELS["goal_pace"], "ob:v1:goal:choice:IMPROVE_PACE")],
            [
                (
                    LABELS["goal_consistency"],
                    "ob:v1:goal:choice:RUN_CONSISTENTLY",
                )
            ],
            [
                (
                    LABELS["goal_something_else"],
                    "ob:v1:goal:choice:SOMETHING_ELSE",
                )
            ],
            [(LABELS["cancel"], "ob:v1:cancel")],
        ]
    )


def resume_keyboard(*, cancelled: bool = False) -> InlineKeyboardMarkup:
    """Build resume or restart action."""

    label = LABELS["restart"] if cancelled else LABELS["resume"]
    action = "ob:v1:restart" if cancelled else "ob:v1:resume"
    return _rows([[(label, action)]])


def cancelled_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["restart"], "ob:v1:restart")],
            [(LABELS["back"], "nav:v1:welcome")],
        ]
    )


def cancel_confirmation_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["confirm_cancel"], "ob:v1:cancel:confirm")],
            [(LABELS["keep_onboarding"], "ob:v1:cancel:keep")],
        ]
    )


def deletion_confirmation_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["confirm_delete"], "acct:v1:delete:confirm")],
            [(LABELS["keep_account"], "acct:v1:delete:keep")],
        ]
    )


def strava_keyboard(
    *,
    connected: bool,
    can_disconnect: bool = False,
    syncing: bool = False,
    connect_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Build only Strava actions valid for the current state."""

    rows: list[list[tuple[str, str]]] = []
    if not connected and connect_url:
        label = (
            LABELS["reconnect_strava"] if can_disconnect else LABELS["connect_strava"]
        )
        rows.append([(label, connect_url)])
    if connected and not syncing:
        rows.extend(
            [
                [(LABELS["sync_now"], "st:v1:sync")],
                [(LABELS["recalculate"], "st:v1:recalculate")],
            ]
        )
    if (connected or can_disconnect) and not syncing:
        rows.append([(LABELS["disconnect_strava"], "st:v1:disconnect")])
    if syncing:
        rows.append([(LABELS["view_sync"], "st:v1:status")])
    rows.append([(LABELS["back"], "menu:v1:home")])
    return _rows(rows, detect_urls=True)


def disconnect_confirmation_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["confirm_disconnect"], "st:v1:disconnect:confirm")],
            [(LABELS["keep_strava"], "st:v1:disconnect:keep")],
        ]
    )


def add_workout_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["cancel"], "wf:v1:cancel")],
            [(LABELS["back"], _feedback_back(WorkoutFlowStep.WAITING_FOR_FILE))],
        ]
    )


def feedback_text_entry_keyboard(
    *,
    state: WorkoutFlowStep = WorkoutFlowStep.HR_ENTRY,
) -> InlineKeyboardMarkup:
    if state not in {
        WorkoutFlowStep.HR_ENTRY,
        WorkoutFlowStep.DESCRIPTION_ENTRY,
    }:
        raise ValueError("Unsupported feedback text-entry state.")
    return _rows(
        [
            [(LABELS["back"], _feedback_back(state))],
            [(LABELS["cancel"], "wf:v1:cancel")],
        ]
    )


def manual_heart_rate_offer_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["enter_average_hr"], "wf:v1:hr:enter")],
            [(LABELS["continue_without_hr"], "wf:v1:hr:skip")],
            [(LABELS["cancel"], "wf:v1:cancel")],
        ]
    )


def manual_heart_rate_confirmation_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["confirm"], "wf:v1:hr:confirm")],
            [(LABELS["change"], "wf:v1:hr:change")],
            [(LABELS["skip"], "wf:v1:hr:skip")],
        ]
    )


def rpe_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["very_easy"], "wf:v1:rpe:very_easy")],
            [(LABELS["easy"], "wf:v1:rpe:easy")],
            [(LABELS["moderate"], "wf:v1:rpe:moderate")],
            [(LABELS["hard"], "wf:v1:rpe:hard")],
            [(LABELS["very_hard"], "wf:v1:rpe:very_hard")],
            [(LABELS["skip"], "wf:v1:rpe:skip")],
            [(LABELS["back"], _feedback_back(WorkoutFlowStep.RPE))],
        ]
    )


def mobility_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["yes"], "wf:v1:mobility:yes")],
            [(LABELS["no"], "wf:v1:mobility:no")],
            [(LABELS["skip"], "wf:v1:mobility:skip")],
            [(LABELS["back"], _feedback_back(WorkoutFlowStep.MOBILITY))],
        ]
    )


def discomfort_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["no"], "wf:v1:discomfort:no")],
            [(LABELS["yes"], "wf:v1:discomfort:yes")],
            [(LABELS["skip"], "wf:v1:discomfort:skip")],
            [(LABELS["back"], _feedback_back(WorkoutFlowStep.DISCOMFORT))],
        ]
    )


def discomfort_area_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["shoulder"], "wf:v1:area:shoulder")],
            [(LABELS["back_area"], "wf:v1:area:back")],
            [(LABELS["hip"], "wf:v1:area:hip")],
            [(LABELS["knee"], "wf:v1:area:knee")],
            [(LABELS["ankle_foot"], "wf:v1:area:ankle_foot")],
            [(LABELS["other"], "wf:v1:area:other")],
            [(LABELS["skip_details"], "wf:v1:area:skip")],
            [(LABELS["back"], _feedback_back(WorkoutFlowStep.BODY_AREA))],
        ]
    )


def discomfort_description_confirmation_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["confirm"], "wf:v1:description:confirm")],
            [(LABELS["change"], "wf:v1:description:change")],
            [(LABELS["skip"], "wf:v1:description:skip")],
        ]
    )


def discomfort_severity_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["mild"], "wf:v1:severity:mild")],
            [(LABELS["moderate"], "wf:v1:severity:moderate")],
            [(LABELS["severe"], "wf:v1:severity:severe")],
            [(LABELS["skip"], "wf:v1:severity:skip")],
            [(LABELS["back"], _feedback_back(WorkoutFlowStep.SEVERITY))],
        ]
    )


def state_menu(
    state: str,
    *,
    connected: bool = False,
    syncing: bool = False,
    strava_enabled: bool = False,
) -> InlineKeyboardMarkup:
    """Build post-profile actions for existing completed athletes."""

    if state == "importing" and strava_enabled:
        strava_action = (
            (LABELS["view_sync"], "st:v1:status")
            if connected and syncing
            else (
                (LABELS["strava_settings"], "menu:v1:strava")
                if connected
                else (LABELS["reconnect_strava"], "menu:v1:strava")
            )
        )
        return _rows(
            [
                [(LABELS["add_workout"], "menu:v1:add_workout")],
                [strava_action],
                [(LABELS["view_baseline"], "menu:v1:baseline")],
                [(LABELS["view_profile"], "menu:v1:profile")],
                [(LABELS["help"], "menu:v1:help")],
            ]
        )
    if state == "ready":
        rows = [
            [(LABELS["add_workout"], "menu:v1:add_workout")],
            [(LABELS["view_baseline"], "menu:v1:baseline")],
        ]
        if connected and strava_enabled:
            if syncing:
                rows.append([(LABELS["view_sync"], "st:v1:status")])
            else:
                rows.extend(
                    [
                        [(LABELS["sync_now"], "st:v1:sync")],
                        [(LABELS["recalculate"], "st:v1:recalculate")],
                    ]
                )
            rows.append([(LABELS["strava_settings"], "menu:v1:strava")])
        elif strava_enabled:
            rows.insert(0, [(LABELS["reconnect_strava"], "menu:v1:strava")])
        rows.extend(
            [
                [(LABELS["view_profile"], "menu:v1:profile")],
                [(LABELS["help"], "menu:v1:help")],
            ]
        )
        return _rows(rows)
    rows = []
    if connected and strava_enabled:
        if syncing:
            rows.append([(LABELS["view_sync"], "st:v1:status")])
        else:
            rows.append([(LABELS["strava_settings"], "menu:v1:strava")])
    elif strava_enabled:
        rows.append([(LABELS["connect_strava"], "menu:v1:strava")])
    rows.extend(
        [
            [(LABELS["add_workout"], "menu:v1:add_workout")],
            [(LABELS["view_baseline"], "menu:v1:baseline")],
            [(LABELS["view_profile"], "menu:v1:profile")],
            [(LABELS["manual_baseline"], "menu:v1:manual")],
            [(LABELS["help"], "menu:v1:help")],
        ]
    )
    return _rows(rows)


def _feedback_back(state: WorkoutFlowStep) -> str:
    return f"wf:v1:back:{state.value.lower()}"


def _rows(
    rows: Sequence[Sequence[tuple[str, str]]],
    *,
    detect_urls: bool = False,
) -> InlineKeyboardMarkup:
    built: list[list[InlineKeyboardButton]] = []
    for row in rows:
        buttons: list[InlineKeyboardButton] = []
        for label, action in row:
            if detect_urls and action.startswith(("https://", "http://")):
                buttons.append(InlineKeyboardButton(label, url=action))
            else:
                buttons.append(InlineKeyboardButton(label, callback_data=action))
        built.append(buttons)
    return InlineKeyboardMarkup(built)
