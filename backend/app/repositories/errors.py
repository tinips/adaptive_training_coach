"""Narrow repository exceptions safe for application-service mapping."""

from __future__ import annotations


class RepositoryError(Exception):
    """Base class for persistence failures with application meaning."""


class OwnedRecordNotFoundError(RepositoryError):
    """A record was not found inside the requesting user's ownership scope."""


class ConcurrentSyncError(RepositoryError):
    """A user already has an active Strava synchronization."""


class ExternalIdentityConflictError(RepositoryError):
    """A provider identity is already associated with another local record."""
