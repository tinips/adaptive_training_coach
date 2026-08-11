"""Portable starter-catalog seed for SQLite application tests."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EquipmentCatalog
from app.domain.enums import Discipline, EquipmentImportance

_NAMESPACE = uuid.UUID("36d470bc-c860-4ba7-ac0d-c90156c79ad7")

CATALOG = (
    (
        Discipline.RUNNING,
        "running_shoes",
        "Running shoes",
        EquipmentImportance.ESSENTIAL,
        ["trail_running_shoes"],
    ),
    (
        Discipline.RUNNING,
        "safe_running_route",
        "Safe running route",
        EquipmentImportance.RECOMMENDED,
        ["treadmill_access"],
    ),
    (
        Discipline.RUNNING,
        "trail_running_shoes",
        "Trail running shoes",
        EquipmentImportance.RECOMMENDED,
        [],
    ),
    (
        Discipline.RUNNING,
        "treadmill_access",
        "Treadmill access",
        EquipmentImportance.OPTIONAL,
        [],
    ),
    (
        Discipline.RUNNING,
        "sports_watch",
        "Sports watch",
        EquipmentImportance.OPTIONAL,
        [],
    ),
    (
        Discipline.CYCLING,
        "bike",
        "Bike",
        EquipmentImportance.ESSENTIAL,
        ["road_bike", "mountain_bike", "stationary_bike"],
    ),
    (Discipline.CYCLING, "road_bike", "Road bike", EquipmentImportance.RECOMMENDED, []),
    (Discipline.CYCLING, "helmet", "Helmet", EquipmentImportance.RECOMMENDED, []),
    (
        Discipline.CYCLING,
        "mountain_bike",
        "Mountain bike",
        EquipmentImportance.OPTIONAL,
        [],
    ),
    (
        Discipline.CYCLING,
        "stationary_bike",
        "Stationary bike",
        EquipmentImportance.OPTIONAL,
        [],
    ),
    (Discipline.CYCLING, "repair_kit", "Repair kit", EquipmentImportance.OPTIONAL, []),
    (
        Discipline.SWIMMING,
        "swimming_access",
        "Swimming access",
        EquipmentImportance.ESSENTIAL,
        ["pool_access", "open_water_access"],
    ),
    (
        Discipline.SWIMMING,
        "pool_access",
        "Pool access",
        EquipmentImportance.RECOMMENDED,
        [],
    ),
    (Discipline.SWIMMING, "goggles", "Goggles", EquipmentImportance.RECOMMENDED, []),
    (
        Discipline.SWIMMING,
        "open_water_access",
        "Open-water access",
        EquipmentImportance.OPTIONAL,
        [],
    ),
    (Discipline.SWIMMING, "wetsuit", "Wetsuit", EquipmentImportance.OPTIONAL, []),
    (
        Discipline.HIKING,
        "suitable_hiking_footwear",
        "Suitable hiking footwear",
        EquipmentImportance.ESSENTIAL,
        ["hiking_shoes", "trail_running_shoes"],
    ),
    (
        Discipline.HIKING,
        "hiking_shoes",
        "Hiking shoes",
        EquipmentImportance.RECOMMENDED,
        [],
    ),
    (Discipline.HIKING, "backpack", "Backpack", EquipmentImportance.RECOMMENDED, []),
    (
        Discipline.HIKING,
        "trail_running_shoes",
        "Trail running shoes",
        EquipmentImportance.OPTIONAL,
        [],
    ),
    (
        Discipline.HIKING,
        "trekking_poles",
        "Trekking poles",
        EquipmentImportance.OPTIONAL,
        [],
    ),
    (
        Discipline.STRENGTH,
        "training_space",
        "Training space",
        EquipmentImportance.ESSENTIAL,
        ["gym_access", "home_training_space"],
    ),
    (
        Discipline.STRENGTH,
        "resistance_bands",
        "Resistance bands",
        EquipmentImportance.RECOMMENDED,
        [],
    ),
    (Discipline.STRENGTH, "gym_access", "Gym access", EquipmentImportance.OPTIONAL, []),
    (
        Discipline.STRENGTH,
        "home_training_space",
        "Home training space",
        EquipmentImportance.OPTIONAL,
        [],
    ),
    (
        Discipline.STRENGTH,
        "free_weights",
        "Free weights",
        EquipmentImportance.OPTIONAL,
        [],
    ),
)


async def seed_equipment_catalog(session: AsyncSession) -> None:
    session.add_all(
        EquipmentCatalog(
            id=uuid.uuid5(_NAMESPACE, f"{discipline.value}:{equipment}"),
            discipline=discipline,
            equipment=equipment,
            display_name=display_name,
            importance=importance,
            substitutions=substitutions,
        )
        for discipline, equipment, display_name, importance, substitutions in CATALOG
    )
    await session.flush()
