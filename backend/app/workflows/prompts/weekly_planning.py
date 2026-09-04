"""Versioned prompt contract for the persisted weekly training planner."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Final

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.schemas.availability import ConfirmedWeeklyAvailability

ONGOING_WEEKLY_PLANNER_PROMPT_VERSION: Final = 10
FIRST_WEEK_PLANNER_PROMPT_VERSION: Final = 3
# Backward-compatible name for callers that use the ongoing planner.
WEEKLY_PLANNER_PROMPT_VERSION: Final = ONGOING_WEEKLY_PLANNER_PROMPT_VERSION

_WEEKDAY_NAMES: Final = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

_DISCIPLINE_LABELS: Final = {
    "running": "running",
    "cycling": "cycling",
    "swimming": "swimming",
    "strength_training": "strength training",
}

_ONGOING_WEEKLY_PLANNER_SYSTEM_PROMPT: Final = (
    "You are an endurance coach creating one\n"
    "concise weekly plan.\n"
    "Return only the required session-prescription schema for the requested "
    "week. Do not\n"
    """assign dates: a deterministic scheduler will place every session.
Use the complete onboarding context: athlete_profile, goal, confirmed weekly
availability, equipment_and_access, self_reported_baseline, preferences, recent
aggregated evidence, and stated health limitations. athlete_profile gives birth year,
sex/category, weight, height, and timezone. Treat it as contextual information only:
do not make medical claims, estimate physiological measurements, or use it to invent
training targets. The goal contains the main target, supporting priority, event date,
performance targets, target_contexts, and any relevant structured triathlon context.
Use only the goal data that applies to its target disciplines.

The deterministic scheduler creates the seven calendar days and rest notes. Return
only training-session prescriptions; omit rest days. Each session must have an
existing discipline, purpose, structured intensity, clear objective, a targets object,
and concise execution. intensity requires metric, target_range, rpe_range, and
guidance. Use rpe_range for every session, including when another metric is primary.
Do not invent measurements not in the context.

availability_constraints is a non-negotiable, human-readable rendering of the
confirmed availability. Use it to set preferred_weekdays, avoid_weekdays and
can_share_day, but never assign dates yourself. Do not infer exceptions from the
athlete's goal or training needs.

evidence_state tells you how much recent history exists for each target discipline.
Respect it:
WELL_EVIDENCED: enough recent history to plan normally for this discipline.
THIN: very little recent history. Give it one short, easy, clearly introductory
session. Keep rpe_range at or below 4 for it.
SELF_REPORTED: the athlete supplied a structured baseline but has not yet
confirmed it with completed workouts. Use their stated recent training volume and
their supported measurements; if stated recent volume is zero, give one short, easy
introductory session rather than treating zero as a volume target to maintain.
NONE: no recent history at all. The athlete's goal still requires this discipline, so
include one short, easy, introductory session. Keep rpe_range at or below 4 for it.

Plan only the disciplines present in evidence_state. An athlete may have one target
discipline or several.

Each entry in target_contexts carries a role. TARGET means the discipline the
athlete's event is in; it gets the bulk of the week. SUPPORTING means a discipline
that exists to support the target, such as strength work to maintain muscle. Give a
supporting discipline one or two short sessions and never let it displace target
training.

per_discipline_target_minutes is the stated total for each discipline this week.
Plan that volume. desired_weekly_sessions is the requested frequency for each
discipline: it controls session count, while per_discipline_target_minutes controls
total volume. If desired_sessions_fit_availability is false, plan what actually fits
and say so plainly in the affected session objectives.

Every session has purpose, targets, intensity, objective, execution, priority, and
weekday preferences. purpose explains the adaptation or skill the session develops;
objective explains the specific session outcome. Do not duplicate them.
Put duration_minutes in targets
for every session, and add distance, heart rate, power, pace, or RPE only where the
athlete's own context supports it. Leave an unsupported target null rather than
guessing. objective says why the session exists in one line; execution says how to
run it. Do not duplicate target numbers in execution.

Any discipline in untrained_disciplines has never been trained by this athlete. Give
it at most two short easy sessions. For untrained swimming, use pool technique:
breathing and exhaling underwater, comfort and body position, short 25m repeats and
generous rest. Never give an untrained swimmer continuous-distance or pace targets.
All swimming this week is in a pool; never plan open-water swimming.

self_reported_baseline contains each discipline's weekly sessions, weekly minutes,
and longest recent session, plus supported details such as running race results,
cycling environment/confidence/FTP, swimming environment/pool length/continuous
distance, and triathlon experience, weakest discipline, and open-water confidence.
Use these details only when present and applicable. preferences contains coaching_style,
desired_weekly_sessions, and desired_sessions_fit_availability; respect them within
the safety and availability rules above."""
)


_FIRST_WEEK_PLANNER_SYSTEM_PROMPT: Final = """You are an endurance coach creating
the athlete's first-week probe menu. Return only the required first-week menu
prescription schema. Do not assign dates or create a calendar: the athlete chooses
which allowed day and time to complete each session.

This is a familiarization and calibration week, not an event-preparation week. The
context intentionally excludes event date, distance, pace, finish time, and goal
metadata. Do not infer or train toward an event. Plan only the supplied
planned_disciplines, recent evidence, structured baseline, confirmed availability,
equipment/access, health limitations, athlete profile, and preferences.

resolved_intensity_zones is authoritative. If a discipline is RPE_FALLBACK, prescribe
only RPE/feel with descriptive guidance; never invent pace, power, or heart-rate
targets. If numeric zones are supplied, keep numeric IntensityTargets inside one of
their supplied ranges. Never prescribe a maximal test, benchmark, all-out effort, or
VO2max test. first_week_baseline_tiers is authoritative and defines preparation for
each discipline. The week must characterize the athlete where they are: an all-easy
week gathers no useful intensity signal from a trained athlete. For UNPREPARED, use
only easy, low-volume work. For DEVELOPING, use easy work plus one controlled moderate
effort when a numeric zone is available. For TRAINED and WELL_TRAINED, use easy base
work plus one or two controlled tempo or threshold sessions in the supplied numeric
zones. Coaching style shifts the amount and placement of this work within the tier;
it never overrides the unprepared rule. Controlled tempo/threshold is not a maximal
test. Do not make medical claims or invent measurements.

Every session must contain purpose, structured intensity, objective, targets,
execution. intensity requires metric, target_range, rpe_range, and guidance. Keep
unprepared disciplines easy, but do not apply a universal RPE cap. Make sessions
distinct in purpose, intensity, and execution; do not repeat a session in the same
discipline unless its role is explicitly different. purpose
explains the adaptation or skill the session develops; objective states the specific
session outcome. Put duration_minutes in targets. Use only targets supported by the
athlete's context. Meet desired_weekly_sessions for every safely prepared discipline;
do not force sessions for a zero-baseline unprepared endurance discipline. Choose each
session duration from the baseline and available windows rather than assuming a fixed
duration. Where it fits the recovery and discipline constraints, use longer windows
(such as weekends) for sessions that benefit from them.

availability_constraints is non-negotiable. Ensure every individual menu session fits
at least one allowed discipline/window, but do not name days or times. Respect
equipment/access and health limitations. Include concise guardrails and explicit
logging instructions: record actual day/time, duration, RPE, how it felt, and any
available pace/power/heart-rate. tests must be empty. All swimming this week is in a
pool; never plan open-water swimming. For untrained swimming, use breathing, body
position, short 25m repeats, and generous rest; never prescribe continuous-distance or
pace targets."""


def render_availability_constraints(
    availability: ConfirmedWeeklyAvailability | None,
    week_start: date,
) -> str | None:
    """Render confirmed availability as unambiguous planner instructions."""

    if availability is None:
        return None

    lines = ["Non-negotiable availability constraints:"]
    permitted_days: dict[str, list[str]] = {
        discipline: [] for discipline in _DISCIPLINE_LABELS
    }
    all_dates: list[str] = []
    for offset, day_name in enumerate(_WEEKDAY_NAMES):
        current_date = week_start + timedelta(days=offset)
        date_label = f"{day_name} {current_date.isoformat()}"
        all_dates.append(date_label)
        day = availability.days[day_name.lower()]
        if not day.available:
            lines.append(f"- {date_label}: unavailable; schedule no sessions.")
            continue

        disciplines = ", ".join(
            _DISCIPLINE_LABELS[discipline] for discipline in day.disciplines
        )
        windows = "; ".join(
            (
                f"{window.time_of_day} up to {window.duration_minutes} min"
                if window.time_of_day is not None
                else f"any time up to {window.duration_minutes} min"
            )
            for window in day.time_windows
        )
        lines.append(f"- {date_label}: {disciplines}; available {windows}.")
        for discipline in day.disciplines:
            permitted_days[discipline].append(date_label)

    for discipline_name, days in permitted_days.items():
        if days and len(days) < len(all_dates):
            discipline_label = _DISCIPLINE_LABELS[discipline_name].capitalize()
            lines.append(
                f"- {discipline_label} is permitted only on {', '.join(days)}."
            )

    return "\n".join(lines)


def build_weekly_planner_messages(
    context: Mapping[str, object],
) -> list[BaseMessage]:
    """Build the only prompt sent for a weekly-plan generation request.

    Callers own the transient context. This function does not log, persist, or
    transform the confirmed availability schedule or health-limitations text.
    """

    return [
        SystemMessage(_ONGOING_WEEKLY_PLANNER_SYSTEM_PROMPT),
        HumanMessage(
            json.dumps(
                dict(context),
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
    ]


def build_first_week_planner_messages(
    context: Mapping[str, object],
) -> list[BaseMessage]:
    """Build the probe-only prompt used before ongoing weekly planning begins."""

    return [
        SystemMessage(_FIRST_WEEK_PLANNER_SYSTEM_PROMPT),
        HumanMessage(
            json.dumps(
                dict(context),
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
    ]
