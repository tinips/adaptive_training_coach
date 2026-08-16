"""Semantic use-case data for the real new-goal catalog expansion flow.

The cases are evaluation data, not production canonicalization rules.  The
structured provider double below lets the test exercise the actual LangGraph,
onboarding service, publication service, and equipment-review boundaries while
keeping the semantic expected output deterministic and auditable.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContextCase:
    code: str
    decision: str
    discipline: str
    role: str = "TARGET"
    capability_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CapabilityCase:
    code: str
    decision: str
    kind: str


@dataclass(frozen=True, slots=True)
class SemanticGoalCase:
    case_id: str
    user_text: str
    goal_code: str
    goal_existing: bool
    contexts: tuple[ContextCase, ...]
    capabilities: tuple[CapabilityCase, ...]
    preload_contexts: tuple[str, ...] = ()
    supporting_goal_code: str | None = None

    @property
    def reused_contexts(self) -> tuple[str, ...]:
        return tuple(
            item.code for item in self.contexts if item.decision == "USE_EXISTING"
        )

    @property
    def created_contexts(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.contexts if item.decision == "CREATE")

    @property
    def reused_capabilities(self) -> tuple[str, ...]:
        return tuple(
            item.code for item in self.capabilities if item.decision == "USE_EXISTING"
        )

    @property
    def created_capabilities(self) -> tuple[str, ...]:
        return tuple(
            item.code for item in self.capabilities if item.decision == "CREATE"
        )


def _context(
    code: str,
    discipline: str,
    capabilities: tuple[str, ...],
    *,
    decision: str = "USE_EXISTING",
    role: str = "TARGET",
) -> ContextCase:
    return ContextCase(
        code=code,
        decision=decision,
        discipline=discipline,
        role=role,
        capability_codes=capabilities,
    )


def _capabilities(
    *items: tuple[str, str, str],
) -> tuple[CapabilityCase, ...]:
    return tuple(CapabilityCase(*item) for item in items)


RUNNING_ROAD = _context("running_road", "RUNNING", ("running_shoes",))
RUNNING_TRAIL = _context("running_trail", "RUNNING", ("trail_running_shoes",))
ROAD_CYCLING = _context("cycling_road", "CYCLING", ("road_bike", "helmet"))
MOUNTAIN_BIKING = _context("cycling_mountain", "CYCLING", ("mountain_bike", "helmet"))
POOL_SWIMMING = _context("swimming_pool", "SWIMMING", ("pool_access", "goggles"))
OPEN_WATER_SWIMMING = _context(
    "swimming_open_water", "SWIMMING", ("open_water_access", "goggles")
)
HIKING = _context("hiking_trail", "HIKING", ("hiking_shoes",))
GENERAL_STRENGTH = _context(
    "strength_general", "STRENGTH", ("gym_access", "free_weights")
)


_HYROX_CONTEXTS = (
    RUNNING_ROAD,
    _context(
        "hyrox_ski_erg",
        "OTHER",
        ("gym_access", "ski_ergometer"),
    ),
    _context(
        "hyrox_sled_push_pull",
        "STRENGTH",
        ("gym_access", "sled_push_pull_equipment"),
    ),
    _context(
        "hyrox_burpee_broad_jump",
        "OTHER",
        ("gym_access", "burpee_broad_jump_space"),
    ),
    _context(
        "hyrox_row",
        "OTHER",
        ("gym_access", "rowing_ergometer"),
    ),
    _context(
        "hyrox_farmer_carry",
        "STRENGTH",
        ("gym_access", "farmer_carry_weights"),
    ),
    _context(
        "hyrox_sandbag_lunge",
        "STRENGTH",
        ("gym_access", "sandbag"),
    ),
    _context(
        "hyrox_wall_balls",
        "STRENGTH",
        ("gym_access", "wall_ball"),
    ),
)


SEMANTIC_GOAL_CASES = (
    # Running: event distance is a goal attribute; terrain is the meaningful
    # context distinction.
    SemanticGoalCase(
        "running_marathon",
        "I want to run a marathon.",
        "MARATHON",
        False,
        (RUNNING_ROAD,),
        _capabilities(("running_shoes", "USE_EXISTING", "EQUIPMENT")),
    ),
    SemanticGoalCase(
        "running_half_marathon",
        "I want to train for a half marathon.",
        "HALF_MARATHON",
        False,
        (RUNNING_ROAD,),
        _capabilities(("running_shoes", "USE_EXISTING", "EQUIPMENT")),
    ),
    SemanticGoalCase(
        "running_10k",
        "I want to prepare for a 10K road race.",
        "RUNNING_10K",
        False,
        (RUNNING_ROAD,),
        _capabilities(("running_shoes", "USE_EXISTING", "EQUIPMENT")),
    ),
    SemanticGoalCase(
        "running_trail_marathon",
        "I want to do a trail marathon.",
        "TRAIL_RACE",
        False,
        (RUNNING_TRAIL,),
        _capabilities(("trail_running_shoes", "USE_EXISTING", "EQUIPMENT")),
    ),
    # Cycling: event format and distance do not create sport-specific contexts.
    SemanticGoalCase(
        "road_cycling_race",
        "I want to train for a road cycling race.",
        "ROAD_CYCLING_EVENT",
        False,
        (ROAD_CYCLING,),
        _capabilities(
            ("road_bike", "USE_EXISTING", "EQUIPMENT"),
            ("helmet", "USE_EXISTING", "EQUIPMENT"),
        ),
    ),
    SemanticGoalCase(
        "gran_fondo",
        "I want to prepare for a gran fondo.",
        "ROAD_CYCLING_EVENT",
        False,
        (ROAD_CYCLING,),
        _capabilities(
            ("road_bike", "USE_EXISTING", "EQUIPMENT"),
            ("helmet", "USE_EXISTING", "EQUIPMENT"),
        ),
    ),
    SemanticGoalCase(
        "road_cycling_100k",
        "I want to race a 100 km road cycling event.",
        "ROAD_CYCLING_EVENT",
        False,
        (ROAD_CYCLING,),
        _capabilities(
            ("road_bike", "USE_EXISTING", "EQUIPMENT"),
            ("helmet", "USE_EXISTING", "EQUIPMENT"),
        ),
    ),
    SemanticGoalCase(
        "mountain_bike_race",
        "I want to train for a mountain bike race.",
        "MTB_RACE",
        False,
        (MOUNTAIN_BIKING,),
        _capabilities(
            ("mountain_bike", "USE_EXISTING", "EQUIPMENT"),
            ("helmet", "USE_EXISTING", "EQUIPMENT"),
        ),
    ),
    SemanticGoalCase(
        "mtb_marathon",
        "I want to prepare for an MTB marathon.",
        "MTB_RACE",
        False,
        (MOUNTAIN_BIKING,),
        _capabilities(
            ("mountain_bike", "USE_EXISTING", "EQUIPMENT"),
            ("helmet", "USE_EXISTING", "EQUIPMENT"),
        ),
    ),
    # Triathlon: the event is a composition of the three sport contexts.
    SemanticGoalCase(
        "ironman",
        "I want to train for an Ironman.",
        "TRIATHLON_FULL_DISTANCE",
        False,
        (RUNNING_ROAD, ROAD_CYCLING, OPEN_WATER_SWIMMING),
        _capabilities(
            ("running_shoes", "USE_EXISTING", "EQUIPMENT"),
            ("road_bike", "USE_EXISTING", "EQUIPMENT"),
            ("helmet", "USE_EXISTING", "EQUIPMENT"),
            ("open_water_access", "USE_EXISTING", "ACCESS"),
            ("goggles", "USE_EXISTING", "EQUIPMENT"),
        ),
    ),
    SemanticGoalCase(
        "half_ironman",
        "I want to train for a half Ironman.",
        "TRIATHLON_HALF_DISTANCE",
        False,
        (RUNNING_ROAD, ROAD_CYCLING, OPEN_WATER_SWIMMING),
        _capabilities(
            ("running_shoes", "USE_EXISTING", "EQUIPMENT"),
            ("road_bike", "USE_EXISTING", "EQUIPMENT"),
            ("helmet", "USE_EXISTING", "EQUIPMENT"),
            ("open_water_access", "USE_EXISTING", "ACCESS"),
            ("goggles", "USE_EXISTING", "EQUIPMENT"),
        ),
    ),
    SemanticGoalCase(
        "olympic_triathlon",
        "I want to prepare for an Olympic-distance triathlon.",
        "TRIATHLON_OLYMPIC",
        False,
        (RUNNING_ROAD, ROAD_CYCLING, OPEN_WATER_SWIMMING),
        _capabilities(
            ("running_shoes", "USE_EXISTING", "EQUIPMENT"),
            ("road_bike", "USE_EXISTING", "EQUIPMENT"),
            ("helmet", "USE_EXISTING", "EQUIPMENT"),
            ("open_water_access", "USE_EXISTING", "ACCESS"),
            ("goggles", "USE_EXISTING", "EQUIPMENT"),
        ),
    ),
    # Swimming: environment is the modality distinction.
    SemanticGoalCase(
        "pool_swimming",
        "I want to train for a 1500m pool swimming race.",
        "POOL_SWIMMING_EVENT",
        False,
        (POOL_SWIMMING,),
        _capabilities(
            ("pool_access", "USE_EXISTING", "ACCESS"),
            ("goggles", "USE_EXISTING", "EQUIPMENT"),
        ),
    ),
    SemanticGoalCase(
        "open_water_swimming",
        "I want to prepare for an open-water swimming race.",
        "OPEN_WATER_SWIM",
        False,
        (OPEN_WATER_SWIMMING,),
        _capabilities(
            ("open_water_access", "USE_EXISTING", "ACCESS"),
            ("goggles", "USE_EXISTING", "EQUIPMENT"),
        ),
    ),
    SemanticGoalCase(
        "lake_swimming",
        "I want to swim a 5 km lake race.",
        "OPEN_WATER_SWIM",
        False,
        (OPEN_WATER_SWIMMING,),
        _capabilities(
            ("open_water_access", "USE_EXISTING", "ACCESS"),
            ("goggles", "USE_EXISTING", "EQUIPMENT"),
        ),
    ),
    # HYROX: running is reused, while each materially distinct station is
    # represented independently rather than hidden under functional fitness.
    *(
        SemanticGoalCase(
            f"hyrox_{suffix}",
            text,
            "HYROX",
            False,
            _HYROX_CONTEXTS,
            _capabilities(
                ("running_shoes", "USE_EXISTING", "EQUIPMENT"),
                ("gym_access", "USE_EXISTING", "FACILITY"),
                ("ski_ergometer", "USE_EXISTING", "EQUIPMENT"),
                ("sled_push_pull_equipment", "USE_EXISTING", "EQUIPMENT"),
                ("burpee_broad_jump_space", "USE_EXISTING", "FACILITY"),
                ("rowing_ergometer", "USE_EXISTING", "EQUIPMENT"),
                ("farmer_carry_weights", "USE_EXISTING", "EQUIPMENT"),
                ("sandbag", "USE_EXISTING", "EQUIPMENT"),
                ("wall_ball", "USE_EXISTING", "EQUIPMENT"),
            ),
        )
        for suffix, text in (
            ("race", "I want to train for HYROX."),
            ("prepare", "I want to prepare for a HYROX race."),
            ("doubles", "I want to compete in HYROX doubles."),
        )
    ),
    # Rowing: the context is rowing; indoor/water execution belongs in the
    # capability/environment layer.
    SemanticGoalCase(
        "rowing_race",
        "I want to train for a rowing race.",
        "ROWING_RACE",
        False,
        (_context("rowing", "OTHER", ("rowing_machine",), decision="CREATE"),),
        _capabilities(("rowing_machine", "CREATE", "EQUIPMENT")),
    ),
    SemanticGoalCase(
        "rowing_regatta",
        "I want to compete in a 2000m rowing regatta.",
        "ROWING_REGATTA",
        False,
        (
            _context(
                "rowing",
                "OTHER",
                ("rowing_boat", "water_access"),
                decision="CREATE",
            ),
        ),
        _capabilities(
            ("rowing_boat", "CREATE", "EQUIPMENT"),
            ("water_access", "CREATE", "ACCESS"),
        ),
    ),
    SemanticGoalCase(
        "indoor_rowing",
        "I want to prepare for an indoor rowing competition.",
        "INDOOR_ROWING",
        False,
        (_context("rowing", "OTHER", ("rowing_machine",)),),
        _capabilities(("rowing_machine", "CREATE", "EQUIPMENT")),
        preload_contexts=("rowing",),
    ),
    SemanticGoalCase(
        "water_rowing",
        "I want to train rowing on the water.",
        "WATER_ROWING",
        False,
        (
            _context(
                "rowing",
                "OTHER",
                ("rowing_boat", "water_access"),
                decision="CREATE",
            ),
        ),
        _capabilities(
            ("rowing_boat", "CREATE", "EQUIPMENT"),
            ("water_access", "CREATE", "ACCESS"),
        ),
    ),
    # A genuinely new water sport must not be replaced by a generic water or
    # endurance context.
    SemanticGoalCase(
        "rafting_race",
        "I want to train for a rafting race.",
        "RAFTING_RACE",
        False,
        (
            _context(
                "rafting_whitewater",
                "OTHER",
                ("whitewater_access", "raft", "paddle"),
                decision="CREATE",
            ),
        ),
        _capabilities(
            ("whitewater_access", "CREATE", "ACCESS"),
            ("raft", "CREATE", "EQUIPMENT"),
            ("paddle", "CREATE", "EQUIPMENT"),
        ),
    ),
    SemanticGoalCase(
        "whitewater_rafting",
        "I want to prepare for a white-water rafting competition.",
        "WHITEWATER_RAFTING",
        False,
        (
            _context(
                "rafting_whitewater",
                "OTHER",
                ("whitewater_access", "raft", "paddle"),
                decision="CREATE",
            ),
        ),
        _capabilities(
            ("whitewater_access", "CREATE", "ACCESS"),
            ("raft", "CREATE", "EQUIPMENT"),
            ("paddle", "CREATE", "EQUIPMENT"),
        ),
    ),
    # Hiking and strength: event wording does not create event-specific
    # contexts; powerlifting is materially distinct from general strength.
    SemanticGoalCase(
        "mountain_hiking",
        "I want to prepare for a mountain hiking challenge.",
        "GENERAL_HIKING",
        False,
        (HIKING,),
        _capabilities(("hiking_shoes", "USE_EXISTING", "EQUIPMENT")),
    ),
    SemanticGoalCase(
        "long_distance_hiking",
        "I want to train for a long-distance hiking event.",
        "GENERAL_HIKING",
        False,
        (HIKING,),
        _capabilities(("hiking_shoes", "USE_EXISTING", "EQUIPMENT")),
    ),
    SemanticGoalCase(
        "general_strength",
        "I want to get stronger for general strength training.",
        "GENERAL_STRENGTH",
        False,
        (GENERAL_STRENGTH,),
        _capabilities(
            ("gym_access", "USE_EXISTING", "FACILITY"),
            ("free_weights", "USE_EXISTING", "EQUIPMENT"),
        ),
    ),
    SemanticGoalCase(
        "powerlifting",
        "I want to prepare for a powerlifting competition.",
        "POWERLIFTING",
        False,
        (
            _context(
                "strength_powerlifting",
                "STRENGTH",
                ("gym_access", "free_weights"),
                decision="CREATE",
            ),
        ),
        _capabilities(
            ("gym_access", "USE_EXISTING", "FACILITY"),
            ("free_weights", "USE_EXISTING", "EQUIPMENT"),
        ),
    ),
    SemanticGoalCase(
        "triathlon_supporting_strength",
        "I want to continue my Ironman goal while maintaining strength.",
        "TRIATHLON_FULL_DISTANCE",
        True,
        (GENERAL_STRENGTH,),
        _capabilities(
            ("gym_access", "USE_EXISTING", "FACILITY"),
            ("free_weights", "USE_EXISTING", "EQUIPMENT"),
        ),
        supporting_goal_code="STRENGTH_MAINTENANCE",
    ),
    # Existing-goal control: no catalog expansion call is expected.
    SemanticGoalCase(
        "existing_hyrox",
        "I want to continue my HYROX goal.",
        "HYROX",
        True,
        _HYROX_CONTEXTS,
        _capabilities(
            ("running_shoes", "USE_EXISTING", "EQUIPMENT"),
            ("gym_access", "USE_EXISTING", "FACILITY"),
            ("ski_ergometer", "USE_EXISTING", "EQUIPMENT"),
            ("sled_push_pull_equipment", "USE_EXISTING", "EQUIPMENT"),
            ("burpee_broad_jump_space", "USE_EXISTING", "FACILITY"),
            ("rowing_ergometer", "USE_EXISTING", "EQUIPMENT"),
            ("farmer_carry_weights", "USE_EXISTING", "EQUIPMENT"),
            ("sandbag", "USE_EXISTING", "EQUIPMENT"),
            ("wall_ball", "USE_EXISTING", "EQUIPMENT"),
        ),
    ),
)
