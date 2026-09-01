"""Orchestrates screenshot extraction, athlete confirmation, and persistence.

Deliberately not part of ``CoachBotApplicationService``'s LangGraph-routed
facade: confirmation here is two plain buttons, not a conversation state, so
it stays a standalone service with its own narrow protocol, wired into the
bot's handlers directly (see ``app/bot/handlers.py``).
"""

from __future__ import annotations

import re
import secrets
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Workout
from app.integrations.llm.vision import (
    DeepSeekWorkoutScreenshotExtractor,
    ScreenshotExtractionError,
)
from app.repositories.activities import (
    ActivityImportValidationError,
    ActivityUpsertOutcome,
    TrainingActivityRepository,
)
from app.repositories.users import UserRepository
from app.schemas.manual_import import ManualWorkoutImportRequest
from app.services.activities.adapters.manual_screenshot import (
    from_manual_screenshot,
)

# A draft is small and short-lived (confirmed or abandoned within minutes),
# so a bounded in-memory map is enough - no new table for something that
# outlives its usefulness within one bot restart cycle anyway.
_DRAFT_TTL_SECONDS = 30 * 60
_MAX_PENDING_DRAFTS = 500


class WorkoutScreenshotDisabledError(RuntimeError):
    """Raised when the feature flag is off."""


class WorkoutScreenshotNotFoundError(RuntimeError):
    """Raised for an unknown/expired draft, or an unrecognized athlete."""


@dataclass(frozen=True, slots=True)
class ScreenshotDraft:
    """What the bot shows the athlete before it commits anything."""

    token: str
    request: ManualWorkoutImportRequest


@dataclass(slots=True)
class _PendingDraft:
    telegram_user_id: int
    request: ManualWorkoutImportRequest
    awaiting_heart_rate: bool = False
    created_at: float = field(default_factory=time.monotonic)


class WorkoutScreenshotService:
    """Extract, hold for confirmation, then persist one screenshot workout."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        extractor: DeepSeekWorkoutScreenshotExtractor,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._extractor = extractor
        self._pending: dict[str, _PendingDraft] = {}

    async def extract_draft(
        self,
        *,
        telegram_user_id: int,
        image_bytes: bytes,
        image_media_type: str = "image/jpeg",
    ) -> ScreenshotDraft:
        """Read the screenshot and hold the result for the athlete to confirm."""

        self._require_enabled()
        async with self._session_factory() as session:
            user = await UserRepository(session).get_by_telegram_id(telegram_user_id)
        if user is None:
            raise WorkoutScreenshotNotFoundError("athlete not recognized")

        try:
            request = await self._extractor.extract(
                image_bytes=image_bytes,
                image_media_type=image_media_type,
            )
        except ScreenshotExtractionError:
            raise

        token = self._store(telegram_user_id, request)
        return ScreenshotDraft(token=token, request=request)

    async def confirm(
        self,
        *,
        telegram_user_id: int,
        token: str,
    ) -> tuple[Workout, ActivityUpsertOutcome]:
        """Persist a previously extracted draft for its original athlete only."""

        self._require_enabled()
        draft = self._pending.get(token)
        if draft is None or draft.telegram_user_id != telegram_user_id:
            raise WorkoutScreenshotNotFoundError("draft not found or expired")

        async with self._session_factory() as session, session.begin():
            user = await UserRepository(session).get_by_telegram_id(telegram_user_id)
            if user is None:
                raise WorkoutScreenshotNotFoundError("athlete not recognized")

            incoming = from_manual_screenshot(draft.request)
            result = await TrainingActivityRepository(session).import_activity(
                user_id=user.id,
                incoming=incoming,
            )
        self._pending.pop(token, None)
        return result

    def cancel(self, *, telegram_user_id: int, token: str) -> bool:
        """Discard a draft; returns whether one actually existed."""

        draft = self._pending.get(token)
        if draft is None or draft.telegram_user_id != telegram_user_id:
            return False
        self._pending.pop(token, None)
        return True

    def request_heart_rate(self, *, telegram_user_id: int, token: str) -> bool:
        draft = self._pending.get(token)
        if draft is None or draft.telegram_user_id != telegram_user_id:
            return False
        for pending_draft in self._pending.values():
            if pending_draft.telegram_user_id == telegram_user_id:
                pending_draft.awaiting_heart_rate = False
        draft.awaiting_heart_rate = True
        return True

    def provide_heart_rate(
        self, *, telegram_user_id: int, text: str
    ) -> ScreenshotDraft | None:
        draft = next(
            (
                item
                for item in self._pending.values()
                if item.telegram_user_id == telegram_user_id
                and item.awaiting_heart_rate
            ),
            None,
        )
        if draft is None:
            return None
        match = re.fullmatch(r"\s*(\d{2,3})\s*[/,]\s*(\d{2,3})\s*", text)
        if match is None:
            raise ValueError("invalid heart rate")
        average, maximum = (int(match.group(1)), int(match.group(2)))
        if average > maximum or maximum > 300:
            raise ValueError("invalid heart rate")
        draft.request = draft.request.model_copy(
            update={"average_heart_rate": average, "max_heart_rate": maximum}
        )
        draft.awaiting_heart_rate = False
        token = next(key for key, item in self._pending.items() if item is draft)
        return ScreenshotDraft(token=token, request=draft.request)

    def _require_enabled(self) -> None:
        if not self._settings.screenshot_import_enabled:
            raise WorkoutScreenshotDisabledError("screenshot import is disabled")

    def _store(self, telegram_user_id: int, request: ManualWorkoutImportRequest) -> str:
        self._evict_expired()
        if len(self._pending) >= _MAX_PENDING_DRAFTS:
            oldest_token = min(
                self._pending, key=lambda key: self._pending[key].created_at
            )
            self._pending.pop(oldest_token, None)
        token = uuid.UUID(bytes=secrets.token_bytes(16)).hex[:16]
        self._pending[token] = _PendingDraft(
            telegram_user_id=telegram_user_id,
            request=request,
        )
        return token

    def _evict_expired(self) -> None:
        deadline = time.monotonic() - _DRAFT_TTL_SECONDS
        expired = [
            token
            for token, draft in self._pending.items()
            if draft.created_at < deadline
        ]
        for token in expired:
            self._pending.pop(token, None)


__all__ = [
    "ActivityImportValidationError",
    "ScreenshotDraft",
    "WorkoutScreenshotDisabledError",
    "WorkoutScreenshotNotFoundError",
    "WorkoutScreenshotService",
]
