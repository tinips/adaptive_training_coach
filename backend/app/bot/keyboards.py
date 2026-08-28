"""Centralized Telegram keyboard labels and callback-data construction."""

from __future__ import annotations

from collections.abc import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from app.bot.rendering import TelegramButtonSpec


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
    "continue": "Continue",
    "goal_sport_running": "Running",
    "goal_sport_cycling": "Cycling",
    "goal_sport_swimming": "Swimming",
    "goal_sport_triathlon": "Triathlon",
    "goal_back": "Back",
    "support_none": "No supporting goal",
    "health_none": "None",
    "gender_male": "Male",
    "gender_female": "Female",
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
    "view_profile": "View profile",
    "add_workout": "Add workout",
    "help": "Help",
    "start": "Start",
    "resume_menu": "Resume",
    "profile": "Profile",
    "change_profile": "Change profile",
    "plan_next_week": "Plan next week",
    "view_weekly_plan": "View weekly plan",
    "delete": "Delete",
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


def goal_sport_keyboard(sports: Sequence[str]) -> InlineKeyboardMarkup:
    """First level: which sport is the goal in.

    Takes plain sport values rather than a service-layer enum, so this module
    keeps importing nothing but telegram and app.bot.rendering.
    """

    label_by_sport = {
        "RUNNING": LABELS["goal_sport_running"],
        "CYCLING": LABELS["goal_sport_cycling"],
        "SWIMMING": LABELS["goal_sport_swimming"],
        "TRIATHLON": LABELS["goal_sport_triathlon"],
    }
    rows = [[(label_by_sport[sport], f"ob:v1:goal:sport:{sport}")] for sport in sports]
    rows.append([(LABELS["cancel"], "ob:v1:cancel")])
    return _rows(rows)


def goal_template_keyboard(
    options: Sequence[tuple[str, str]],
) -> InlineKeyboardMarkup:
    """Second level: which goal within that sport. Takes (code, name) pairs."""

    rows = [
        [(display_name, f"ob:v1:goal:template:{code}")]
        for code, display_name in options
    ]
    rows.append([(LABELS["goal_back"], "ob:v1:goal:back")])
    rows.append([(LABELS["cancel"], "ob:v1:cancel")])
    return _rows(rows)


def supporting_goal_keyboard(
    options: Sequence[tuple[str, str]],
) -> InlineKeyboardMarkup:
    """Optional supporting goal, offered to every athlete."""

    rows = [[(name, f"ob:v1:support:{code}")] for code, name in options]
    rows.append([(LABELS["support_none"], "ob:v1:support:none")])
    rows.append([(LABELS["cancel"], "ob:v1:cancel")])
    return _rows(rows)


def goal_saved_keyboard() -> InlineKeyboardMarkup:
    return _rows([[("Back to welcome", "nav:v1:welcome")]])


def profile_text_input_keyboard() -> InlineKeyboardMarkup:
    """Keep cancellation available during mandatory deterministic text intake."""

    return _rows([[(LABELS["cancel"], "ob:v1:cancel")]])


def profile_settings_text_keyboard() -> InlineKeyboardMarkup:
    """Leave profile editing without invoking the onboarding cancellation flow."""

    return _rows([[("Back / Done", "ps:v1:done")]])


def profile_gender_keyboard() -> InlineKeyboardMarkup:
    """Build the deterministic competitive-category choices."""

    return _rows(
        [
            [(LABELS["gender_male"], "ob:v1:profile:gender:MALE")],
            [(LABELS["gender_female"], "ob:v1:profile:gender:FEMALE")],
            [(LABELS["cancel"], "ob:v1:cancel")],
        ]
    )


def equipment_intake_keyboard(
    resources: dict[str, str] | None = None,
    selected: set[str] | None = None,
) -> InlineKeyboardMarkup:
    """Render checkbox-like resource controls with deterministic callbacks."""

    selected = selected or set()
    if not resources:
        return _rows(
            [
                [("Continue", "ob:v1:equipment:done")],
                [(LABELS["cancel"], "ob:v1:cancel")],
            ]
        )
    rows = [
        [
            (
                ("[x] " if resource_id in selected else "[ ] ") + name,
                f"ob:v1:equipment:{resource_id}",
            )
        ]
        for resource_id, name in resources.items()
    ]
    rows.extend(
        [[("Continue", "ob:v1:equipment:done")], [(LABELS["cancel"], "ob:v1:cancel")]]
    )
    return _rows(rows)


def start_keyboard() -> ReplyKeyboardMarkup:
    """Show the only valid account action before an account exists."""

    return _reply_rows([[LABELS["start"]]])


def onboarding_keyboard() -> ReplyKeyboardMarkup:
    """Keep resume and deletion available while onboarding is incomplete."""

    return _reply_rows([[LABELS["resume_menu"]], [LABELS["delete"]]])


def completed_onboarding_keyboard(
    *, plan_available: bool = False
) -> ReplyKeyboardMarkup:
    """Expose the durable completed-account actions."""

    return _reply_rows(
        [
            [LABELS["profile"], LABELS["change_profile"]],
            [
                LABELS["add_workout"],
                LABELS["view_weekly_plan"]
                if plan_available
                else LABELS["plan_next_week"],
            ],
            [LABELS["delete"]],
        ]
    )


def _reply_rows(rows: Sequence[Sequence[str]]) -> ReplyKeyboardMarkup:
    """Build one persistent Telegram reply keyboard."""

    return ReplyKeyboardMarkup(
        [list(row) for row in rows],
        resize_keyboard=True,
        is_persistent=True,
    )


def profile_settings_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [("Goal", "ps:v1:section:goal")],
            [("Availability", "ps:v1:section:availability")],
            [("Equipment & access", "ps:v1:section:equipment")],
            [("Health limitations", "ps:v1:section:health")],
            [("Personal details", "ps:v1:section:personal")],
            [("Back / Done", "ps:v1:done")],
        ]
    )


def profile_goal_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [("Target outcome", "ps:v1:goal:outcome")],
            [("Event date", "ps:v1:goal:date")],
            [("Back", "ps:v1:goal:back")],
        ]
    )


def profile_goal_text_keyboard() -> InlineKeyboardMarkup:
    return _rows([[("Back", "ps:v1:goal:back")]])


def profile_goal_date_keyboard() -> InlineKeyboardMarkup:
    return _rows([[("Not yet", "ps:v1:goal:no-date")], [("Back", "ps:v1:goal:back")]])


def profile_health_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [("None", "ps:v1:health:none")],
            [("Back / Done", "ps:v1:done")],
        ]
    )


def profile_personal_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [("Birth year", "ps:v1:personal:birth_year")],
            [("Category", "ps:v1:personal:gender")],
            [("Weight", "ps:v1:personal:weight")],
            [("Height", "ps:v1:personal:height")],
            [("Back / Done", "ps:v1:back")],
        ]
    )


def profile_settings_gender_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [(LABELS["gender_male"], "ps:v1:personal:gender:MALE")],
            [(LABELS["gender_female"], "ps:v1:personal:gender:FEMALE")],
            [("Back / Done", "ps:v1:back")],
        ]
    )


def profile_equipment_keyboard(
    resources: dict[str, str], selected: set[str]
) -> InlineKeyboardMarkup:
    rows = [
        [(("[x] " if key in selected else "[ ] ") + value, f"ps:v1:equipment:{key}")]
        for key, value in resources.items()
    ]
    rows.extend(
        [[("Continue", "ps:v1:equipment:done")], [("Back / Done", "ps:v1:back")]]
    )
    return _rows(rows)


def health_limitations_keyboard() -> InlineKeyboardMarkup:
    """Offer an explicit no-limitations answer without invoking an LLM."""

    return _rows(
        [
            [(LABELS["health_none"], "ob:v1:health:none")],
            [(LABELS["cancel"], "ob:v1:cancel")],
        ]
    )


def training_history_import_keyboard() -> InlineKeyboardMarkup:
    return _rows(
        [
            [("Skip for now", "ob:v1:history:skip")],
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


def add_workout_keyboard() -> InlineKeyboardMarkup:
    return _rows([[(LABELS["cancel"], "ob:v1:cancel")]])


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
