"""Owner-scoped exact source identity and traceability persistence."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ActivitySourceLink, AppleHealthImportJob, Workout
from app.domain.enums import ActivitySource
from app.repositories.errors import OwnedRecordNotFoundError
from app.services.activities.contracts import (
    ActivityImportData,
    ActivitySourceConflictError,
)
from app.services.activities.normalization import json_safe


class ActivitySourceLinkRepository:
    """Persist exact provider keys without cross-source matching."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_workout(
        self,
        *,
        user_id: uuid.UUID,
        source: ActivitySource,
        external_id: str,
    ) -> Workout | None:
        """Resolve one exact owner/source/external-id identity."""

        linked = await self._session.scalar(
            select(Workout)
            .join(
                ActivitySourceLink,
                ActivitySourceLink.workout_id == Workout.id,
            )
            .where(
                ActivitySourceLink.user_id == user_id,
                ActivitySourceLink.source == source,
                ActivitySourceLink.external_id == external_id,
                Workout.athlete_id == user_id,
            )
        )
        if linked is not None:
            return linked
        direct: Workout | None = await self._session.scalar(
            select(Workout).where(
                Workout.athlete_id == user_id,
                Workout.source == source,
                Workout.external_id == external_id,
            )
        )
        return direct

    async def ensure(
        self,
        *,
        user_id: uuid.UUID,
        workout: Workout,
        incoming: ActivityImportData,
        file_sha256: str | None,
        import_job_id: uuid.UUID | None,
    ) -> tuple[ActivitySourceLink, bool]:
        """Create or refresh the exact identity link for one owned workout."""

        if workout.athlete_id != user_id:
            raise OwnedRecordNotFoundError("Workout not found")
        if incoming.external_id is None:
            raise ValueError("Normalized external_id is required")
        await self._owned_import_job(
            user_id=user_id,
            import_job_id=import_job_id,
        )
        existing = await self._get(
            user_id=user_id,
            source=incoming.source,
            external_id=incoming.external_id,
        )
        values = _source_link_values(
            incoming,
            file_sha256=file_sha256,
            import_job_id=import_job_id,
        )
        if existing is not None:
            if existing.workout_id != workout.id:
                raise ActivitySourceConflictError(
                    "Source key is already linked to another workout"
                )
            _preserve_existing_traceability(existing, values)
            changed = _assign_nonidentical(existing, values)
            if changed:
                await self._session.flush()
            return existing, changed

        link = ActivitySourceLink(
            user_id=user_id,
            workout_id=workout.id,
            source=incoming.source,
            external_id=incoming.external_id,
            **values,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(link)
                await self._session.flush()
        except IntegrityError as error:
            existing = await self._get(
                user_id=user_id,
                source=incoming.source,
                external_id=incoming.external_id,
            )
            if existing is None or existing.workout_id != workout.id:
                raise ActivitySourceConflictError(
                    "Source key is already linked to another workout"
                ) from error
            _preserve_existing_traceability(existing, values)
            changed = _assign_nonidentical(existing, values)
            return existing, changed
        await self._session.refresh(workout, attribute_names=["source_links"])
        return link, True

    async def attach_import_job(
        self,
        *,
        user_id: uuid.UUID,
        import_job_id: uuid.UUID | None,
        workout: Workout,
    ) -> None:
        job = await self._owned_import_job(
            user_id=user_id,
            import_job_id=import_job_id,
        )
        if job is not None and job.workout_id != workout.id:
            job.workout_id = workout.id

    async def _get(
        self,
        *,
        user_id: uuid.UUID,
        source: ActivitySource,
        external_id: str,
    ) -> ActivitySourceLink | None:
        link: ActivitySourceLink | None = await self._session.scalar(
            select(ActivitySourceLink).where(
                ActivitySourceLink.user_id == user_id,
                ActivitySourceLink.source == source,
                ActivitySourceLink.external_id == external_id,
            )
        )
        return link

    async def _owned_import_job(
        self,
        *,
        user_id: uuid.UUID,
        import_job_id: uuid.UUID | None,
    ) -> AppleHealthImportJob | None:
        if import_job_id is None:
            return None
        job = await self._session.scalar(
            select(AppleHealthImportJob).where(
                AppleHealthImportJob.id == import_job_id,
                AppleHealthImportJob.user_id == user_id,
            )
        )
        if job is None:
            raise OwnedRecordNotFoundError("Import job not found")
        return job


def _source_link_values(
    incoming: ActivityImportData,
    *,
    file_sha256: str | None,
    import_job_id: uuid.UUID | None,
) -> dict[str, object]:
    return {
        "raw_sport": incoming.raw_sport,
        "raw_sub_sport": incoming.raw_sub_sport,
        "source_metadata_jsonb": json_safe(incoming.source_metadata),
        "file_sha256": file_sha256,
        "import_job_id": import_job_id,
    }


def _preserve_existing_traceability(
    existing: ActivitySourceLink,
    values: dict[str, object],
) -> None:
    if values["file_sha256"] is None and existing.file_sha256 is not None:
        values["file_sha256"] = existing.file_sha256
    if values["import_job_id"] is None and existing.import_job_id is not None:
        values["import_job_id"] = existing.import_job_id
    migration_provenance = _migration_provenance(existing.source_metadata_jsonb)
    if migration_provenance is not None:
        incoming_metadata = values["source_metadata_jsonb"]
        preserved_metadata = (
            dict(incoming_metadata) if isinstance(incoming_metadata, Mapping) else {}
        )
        preserved_metadata["migration_provenance"] = migration_provenance
        values["source_metadata_jsonb"] = preserved_metadata


def _migration_provenance(metadata: object) -> dict[str, object] | None:
    if not isinstance(metadata, Mapping):
        return None
    nested = metadata.get("migration_provenance")
    for candidate in (nested, metadata):
        if (
            isinstance(candidate, Mapping)
            and candidate.get("migration_revision") == "0004_discipline_workout_models"
            and isinstance(candidate.get("legacy_activity"), Mapping)
            and isinstance(candidate.get("canonical_snapshot"), Mapping)
        ):
            return {str(key): value for key, value in candidate.items()}
    return None


def _assign_nonidentical(target: object, values: Mapping[str, object]) -> bool:
    changed = False
    for name, value in values.items():
        if getattr(target, name) != value:
            setattr(target, name, value)
            changed = True
    return changed


__all__ = ["ActivitySourceLinkRepository"]
