"""Domain errors for the first-week evaluator's linking and evaluation flow."""

from __future__ import annotations


class EvaluatorError(Exception):
    """Base class for evaluator failures with application meaning."""


class PlanNotLinkableError(EvaluatorError):
    """The plan revision predates stable session ids, or was superseded."""


class PlannedSessionNotFoundError(EvaluatorError):
    """The session reference does not resolve inside this exact plan revision."""


class WorkoutNotEligibleError(EvaluatorError):
    """The workout falls outside the plan's athlete-local Monday-Sunday week."""


class WorkoutAlreadyLinkedError(EvaluatorError):
    """The workout is already the primary link for a different planned session."""
