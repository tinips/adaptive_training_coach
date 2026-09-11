"""Evaluator-evidence checks for screenshot-captured workouts.

Screenshot capture is the only production workout-ingestion path.  It must
therefore reject an endurance summary that cannot provide the deterministic
first-week evaluator's actual volume and primary-output facts.  This is a
capture-quality gate, not an evaluator verdict: no workout has been saved or
linked when it rejects a screenshot.
"""

from __future__ import annotations

from app.schemas.manual_import import ManualWorkoutImportRequest


class ScreenshotEvaluatorEvidenceRequiredError(RuntimeError):
    """Raised when a screenshot omits an evaluator-required actual metric."""

    def __init__(self, missing_fields: tuple[str, ...]) -> None:
        self.missing_fields = missing_fields
        super().__init__("missing evaluator evidence: " + ", ".join(missing_fields))


def require_evaluator_capture_metrics(request: ManualWorkoutImportRequest) -> None:
    """Require actual facts needed for an evaluable endurance workout.

    Duration is already required by ``ManualWorkoutImportRequest``.  For running
    and swimming, canonical pace is derived deterministically from that duration
    and a positive distance during normalization, so a separately displayed pace
    is not required.  Cycling power has no safe equivalent derivation and is
    therefore required directly.  Average/max HR are checked separately at
    confirmation: average HR is evaluator evidence and max HR is mandated by the
    locked workout-capture rule, while the athlete can supply both when they are
    absent from the screenshot.

    Strength is duration-only in the evaluator, so it has no extra screenshot
    metric requirement.
    """

    missing: list[str] = []
    has_distance = request.distance_meters is not None and request.distance_meters > 0

    if request.discipline in {"RUNNING", "SWIMMING"} and not has_distance:
        missing.append("distance")
    if request.discipline == "CYCLING":
        if not has_distance:
            missing.append("distance")
        if request.average_power_watts is None or request.average_power_watts <= 0:
            missing.append("average power")

    if missing:
        raise ScreenshotEvaluatorEvidenceRequiredError(tuple(missing))


__all__ = [
    "ScreenshotEvaluatorEvidenceRequiredError",
    "require_evaluator_capture_metrics",
]
