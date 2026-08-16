# Semantic catalog expansion report

This report records the evaluation dataset and the final result of running it
through the real goal extraction → context mapping → capability definition →
catalog publication → equipment review flow.

The provider used by the scenario test is a structured semantic fixture. It is
deliberately kept in test code so the cases are evaluation data, not production
HYROX/rowing rules. The production path still receives active-catalog snapshots
and structured model output through LangGraph.

## Execution evidence

Command:

```text
cd backend
pytest -q tests/scenarios/test_semantic_catalog_dataset.py
```

Result: **30 passed**.

Each case exercised the actual onboarding service. New-goal cases captured the
goal extraction output, mapping request, capability request, reconciliation,
persisted goal-context graph, persisted execution requirements, and equipment
review. Existing-goal controls asserted that mapping and capability expansion
were not called.

## Dataset and results

All final results are `PASS`; no semantic warning was accepted by the test.
`USE` and `CREATE` below are the reconciled canonical outcomes.

| # | Goal | Existing goal? | Contexts reused | Contexts created | Capabilities reused | Capabilities created | Result |
|---:|---|:---:|---|---|---|---|---|
| 1 | I want to run a marathon. | No | running_road | — | running_shoes | — | PASS |
| 2 | I want to train for a half marathon. | No | running_road | — | running_shoes | — | PASS |
| 3 | I want to prepare for a 10K road race. | No | running_road | — | running_shoes | — | PASS |
| 4 | I want to do a trail marathon. | No | — | running_trail | — | trail_running_shoes | PASS |
| 5 | I want to train for a road cycling race. | No | cycling_road | — | road_bike, helmet | — | PASS |
| 6 | I want to prepare for a gran fondo. | No | cycling_road | — | road_bike, helmet | — | PASS |
| 7 | I want to race a 100 km road cycling event. | No | cycling_road | — | road_bike, helmet | — | PASS |
| 8 | I want to train for a mountain bike race. | No | cycling_mountain | — | mountain_bike, helmet | — | PASS |
| 9 | I want to prepare for an MTB marathon. | No | cycling_mountain | — | mountain_bike, helmet | — | PASS |
| 10 | I want to train for an Ironman. | No | running_road, cycling_road, swimming_open_water | — | running_shoes, road_bike, helmet, open_water_access, goggles | — | PASS |
| 11 | I want to train for a half Ironman. | No | running_road, cycling_road, swimming_open_water | — | running_shoes, road_bike, helmet, open_water_access, goggles | — | PASS |
| 12 | I want to prepare for an Olympic-distance triathlon. | No | running_road, cycling_road, swimming_open_water | — | running_shoes, road_bike, helmet, open_water_access, goggles | — | PASS |
| 13 | I want to train for a 1500m pool swimming race. | No | swimming_pool | — | pool_access, goggles | — | PASS |
| 14 | I want to prepare for an open-water swimming race. | No | swimming_open_water | — | open_water_access, goggles | — | PASS |
| 15 | I want to swim a 5 km lake race. | No | swimming_open_water | — | open_water_access, goggles | — | PASS |
| 16 | I want to train for HYROX. | No | running_road, hyrox_ski_erg, hyrox_sled_push_pull, hyrox_burpee_broad_jump, hyrox_row, hyrox_farmer_carry, hyrox_sandbag_lunge, hyrox_wall_balls | — | running_shoes, gym_access, ski_ergometer, sled_push_pull_equipment, burpee_broad_jump_space, rowing_ergometer, farmer_carry_weights, sandbag, wall_ball | — | PASS |
| 17 | I want to prepare for a HYROX race. | No | running_road, hyrox_ski_erg, hyrox_sled_push_pull, hyrox_burpee_broad_jump, hyrox_row, hyrox_farmer_carry, hyrox_sandbag_lunge, hyrox_wall_balls | — | running_shoes, gym_access, ski_ergometer, sled_push_pull_equipment, burpee_broad_jump_space, rowing_ergometer, farmer_carry_weights, sandbag, wall_ball | — | PASS |
| 18 | I want to compete in HYROX doubles. | No | running_road, hyrox_ski_erg, hyrox_sled_push_pull, hyrox_burpee_broad_jump, hyrox_row, hyrox_farmer_carry, hyrox_sandbag_lunge, hyrox_wall_balls | — | running_shoes, gym_access, ski_ergometer, sled_push_pull_equipment, burpee_broad_jump_space, rowing_ergometer, farmer_carry_weights, sandbag, wall_ball | — | PASS |
| 19 | I want to train for a rowing race. | No | — | rowing | — | rowing_machine | PASS |
| 20 | I want to compete in a 2000m rowing regatta. | No | — | rowing | — | rowing_boat, water_access | PASS |
| 21 | I want to prepare for an indoor rowing competition. | No | rowing | — | — | rowing_machine | PASS |
| 22 | I want to train rowing on the water. | No | — | rowing | — | rowing_boat, water_access | PASS |
| 23 | I want to train for a rafting race. | No | — | rafting_whitewater | — | whitewater_access, raft, paddle | PASS |
| 24 | I want to prepare for a white-water rafting competition. | No | — | rafting_whitewater | — | whitewater_access, raft, paddle | PASS |
| 25 | I want to prepare for a mountain hiking challenge. | No | hiking_trail | — | hiking_shoes | — | PASS |
| 26 | I want to train for a long-distance hiking event. | No | hiking_trail | — | hiking_shoes | — | PASS |
| 27 | I want to get stronger for general strength training. | No | strength_general | — | gym_access, free_weights | — | PASS |
| 28 | I want to prepare for a powerlifting competition. | No | — | strength_powerlifting | gym_access, free_weights | — | PASS |
| 29 | I want to continue my Ironman goal while maintaining strength. | Yes | canonical triathlon + supporting strength graph | — | canonical catalog capabilities | — | PASS |
| 30 | I want to continue my HYROX goal. | Yes | canonical HYROX graph | — | canonical HYROX capabilities | — | PASS |

The rafting rows intentionally use a created context and created capability
proposals in the test fixture; this evaluates the semantic entity boundary
without adding any rafting-specific production rule.

## Initial failures and root causes

The initial targeted HYROX run exposed the important production failure:

- HYROX reused `running_road` and mapped the other demand to the generic
  `functional_fitness` context.
- Because no context was marked `CREATE`, capability expansion was skipped.
- Equipment review therefore saw only the generic canonical context
  requirements, not the goal-aware HYROX requirements.

The root cause was a flow boundary that treated “new contexts” as “created
contexts.” Capability definition must cover every context required by a new
goal, including reused contexts. A secondary deterministic-mock defect treated
known HYROX as reusable even when it was absent from the active catalog and
defaulted unmatched goals to running. The canonical seed is now complete, so
HYROX is a valid existing-goal definition when present; tests that simulate a
new HYROX goal remove only the goal row and retain the canonical contexts and
capabilities.

## Changes that fixed the failures

- The new-goal flow now sends every reconciled required context to capability
  expansion, while the existing-goal path still bypasses expansion.
- Publication validates and persists goal-aware definitions for every mapped
  context, including reused canonical contexts.
- The context prompt now requires materially distinct named stations/challenges
  to remain distinct and explicitly prevents a generic functional-fitness
  context from replacing them.
- The deterministic mock now uses the active catalog snapshot for reuse and
  produces a generic unmatched context without a sport-specific hard-coded
  branch.
- Migration `0026_complete_hyrox_catalog` replaces the generic HYROX link with
  the complete station graph and is idempotent for existing rows.
- The semantic scenario records and verifies the graph at each boundary,
  including equipment review.

## Final HYROX structure

Goal: `HYROX`.

- Reused running context: `running_road`, with `running_shoes`.
- Canonical challenge contexts: `hyrox_ski_erg`, `hyrox_sled_push_pull`,
  `hyrox_burpee_broad_jump`, `hyrox_row`, `hyrox_farmer_carry`,
  `hyrox_sandbag_lunge`, and `hyrox_wall_balls`.
- Canonical challenge capabilities: `ski_ergometer`,
  `sled_push_pull_equipment`, `burpee_broad_jump_space`,
  `rowing_ergometer`, `farmer_carry_weights`, `sandbag`, and `wall_ball`,
  with shared `gym_access` where required.
- A new goal reuses these canonical challenge rows; it does not regenerate or
  mutate them. The migration creates missing rows only when upgrading a
  database that still has the old generic HYROX definition.

This preserves running reuse while modeling the materially different race
challenges individually. It does not add a `functional_fitness` substitute.

## Final rowing structure

The canonical semantic boundary is a `rowing` training context. Indoor rowing
uses a `rowing_machine`; water rowing uses `rowing_boat` and `water_access`.
The machine, boat, and access are capabilities/execution requirements, not
contexts. No `functional_rowing`, `rowing_fitness`, or `rowing_equipment`
context/capability is introduced.

## Final rafting structure

The new modality is represented by `rafting_whitewater`, not by generic
`water_training`, `endurance`, or `functional_fitness`. The execution layer
can carry `whitewater_access`, `raft`, and `paddle` when the semantic provider
identifies them as required by the specific event.

## Artifacts

- Dataset: [semantic_catalog_dataset.py](../backend/tests/scenarios/semantic_catalog_dataset.py)
- Executable real-flow test: [test_semantic_catalog_dataset.py](../backend/tests/scenarios/test_semantic_catalog_dataset.py)
- HYROX focused end-to-end regression: [test_new_goal_catalog_e2e.py](../backend/tests/scenarios/test_new_goal_catalog_e2e.py)
