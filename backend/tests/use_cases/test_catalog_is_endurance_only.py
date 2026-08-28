"""The planned catalog covers swimming, cycling and running only."""

from __future__ import annotations

from app.training_catalog_seed import PRIMARY_GOAL_CODES  # see step 3

RETIRED = {"GENERAL_HIKING", "GENERAL_STRENGTH", "HYROX", "OBSTACLE_RACE"}

EXPECTED_PRIMARY = {
    "GENERAL_RUNNING",
    "RUNNING_5K",
    "RUNNING_10K",
    "HALF_MARATHON",
    "MARATHON",
    "TRAIL_RACE",
    "ROAD_CYCLING_EVENT",
    "MTB_RACE",
    "OPEN_WATER_SWIM",
    "POOL_SWIMMING_EVENT",
    "TRIATHLON_SPRINT",
    "TRIATHLON_OLYMPIC",
    "TRIATHLON_HALF_DISTANCE",
    "TRIATHLON_FULL_DISTANCE",
}


def test_the_seed_offers_only_endurance_primary_goals() -> None:
    assert PRIMARY_GOAL_CODES == EXPECTED_PRIMARY


def test_no_retired_goal_remains_in_the_seed() -> None:
    assert not (PRIMARY_GOAL_CODES & RETIRED)
