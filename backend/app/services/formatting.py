"""Athlete-facing unit formatting.

Pace is stored and computed in seconds everywhere else in this codebase
(running s/km, swimming s/100m) -- this is the single place that converts
it to the min:sec an athlete actually sees. Every athlete-facing surface
that shows a pace (the "view my zones" command, plan rendering, and any
future evaluator output) must call this rather than rendering seconds.
"""

from __future__ import annotations


def format_pace_min_sec(seconds_per_unit: float, *, unit_label: str) -> str:
    """Render e.g. 296 with unit_label="/km" as "4:56/km"."""

    total_seconds = round(seconds_per_unit)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}:{seconds:02d}{unit_label}"


__all__ = ["format_pace_min_sec"]
