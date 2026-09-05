"""Dispatch coverage for the read-only /zones command."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage

from app.bot import messages
from app.bot.service import CoachBotApplicationService, WeeklyPlanningBotPort
from app.domain.enums import UserStatus
from app.schemas.common import TelegramIdentity
from app.services.accounts.service import AccountQueryService, AccountService
from app.services.athlete_zones import AthleteDisplayZones, ReferenceHeartRateZones
from app.services.onboarding import OnboardingService
from app.services.profiles import ProfileService


def _identity() -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=7001, telegram_username="z", first_name="Z"
    )


def _zones() -> AthleteDisplayZones:
    return AthleteDisplayZones(
        heart_rate=ReferenceHeartRateZones(
            estimated_max_hr_bpm=182.8,
            easy=(110, 137),
            moderate=(139, 155),
            hard=(157, 168),
        ),
        running=None,
        cycling=None,
        swimming=None,
    )


def _facade(account_queries: object) -> CoachBotApplicationService:
    return CoachBotApplicationService(
        onboarding=cast(OnboardingService, SimpleNamespace()),
        profiles=cast(ProfileService, SimpleNamespace()),
        account_queries=cast(AccountQueryService, account_queries),
        accounts=cast(AccountService, SimpleNamespace()),
        planning=cast(
            WeeklyPlanningBotPort,
            SimpleNamespace(has_plan_for_next_week=AsyncMock(return_value=True)),
        ),
    )


@pytest.mark.asyncio
async def test_zones_command_renders_the_view_when_athlete_known() -> None:
    account_queries = AsyncMock()
    account_queries.zones.return_value = _zones()
    service = CoachBotApplicationService.__new__(CoachBotApplicationService)
    service._account_queries = account_queries

    response = await service.zones(_identity())

    account_queries.zones.assert_awaited_once_with(_identity())
    assert "110-137 bpm" in response.text
    assert "182.8" in response.text or "183" in response.text


@pytest.mark.asyncio
async def test_zones_command_reports_not_found_for_unknown_athlete() -> None:
    account_queries = AsyncMock()
    account_queries.zones.return_value = None
    service = CoachBotApplicationService.__new__(CoachBotApplicationService)
    service._account_queries = account_queries

    response = await service.zones(_identity())

    assert response.text == messages.NOT_FOUND


@pytest.mark.asyncio
async def test_zones_takes_the_deterministic_route_like_every_other_command() -> None:
    """Go through the real dispatcher, so reordering its branches cannot drop /zones.

    Every reply looks the athlete's lifecycle up twice to pick the keyboard. A
    command on the deterministic route set adds no third lookup, because it
    returns before the dispatcher's own lifecycle branch. Pinning the count is
    what proves /zones is routed explicitly rather than reaching its handler
    through the fall-through that happened to carry it.
    """

    account_queries = SimpleNamespace(
        zones=AsyncMock(return_value=_zones()),
        lifecycle=AsyncMock(
            return_value={
                "user_id": uuid4(),
                "status": UserStatus.ONBOARDING_COMPLETED,
            }
        ),
    )

    response = await _facade(account_queries).handle_agent_input(
        _identity(),
        HumanMessage(content="/zones"),
    )

    account_queries.zones.assert_awaited_once_with(_identity())
    assert account_queries.lifecycle.await_count == 2
    assert "110-137 bpm" in response.text
