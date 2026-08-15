"""Version-one reusable training catalog seed data.

The Alembic migration and focused tests share these immutable tuples.  Existing
entries must not be edited after release; publish later catalog corrections as
new migrations or runtime-generated definitions.
"""

from __future__ import annotations

import uuid

CATALOG_NAMESPACE = uuid.UUID("78328ba8-1bf2-4f37-bd37-88d74c844f16")


def catalog_id(kind: str, code: str) -> uuid.UUID:
    return uuid.uuid5(CATALOG_NAMESPACE, f"{kind}:{code}")


# code, kind, display name, classifier description
GOAL_TEMPLATES = (
    (
        "GENERAL_RUNNING",
        "PRIMARY",
        "General running",
        "General running fitness or habit.",
    ),
    (
        "RUNNING_5K",
        "PRIMARY",
        "5K running",
        "Road or general five-kilometre running event.",
    ),
    (
        "RUNNING_10K",
        "PRIMARY",
        "10K running",
        "Road or general ten-kilometre running event.",
    ),
    (
        "HALF_MARATHON",
        "PRIMARY",
        "Half marathon",
        "Road or general half-marathon event.",
    ),
    ("MARATHON", "PRIMARY", "Marathon", "Road or general marathon event."),
    ("TRAIL_RACE", "PRIMARY", "Trail race", "Trail-running race of any distance."),
    ("GENERAL_HIKING", "PRIMARY", "General hiking", "Hiking or trekking preparation."),
    (
        "ROAD_CYCLING_EVENT",
        "PRIMARY",
        "Road cycling event",
        "Road cycling race, sportive, or gran fondo.",
    ),
    (
        "MTB_RACE",
        "PRIMARY",
        "Mountain-bike race",
        "Mountain-bike or cross-country cycling race.",
    ),
    (
        "POOL_SWIMMING_EVENT",
        "PRIMARY",
        "Pool swimming event",
        "Pool swimming competition or performance goal.",
    ),
    (
        "OPEN_WATER_SWIM",
        "PRIMARY",
        "Open-water swim",
        "Open-water swimming event or crossing.",
    ),
    ("TRIATHLON_SPRINT", "PRIMARY", "Sprint triathlon", "Sprint-distance triathlon."),
    (
        "TRIATHLON_OLYMPIC",
        "PRIMARY",
        "Olympic triathlon",
        "Olympic-distance or standard-distance triathlon.",
    ),
    (
        "TRIATHLON_HALF_DISTANCE",
        "PRIMARY",
        "Half-distance triathlon",
        "Half-distance triathlon, Ironman 70.3, or half Ironman.",
    ),
    (
        "TRIATHLON_FULL_DISTANCE",
        "PRIMARY",
        "Full-distance triathlon",
        "Full-distance triathlon or Ironman.",
    ),
    (
        "GENERAL_STRENGTH",
        "PRIMARY",
        "General strength",
        "General strength or resistance-training goal.",
    ),
    ("HYROX", "PRIMARY", "HYROX", "HYROX running and functional-fitness event."),
    (
        "OBSTACLE_RACE",
        "PRIMARY",
        "Obstacle race",
        "Obstacle-course race such as Spartan Sprint, Super, or Beast.",
    ),
    (
        "MUSCLE_RETENTION",
        "SUPPORTING",
        "Maintain muscle",
        "Preserve muscle while pursuing another training goal.",
    ),
    (
        "STRENGTH_MAINTENANCE",
        "SUPPORTING",
        "Maintain strength",
        "Preserve current strength while pursuing another goal.",
    ),
    (
        "IMPROVE_RUNNING",
        "SUPPORTING",
        "Improve running",
        "Give additional planning emphasis to running.",
    ),
    (
        "IMPROVE_CYCLING",
        "SUPPORTING",
        "Improve cycling",
        "Give additional planning emphasis to cycling.",
    ),
    (
        "IMPROVE_SWIMMING",
        "SUPPORTING",
        "Improve swimming",
        "Give additional planning emphasis to swimming.",
    ),
)

# code, broad discipline, display name, description
TRAINING_CONTEXTS = (
    (
        "running_road",
        "RUNNING",
        "Road running",
        "Outdoor running on road or a safe general route.",
    ),
    (
        "running_trail",
        "RUNNING",
        "Trail running",
        "Running on trails or uneven natural terrain.",
    ),
    (
        "running_treadmill",
        "RUNNING",
        "Treadmill running",
        "Indoor running on a treadmill.",
    ),
    (
        "cycling_road",
        "CYCLING",
        "Road cycling",
        "Cycling targeted at road-event demands.",
    ),
    (
        "cycling_mountain",
        "CYCLING",
        "Mountain biking",
        "Cycling on mountain-bike terrain or equipment.",
    ),
    (
        "cycling_stationary",
        "CYCLING",
        "Stationary cycling",
        "Indoor cycling on a stationary bike or trainer.",
    ),
    ("swimming_pool", "SWIMMING", "Pool swimming", "Swimming training in a pool."),
    (
        "swimming_open_water",
        "SWIMMING",
        "Open-water swimming",
        "Swimming in open water.",
    ),
    (
        "strength_general",
        "STRENGTH",
        "General strength",
        "General resistance-training stimulus.",
    ),
    (
        "strength_gym",
        "STRENGTH",
        "Gym strength",
        "Strength training in an equipped gym.",
    ),
    (
        "strength_home",
        "STRENGTH",
        "Home strength",
        "Strength training with compact home equipment.",
    ),
    (
        "functional_fitness",
        "STRENGTH",
        "Functional fitness",
        "Mixed functional strength and conditioning.",
    ),
    (
        "grip_training",
        "STRENGTH",
        "Grip training",
        "Grip and hanging-strength preparation.",
    ),
    (
        "obstacle_practice",
        "OTHER",
        "Obstacle practice",
        "Practice of obstacle-specific movement skills.",
    ),
    ("hiking_trail", "HIKING", "Trail hiking", "Hiking or trekking on trails."),
)

# goal code, context code, role, priority
GOAL_CONTEXTS = (
    *(
        (code, "running_road", "TARGET", 10)
        for code in (
            "GENERAL_RUNNING",
            "RUNNING_5K",
            "RUNNING_10K",
            "HALF_MARATHON",
            "MARATHON",
        )
    ),
    ("TRAIL_RACE", "running_trail", "TARGET", 10),
    ("GENERAL_HIKING", "hiking_trail", "TARGET", 10),
    ("ROAD_CYCLING_EVENT", "cycling_road", "TARGET", 10),
    ("MTB_RACE", "cycling_mountain", "TARGET", 10),
    ("POOL_SWIMMING_EVENT", "swimming_pool", "TARGET", 10),
    ("OPEN_WATER_SWIM", "swimming_open_water", "TARGET", 10),
    *(
        (goal, context, "TARGET", priority)
        for goal in (
            "TRIATHLON_SPRINT",
            "TRIATHLON_OLYMPIC",
            "TRIATHLON_HALF_DISTANCE",
            "TRIATHLON_FULL_DISTANCE",
        )
        for context, priority in (
            ("running_road", 10),
            ("cycling_road", 20),
            ("swimming_open_water", 30),
        )
    ),
    *(
        (goal, "swimming_pool", "SUPPORTING", 40)
        for goal in (
            "TRIATHLON_SPRINT",
            "TRIATHLON_OLYMPIC",
            "TRIATHLON_HALF_DISTANCE",
            "TRIATHLON_FULL_DISTANCE",
        )
    ),
    ("GENERAL_STRENGTH", "strength_general", "TARGET", 10),
    ("HYROX", "running_road", "TARGET", 10),
    ("HYROX", "functional_fitness", "TARGET", 20),
    ("OBSTACLE_RACE", "running_trail", "TARGET", 10),
    ("OBSTACLE_RACE", "functional_fitness", "SUPPORTING", 20),
    ("OBSTACLE_RACE", "grip_training", "SUPPORTING", 30),
    ("OBSTACLE_RACE", "obstacle_practice", "SUPPORTING", 40),
    ("MUSCLE_RETENTION", "strength_general", "SUPPORTING", 10),
    ("STRENGTH_MAINTENANCE", "strength_general", "SUPPORTING", 10),
    ("IMPROVE_RUNNING", "running_road", "SUPPORTING", 10),
    ("IMPROVE_CYCLING", "cycling_road", "SUPPORTING", 10),
    ("IMPROVE_SWIMMING", "swimming_pool", "SUPPORTING", 10),
)

# code, display name, kind, description
CAPABILITIES = (
    (
        "running_shoes",
        "Running shoes",
        "EQUIPMENT",
        "Shoes suitable for general running.",
    ),
    (
        "trail_running_shoes",
        "Trail running shoes",
        "EQUIPMENT",
        "Shoes suitable for trail terrain.",
    ),
    (
        "treadmill_access",
        "Treadmill access",
        "ACCESS",
        "Reliable access to a treadmill.",
    ),
    (
        "sports_watch",
        "Sports watch",
        "EQUIPMENT",
        "A watch capable of recording training.",
    ),
    ("road_bike", "Road bike", "EQUIPMENT", "A road or triathlon bicycle."),
    ("mountain_bike", "Mountain bike", "EQUIPMENT", "A mountain bicycle."),
    (
        "stationary_bike",
        "Stationary bike",
        "EQUIPMENT",
        "A stationary bike or indoor trainer.",
    ),
    ("helmet", "Cycling helmet", "EQUIPMENT", "A suitable cycling helmet."),
    (
        "repair_kit",
        "Repair kit",
        "EQUIPMENT",
        "Basic equipment for common cycling repairs.",
    ),
    ("pool_access", "Pool access", "ACCESS", "Reliable access to a swimming pool."),
    (
        "open_water_access",
        "Open-water access",
        "ACCESS",
        "Access to a suitable open-water swimming location.",
    ),
    ("goggles", "Swimming goggles", "EQUIPMENT", "Swimming goggles."),
    (
        "wetsuit",
        "Swimming wetsuit",
        "EQUIPMENT",
        "A wetsuit suitable for open-water swimming.",
    ),
    ("hiking_shoes", "Hiking shoes", "EQUIPMENT", "Footwear suitable for hiking."),
    ("gym_access", "Gym access", "FACILITY", "Access to an equipped gym."),
    (
        "home_training_space",
        "Home training space",
        "FACILITY",
        "Space suitable for training at home.",
    ),
    (
        "resistance_bands",
        "Resistance bands",
        "EQUIPMENT",
        "Resistance bands for strength training.",
    ),
    (
        "free_weights",
        "Free weights",
        "EQUIPMENT",
        "Dumbbells, barbells, or kettlebells.",
    ),
    (
        "pull_up_bar",
        "Pull-up bar",
        "EQUIPMENT",
        "A fixed bar suitable for hanging exercises.",
    ),
    (
        "obstacle_access",
        "Obstacle facility access",
        "ACCESS",
        "Access to obstacle-specific practice facilities.",
    ),
    (
        "bike_access_unspecified",
        "Unspecified bike access",
        "ACCESS",
        "Migrated bike access whose type is not known.",
    ),
    (
        "swimming_access_unspecified",
        "Unspecified swimming access",
        "ACCESS",
        "Migrated swimming access whose environment is not known.",
    ),
    (
        "hiking_footwear_unspecified",
        "Unspecified hiking footwear",
        "EQUIPMENT",
        "Migrated suitable hiking footwear whose specific type is not known.",
    ),
)

# target context, option code, label, execution context, role, priority, limitations
EXECUTION_OPTIONS = (
    (
        "running_road",
        "outdoor_road",
        "Outdoor road running",
        "running_road",
        "PREFERRED",
        10,
        (),
    ),
    (
        "running_road",
        "treadmill",
        "Treadmill running",
        "running_treadmill",
        "SUBSTITUTE",
        20,
        ("Does not reproduce outdoor terrain or weather.",),
    ),
    ("running_trail", "trail", "Trail running", "running_trail", "PREFERRED", 10, ()),
    (
        "running_trail",
        "road",
        "Road running",
        "running_road",
        "SUBSTITUTE",
        20,
        ("Does not reproduce uneven trail terrain.",),
    ),
    (
        "running_trail",
        "treadmill",
        "Treadmill running",
        "running_treadmill",
        "SUBSTITUTE",
        30,
        ("Does not reproduce technical trail terrain.",),
    ),
    (
        "running_treadmill",
        "treadmill",
        "Treadmill running",
        "running_treadmill",
        "PREFERRED",
        10,
        (),
    ),
    ("cycling_road", "road_bike", "Road bike", "cycling_road", "PREFERRED", 10, ()),
    (
        "cycling_road",
        "mountain_bike",
        "Mountain bike",
        "cycling_mountain",
        "SUBSTITUTE",
        20,
        ("Does not reproduce road-bike handling or position.",),
    ),
    (
        "cycling_road",
        "stationary_bike",
        "Stationary bike",
        "cycling_stationary",
        "SUBSTITUTE",
        30,
        ("Does not reproduce outdoor handling or descending.",),
    ),
    (
        "cycling_mountain",
        "mountain_bike",
        "Mountain bike",
        "cycling_mountain",
        "PREFERRED",
        10,
        (),
    ),
    (
        "cycling_mountain",
        "road_bike",
        "Road bike",
        "cycling_road",
        "SUBSTITUTE",
        20,
        ("Does not reproduce technical mountain-bike terrain.",),
    ),
    (
        "cycling_mountain",
        "stationary_bike",
        "Stationary bike",
        "cycling_stationary",
        "SUBSTITUTE",
        30,
        ("Provides conditioning without terrain skills.",),
    ),
    (
        "cycling_stationary",
        "stationary_bike",
        "Stationary bike",
        "cycling_stationary",
        "PREFERRED",
        10,
        (),
    ),
    ("swimming_pool", "pool", "Pool swimming", "swimming_pool", "PREFERRED", 10, ()),
    (
        "swimming_open_water",
        "open_water",
        "Open-water swimming",
        "swimming_open_water",
        "PREFERRED",
        10,
        (),
    ),
    (
        "swimming_open_water",
        "pool",
        "Pool swimming",
        "swimming_pool",
        "SUBSTITUTE",
        20,
        ("Does not reproduce open-water navigation or conditions.",),
    ),
    ("strength_general", "gym", "Gym strength", "strength_gym", "PREFERRED", 10, ()),
    (
        "strength_general",
        "home",
        "Home strength",
        "strength_home",
        "SUBSTITUTE",
        20,
        ("Exercise selection depends on available home equipment.",),
    ),
    ("strength_gym", "gym", "Gym strength", "strength_gym", "PREFERRED", 10, ()),
    ("strength_home", "home", "Home strength", "strength_home", "PREFERRED", 10, ()),
    (
        "functional_fitness",
        "gym",
        "Gym functional fitness",
        "functional_fitness",
        "PREFERRED",
        10,
        (),
    ),
    (
        "functional_fitness",
        "home",
        "Home functional fitness",
        "strength_home",
        "SUBSTITUTE",
        20,
        ("May not reproduce event-specific loading or machines.",),
    ),
    (
        "grip_training",
        "pull_up_bar",
        "Hanging grip training",
        "grip_training",
        "PREFERRED",
        10,
        (),
    ),
    (
        "grip_training",
        "free_weights",
        "Loaded grip training",
        "strength_gym",
        "SUBSTITUTE",
        20,
        ("Does not reproduce hanging or obstacle grips.",),
    ),
    (
        "obstacle_practice",
        "obstacles",
        "Obstacle practice",
        "obstacle_practice",
        "PREFERRED",
        10,
        (),
    ),
    (
        "obstacle_practice",
        "gym",
        "Gym obstacle preparation",
        "functional_fitness",
        "SUBSTITUTE",
        20,
        ("Does not reproduce complete obstacle technique.",),
    ),
    ("hiking_trail", "trail", "Trail hiking", "hiking_trail", "PREFERRED", 10, ()),
)

# target context, option code, capability code, importance
OPTION_CAPABILITIES = (
    ("running_road", "outdoor_road", "running_shoes", "REQUIRED"),
    ("running_road", "treadmill", "running_shoes", "REQUIRED"),
    ("running_road", "treadmill", "treadmill_access", "REQUIRED"),
    ("running_trail", "trail", "trail_running_shoes", "REQUIRED"),
    ("running_trail", "road", "running_shoes", "REQUIRED"),
    ("running_trail", "treadmill", "running_shoes", "REQUIRED"),
    ("running_trail", "treadmill", "treadmill_access", "REQUIRED"),
    ("running_treadmill", "treadmill", "running_shoes", "REQUIRED"),
    ("running_treadmill", "treadmill", "treadmill_access", "REQUIRED"),
    *(
        ("cycling_road", option, bike, "REQUIRED")
        for option, bike in (
            ("road_bike", "road_bike"),
            ("mountain_bike", "mountain_bike"),
            ("stationary_bike", "stationary_bike"),
        )
    ),
    *(
        ("cycling_road", option, "helmet", "REQUIRED")
        for option in ("road_bike", "mountain_bike")
    ),
    *(
        ("cycling_mountain", option, bike, "REQUIRED")
        for option, bike in (
            ("mountain_bike", "mountain_bike"),
            ("road_bike", "road_bike"),
            ("stationary_bike", "stationary_bike"),
        )
    ),
    *(
        ("cycling_mountain", option, "helmet", "REQUIRED")
        for option in ("mountain_bike", "road_bike")
    ),
    ("cycling_stationary", "stationary_bike", "stationary_bike", "REQUIRED"),
    ("swimming_pool", "pool", "pool_access", "REQUIRED"),
    ("swimming_pool", "pool", "goggles", "RECOMMENDED"),
    ("swimming_open_water", "open_water", "open_water_access", "REQUIRED"),
    ("swimming_open_water", "open_water", "goggles", "REQUIRED"),
    ("swimming_open_water", "open_water", "wetsuit", "OPTIONAL"),
    ("swimming_open_water", "pool", "pool_access", "REQUIRED"),
    ("swimming_open_water", "pool", "goggles", "RECOMMENDED"),
    ("strength_general", "gym", "gym_access", "REQUIRED"),
    ("strength_general", "gym", "free_weights", "RECOMMENDED"),
    ("strength_general", "home", "home_training_space", "REQUIRED"),
    ("strength_general", "home", "resistance_bands", "RECOMMENDED"),
    ("strength_gym", "gym", "gym_access", "REQUIRED"),
    ("strength_home", "home", "home_training_space", "REQUIRED"),
    ("functional_fitness", "gym", "gym_access", "REQUIRED"),
    ("functional_fitness", "gym", "free_weights", "RECOMMENDED"),
    ("functional_fitness", "home", "home_training_space", "REQUIRED"),
    ("functional_fitness", "home", "resistance_bands", "RECOMMENDED"),
    ("grip_training", "pull_up_bar", "pull_up_bar", "REQUIRED"),
    ("grip_training", "free_weights", "free_weights", "REQUIRED"),
    ("obstacle_practice", "obstacles", "obstacle_access", "REQUIRED"),
    ("obstacle_practice", "gym", "gym_access", "REQUIRED"),
    ("obstacle_practice", "gym", "pull_up_bar", "RECOMMENDED"),
    ("hiking_trail", "trail", "hiking_shoes", "REQUIRED"),
)
