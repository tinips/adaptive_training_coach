"""Centralized Telegram keyboard labels and callback-data construction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import OnboardingStep, WorkoutFlowStep

LABELS = {
    "continue": "Continue",
    "cancel": "Cancel",
    "back": "Back",
    "skip": "Skip",
    "other": "Other",
    "correct": "Correct",
    "write_again": "Write it again",
    "back_options": "Back to options",
    "confirm_profile": "Confirm profile",
    "edit_goal": "Edit goal",
    "edit_availability": "Edit availability",
    "edit_equipment": "Edit equipment",
    "edit_limitations": "Edit limitations",
    "edit_coach": "Edit coach style",
    "edit_baseline": "Edit baseline",
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
    "training_history_import": "Import training history",
    "choose_other_method": "Choose another method",
    "cancel_import": "Cancel import",
    "retry_import": "Retry import",
    "continue_onboarding": "Continue onboarding",
    "finish_import": "Finish import",
    "add_latest_details": "Add details to the most recent workout",
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

SPORT_OPTIONS = [
    ("Running", "RUNNING"),
    ("Cycling", "CYCLING"),
    ("Triathlon", "TRIATHLON"),
    ("Swimming", "SWIMMING"),
    ("General fitness", "GENERAL_FITNESS"),
]

GOAL_OPTIONS: dict[str, list[tuple[str, str]]] = {
    "running": [
        ("5 km", "FIVE_K"),
        ("10 km", "TEN_K"),
        ("Half marathon", "HALF_MARATHON"),
        ("Marathon", "MARATHON"),
        ("Trail", "TRAIL"),
        ("Improve performance", "IMPROVE_PERFORMANCE"),
    ],
    "cycling": [
        ("Cycling event", "CYCLING_EVENT"),
        ("Gran fondo", "GRAN_FONDO"),
        ("Improve endurance", "IMPROVE_ENDURANCE"),
        ("Improve performance", "IMPROVE_PERFORMANCE"),
    ],
    "triathlon": [
        ("Sprint", "SPRINT_TRIATHLON"),
        ("Olympic", "OLYMPIC_TRIATHLON"),
        ("Half Ironman / 70.3", "HALF_IRONMAN_70_3"),
        ("Ironman", "IRONMAN"),
        ("Complete my first triathlon", "FIRST_TRIATHLON"),
    ],
    "swimming": [
        ("Improve technique", "IMPROVE_TECHNIQUE"),
        ("Open-water swimming", "OPEN_WATER_SWIMMING"),
        ("Specific event", "SPECIFIC_EVENT"),
        ("Improve endurance", "IMPROVE_ENDURANCE"),
    ],
    "general_fitness": [
        ("General health", "GENERAL_HEALTH"),
        ("Improve endurance", "IMPROVE_ENDURANCE"),
        ("Lose body fat", "LOSE_BODY_FAT"),
        ("Build strength", "BUILD_STRENGTH"),
    ],
}

SIMPLE_OPTIONS: dict[OnboardingStep, list[tuple[str, str]]] = {
    OnboardingStep.EVENT_STATUS: [("Yes", "YES"), ("Not yet", "NO")],
    OnboardingStep.GOAL_PRIORITY: [
        ("Finish safely", "FINISH_SAFELY"),
        ("Improve a personal best", "PERSONAL_BEST"),
        ("Reach a target time", "TARGET_TIME"),
        ("Health and consistency", "HEALTH_CONSISTENCY"),
    ],
    OnboardingStep.WEEKDAY_DURATION: [
        ("30 min", "30"),
        ("45 min", "45"),
        ("60 min", "60"),
        ("90 min", "90"),
        ("More than 90 min", "OVER_90"),
        ("Variable", "VARIABLE"),
    ],
    OnboardingStep.WEEKEND_DURATION: [
        ("60 min", "60"),
        ("90 min", "90"),
        ("2 hours", "120"),
        ("3 hours", "180"),
        ("More than 3 hours", "OVER_180"),
        ("Variable", "VARIABLE"),
    ],
    OnboardingStep.HEALTH_TIMING: [
        ("Current", "CURRENT"),
        ("Historical", "HISTORICAL"),
        ("Both", "BOTH"),
    ],
    OnboardingStep.COACH_TONE: [
        ("Direct and demanding", "DIRECT_DEMANDING"),
        ("Analytical and detailed", "ANALYTICAL_DETAILED"),
        ("Concise and practical", "CONCISE_PRACTICAL"),
        ("Supportive and motivational", "SUPPORTIVE_MOTIVATIONAL"),
    ],
    OnboardingStep.COACH_DETAIL: [
        ("Short", "SHORT"),
        ("Medium", "MEDIUM"),
        ("Detailed", "DETAILED"),
    ],
}

MULTI_OPTIONS: dict[OnboardingStep, list[tuple[str, str]]] = {
    OnboardingStep.TRAINING_DAYS: [
        ("Monday", "MONDAY"),
        ("Tuesday", "TUESDAY"),
        ("Wednesday", "WEDNESDAY"),
        ("Thursday", "THURSDAY"),
        ("Friday", "FRIDAY"),
        ("Saturday", "SATURDAY"),
        ("Sunday", "SUNDAY"),
    ],
    OnboardingStep.EQUIPMENT: [
        ("Running shoes", "RUNNING_SHOES"),
        ("Road bike", "ROAD_BIKE"),
        ("Mountain bike", "MOUNTAIN_BIKE"),
        ("Indoor bike or trainer", "INDOOR_BIKE_TRAINER"),
        ("Swimming pool", "SWIMMING_POOL"),
        ("Gym", "GYM"),
        ("Resistance bands", "RESISTANCE_BANDS"),
        ("Sports watch", "SPORTS_WATCH"),
        ("Heart-rate chest strap", "HEART_RATE_CHEST_STRAP"),
    ],
    OnboardingStep.POOL_ACCESS: [
        ("Monday", "MONDAY"),
        ("Tuesday", "TUESDAY"),
        ("Wednesday", "WEDNESDAY"),
        ("Thursday", "THURSDAY"),
        ("Friday", "FRIDAY"),
        ("Saturday", "SATURDAY"),
        ("Sunday", "SUNDAY"),
        ("Irregular access", "IRREGULAR"),
        ("No regular access", "NO_REGULAR_ACCESS"),
    ],
    OnboardingStep.BIKE_ACCESS: [
        ("Monday", "MONDAY"),
        ("Tuesday", "TUESDAY"),
        ("Wednesday", "WEDNESDAY"),
        ("Thursday", "THURSDAY"),
        ("Friday", "FRIDAY"),
        ("Saturday", "SATURDAY"),
        ("Sunday", "SUNDAY"),
        ("Irregular access", "IRREGULAR"),
        ("No regular access", "NO_REGULAR_ACCESS"),
    ],
    OnboardingStep.HEALTH_AREAS: [
        ("None", "NONE"),
        ("Shoulder", "SHOULDER"),
        ("Back", "BACK"),
        ("Hip", "HIP"),
        ("Knee", "KNEE"),
        ("Ankle or foot", "ANKLE_FOOT"),
    ],
}


def keyboard_for_step(
    step: OnboardingStep,
    answers: Mapping[str, Any],
    *,
    strava_enabled: bool = False,
    apple_health_enabled: bool = True,
    tcx_enabled: bool = True,
) -> InlineKeyboardMarkup | None:
    """Build the appropriate inline keyboard for a persisted step."""

    if step is OnboardingStep.CONSENT:
        return _rows(
            [
                [
                    (
                        LABELS["continue"],
                        f"ob:v1:set:{step.value}:CONTINUE",
                    )
                ],
                [(LABELS["cancel"], "ob:v1:cancel")],
            ]
        )
    if step is OnboardingStep.PRIMARY_SPORT:
        return _single_options(step, SPORT_OPTIONS, other=True)
    if step is OnboardingStep.GOAL_TYPE:
        sport = str(answers.get("primary_sport", "GENERAL_FITNESS")).lower()
        options = GOAL_OPTIONS.get(sport, GOAL_OPTIONS["general_fitness"])
        return _single_options(step, options, other=True)
    if step is OnboardingStep.BASELINE_SOURCE:
        baseline_options: list[tuple[str, str]] = []
        if apple_health_enabled or tcx_enabled:
            baseline_options.append((LABELS["training_history_import"], "FILE_IMPORT"))
        baseline_options.extend(
            [
                ("Enter baseline manually", "MANUAL"),
                ("Decide later", "SKIP_FOR_NOW"),
            ]
        )
        if strava_enabled:
            baseline_options.insert(0, ("Connect Strava", "STRAVA"))
        return _single_options(step, baseline_options, other=False)
    if step is OnboardingStep.APPLE_HEALTH_PRIVACY_NOTICE:
        return apple_health_privacy_keyboard()
    if step is OnboardingStep.APPLE_HEALTH_WAITING_FOR_FILE:
        return apple_health_file_keyboard()
    if step is OnboardingStep.APPLE_HEALTH_IMPORT_COMPLETE:
        return _rows(
            [
                [
                    (
                        LABELS["continue_onboarding"],
                        "ob:v1:apple:continue",
                    )
                ]
            ]
        )
    if step is OnboardingStep.APPLE_HEALTH_IMPORT_FAILED:
        return apple_health_failure_keyboard()
    if step is OnboardingStep.FILE_IMPORT_WAITING:
        return training_file_import_keyboard()
    if step is OnboardingStep.FILE_IMPORT_COMPLETE:
        return training_file_complete_keyboard()
    if step in SIMPLE_OPTIONS:
        allow_other = step is OnboardingStep.GOAL_PRIORITY
        return _single_options(step, SIMPLE_OPTIONS[step], other=allow_other)
    if step in MULTI_OPTIONS:
        key = _answer_key(step)
        selected = _selection_values(answers.get(f"_selection_{key}", answers.get(key)))
        allow_other = step in {
            OnboardingStep.EQUIPMENT,
            OnboardingStep.HEALTH_AREAS,
        }
        return _multi_options(
            step,
            MULTI_OPTIONS[step],
            selected,
            other=allow_other,
        )
    if step in {OnboardingStep.HEIGHT, OnboardingStep.WEIGHT}:
        return _rows(
            [
                [(LABELS["skip"], f"ob:v1:skip:{step.value}")],
                [(LABELS["back"], f"ob:v1:back:{step.value}")],
            ]
        )
    if step is OnboardingStep.HEALTH_DESCRIPTION:
        return _rows(
            [
                [("Write answer", f"ob:v1:other:{step.value}")],
                [(LABELS["skip"], f"ob:v1:skip:{step.value}")],
                [(LABELS["back"], f"ob:v1:back:{step.value}")],
            ]
        )
    if step is OnboardingStep.SUMMARY:
        return summary_keyboard()
    if step in {
        OnboardingStep.EVENT_NAME,
        OnboardingStep.EVENT_DATE,
        OnboardingStep.AGE,
    }:
        return _rows([[(LABELS["back"], f"ob:v1:back:{step.value}")]])
    return None


def parsed_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Build the mandatory interpretation confirmation choices."""

    return _rows(
        [
            [(LABELS["correct"], "ob:v1:parsed:confirm")],
            [(LABELS["write_again"], "ob:v1:parsed:retry")],
            [(LABELS["back_options"], "ob:v1:parsed:back")],
        ]
    )


def free_text_recovery_keyboard() -> InlineKeyboardMarkup:
    """Offer another explicit parse or return to deterministic options."""

    return _rows(
        [
            [(LABELS["write_again"], "ob:v1:parsed:retry")],
            [(LABELS["back_options"], "ob:v1:parsed:back")],
        ]
    )


def apple_health_privacy_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["continue"], "ob:v1:apple:continue")],
            [
                (
                    LABELS["choose_other_method"],
                    "ob:v1:apple:choose_other",
                )
            ],
            [(LABELS["back"], "ob:v1:apple:back")],
        ]
    )


def apple_health_file_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["cancel_import"], "ob:v1:apple:cancel")],
            [
                (
                    LABELS["choose_other_method"],
                    "ob:v1:apple:choose_other",
                )
            ],
            [(LABELS["back"], "ob:v1:apple:back")],
        ]
    )


def apple_health_failure_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["retry_import"], "ob:v1:apple:retry")],
            [
                (
                    LABELS["choose_other_method"],
                    "ob:v1:apple:choose_other",
                )
            ],
            [(LABELS["back"], "ob:v1:apple:back")],
        ]
    )


def training_file_import_keyboard() -> InlineKeyboardMarkup:
    """Keep onboarding in one persisted multi-file import session."""

    return _rows(
        [
            [(LABELS["finish_import"], "ob:v1:import:finish")],
            [
                (
                    LABELS["choose_other_method"],
                    "ob:v1:apple:choose_other",
                )
            ],
            [(LABELS["back"], "ob:v1:apple:back")],
        ]
    )


def training_file_complete_keyboard(
    *,
    can_enrich_latest: bool = True,
) -> InlineKeyboardMarkup:
    rows: list[list[tuple[str, str]]] = []
    if can_enrich_latest:
        rows.append(
            [
                (
                    LABELS["add_latest_details"],
                    "ob:v1:import:enrich_latest",
                )
            ]
        )
    rows.append(
        [
            (
                LABELS["continue_onboarding"],
                "ob:v1:apple:continue",
            )
        ]
    )
    return _rows(rows)


def summary_keyboard() -> InlineKeyboardMarkup:
    """Build final profile confirmation and section-edit actions."""

    return _rows(
        [
            [(LABELS["confirm_profile"], "ob:v1:summary:confirm")],
            [
                (LABELS["edit_goal"], "ob:v1:edit:goal"),
                (LABELS["edit_availability"], "ob:v1:edit:availability"),
            ],
            [
                (LABELS["edit_equipment"], "ob:v1:edit:equipment"),
                (LABELS["edit_limitations"], "ob:v1:edit:limitations"),
            ],
            [
                (LABELS["edit_coach"], "ob:v1:edit:coach"),
                (LABELS["edit_baseline"], "ob:v1:edit:baseline"),
            ],
            [(LABELS["cancel"], "ob:v1:cancel")],
        ]
    )


def resume_keyboard(*, cancelled: bool = False) -> InlineKeyboardMarkup:
    """Build resume or restart action."""

    label = LABELS["restart"] if cancelled else LABELS["resume"]
    action = "ob:v1:restart" if cancelled else "ob:v1:resume"
    return _rows([[(label, action)]])


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
            [
                (
                    LABELS["back"],
                    _feedback_back(WorkoutFlowStep.WAITING_FOR_FILE),
                )
            ],
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
    """Build post-onboarding actions valid for lifecycle and connection state."""

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
            rows.insert(
                0,
                [(LABELS["reconnect_strava"], "menu:v1:strava")],
            )
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


def _single_options(
    step: OnboardingStep,
    options: Sequence[tuple[str, str]],
    *,
    other: bool,
) -> InlineKeyboardMarkup:
    rows = [[(label, f"ob:v1:set:{step.value}:{value}")] for label, value in options]
    if other:
        rows.append([(LABELS["other"], f"ob:v1:other:{step.value}")])
    rows.extend(
        [
            [(LABELS["back"], f"ob:v1:back:{step.value}")],
            [(LABELS["cancel"], "ob:v1:cancel")],
        ]
    )
    return _rows(rows)


def _multi_options(
    step: OnboardingStep,
    options: Sequence[tuple[str, str]],
    selected: Sequence[str],
    *,
    other: bool,
) -> InlineKeyboardMarkup:
    selected_set = set(selected)
    rows = [
        [
            (
                f"✓ {label}" if value in selected_set else label,
                (
                    f"ob:v1:multi:remove:{step.value}:{value}"
                    if value in selected_set
                    else f"ob:v1:multi:add:{step.value}:{value}"
                ),
            )
        ]
        for label, value in options
    ]
    if other:
        rows.append([(LABELS["other"], f"ob:v1:other:{step.value}")])
    rows.extend(
        [
            [(LABELS["continue"], f"ob:v1:continue:{step.value}")],
            [(LABELS["back"], f"ob:v1:back:{step.value}")],
            [(LABELS["cancel"], "ob:v1:cancel")],
        ]
    )
    return _rows(rows)


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


def _answer_key(step: OnboardingStep) -> str:
    return {
        OnboardingStep.TRAINING_DAYS: "training_days",
        OnboardingStep.EQUIPMENT: "equipment",
        OnboardingStep.POOL_ACCESS: "pool_access",
        OnboardingStep.BIKE_ACCESS: "bike_access",
        OnboardingStep.HEALTH_AREAS: "health_areas",
    }[step]


def _selection_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, Mapping):
        access_type = value.get("type")
        if access_type in {"IRREGULAR", "NO_REGULAR_ACCESS"}:
            return [str(access_type)]
        days = value.get("days")
        if isinstance(days, list):
            return [str(item) for item in days]
    return []
