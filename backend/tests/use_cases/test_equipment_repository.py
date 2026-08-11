"""Persistence behavior for global athlete equipment access."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import User
from app.domain.enums import Discipline
from app.repositories.equipment import EquipmentRepository
from app.services.equipment import EquipmentRecommendationService
from tests.equipment_seed import seed_equipment_catalog


@pytest_asyncio.fixture
async def equipment_database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory.begin() as session:
        await seed_equipment_catalog(session)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_replacement_deduplicates_and_preserves_unreviewed_disciplines(
    equipment_database: async_sessionmaker[AsyncSession],
) -> None:
    async with equipment_database.begin() as session:
        athlete = User(telegram_user_id=9001)
        session.add(athlete)
        await session.flush()
        repository = EquipmentRepository(session)
        running = await repository.catalog_for_disciplines(
            disciplines=(Discipline.RUNNING,)
        )
        cycling = await repository.catalog_for_disciplines(
            disciplines=(Discipline.CYCLING,)
        )
        running_shoes = next(
            item for item in running if item.equipment == "running_shoes"
        )
        road_bike = next(item for item in cycling if item.equipment == "road_bike")
        stationary = next(
            item for item in cycling if item.equipment == "stationary_bike"
        )

        await repository.replace_for_disciplines(
            athlete_id=athlete.id,
            disciplines=(Discipline.RUNNING,),
            equipment_ids=(running_shoes.id, running_shoes.id),
        )
        await repository.replace_for_disciplines(
            athlete_id=athlete.id,
            disciplines=(Discipline.CYCLING,),
            equipment_ids=(road_bike.id,),
        )
        await repository.replace_for_disciplines(
            athlete_id=athlete.id,
            disciplines=(Discipline.CYCLING,),
            equipment_ids=(stationary.id,),
        )

        selected = await repository.selected_catalog(athlete_id=athlete.id)

    assert {item.equipment for item in selected} == {
        "running_shoes",
        "stationary_bike",
    }


@pytest.mark.asyncio
async def test_athletes_have_isolated_equipment_rows(
    equipment_database: async_sessionmaker[AsyncSession],
) -> None:
    async with equipment_database.begin() as session:
        first = User(telegram_user_id=9002)
        second = User(telegram_user_id=9003)
        session.add_all((first, second))
        await session.flush()
        repository = EquipmentRepository(session)
        cycling = await repository.catalog_for_disciplines(
            disciplines=(Discipline.CYCLING,)
        )
        stationary = next(
            item for item in cycling if item.equipment == "stationary_bike"
        )
        await repository.replace_for_disciplines(
            athlete_id=first.id,
            disciplines=(Discipline.CYCLING,),
            equipment_ids=(stationary.id,),
        )

        changed_goal_review = await EquipmentRecommendationService().review(
            repository=repository,
            athlete_id=first.id,
            main_goal="Finish an Ironman 70.3",
            target_outcome="Finish comfortably",
            secondary_priority=None,
        )

        assert await repository.selected_catalog(athlete_id=second.id) == ()
        assert {
            item.equipment
            for item in await repository.selected_catalog(athlete_id=first.id)
        } == {"stationary_bike"}
        assert changed_goal_review is not None
        assert next(
            item
            for item in changed_goal_review.options
            if item.equipment == "stationary_bike"
        ).selected
