"""Dispatch coverage for the read-only /zones command."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.bot import messages
from app.bot.service import CoachBotApplicationService
from app.schemas.common import TelegramIdentity
from app.services.athlete_zones import AthleteDisplayZones


def _identity() -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=7001, telegram_username="z", first_name="Z"
    )


@pytest.mark.asyncio
async def test_zones_command_renders_the_view_when_athlete_known() -> None:
    account_queries = AsyncMock()
    account_queries.zones.return_value = AthleteDisplayZones(
        heart_rate=None, running=None, cycling=None, swimming=None
    )
    service = CoachBotApplicationService.__new__(CoachBotApplicationService)
    service._account_queries = account_queries

    response = await service.zones(_identity())

    account_queries.zones.assert_awaited_once_with(_identity())
    assert "birth year" in response.text.lower() or "profile" in response.text.lower()


@pytest.mark.asyncio
async def test_zones_command_reports_not_found_for_unknown_athlete() -> None:
    account_queries = AsyncMock()
    account_queries.zones.return_value = None
    service = CoachBotApplicationService.__new__(CoachBotApplicationService)
    service._account_queries = account_queries

    response = await service.zones(_identity())

    assert response.text == messages.NOT_FOUND
