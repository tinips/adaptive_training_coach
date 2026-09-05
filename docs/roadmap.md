# Roadmap — Build Order

## Done

- First-week planner (complete, live, documented)
- Capture infrastructure: cycling power, screenshot extraction, derived pace/speed
- Age-based HR zones (verification/info only) + /zones command
- Unit formatting (min:sec pace)
- UI/UX pass (Astra branch)
- Code-review fixes: ongoing HR guard, migration downgrade safety, /zones routing, dead-branch cleanup (implemented and verified in the working tree; **not yet committed**)
- Ongoing weekly planner (dated sessions) + its plan-vs-actual comparison (`compare_week()`, `weekly_plan_outcomes`) — verified `BUILT` during this documentation pass; was previously undocumented as a separate mechanism from the first-week evaluator now being designed.

## Next: First-week evaluator (this milestone — design complete, implementation not started)

Recommended implementation order, per `docs/design/first-week-evaluator.md`, `docs/design/fitness-state.md`, and `docs/decisions/open.md`:

1. Stable planned-session identifiers — nothing below this line can be built without it; `PlanSession` currently has no per-session id.
2. Workout-to-planned-session linking (athlete-explicit, per the locked matching decision).
3. Per-session deterministic comparison (first-week-specific; the existing `compare_week()` does not apply — no dates to match).
4. Weekly aggregate and manual evaluation trigger — trigger mechanism (manual/automatic/both) is an open decision (`docs/decisions/open.md` #1) to resolve before or during this step.
5. Weekly outcome persistence — whether this reuses `weekly_plan_outcomes` or needs a distinct shape is open (`docs/decisions/open.md` #7) to resolve before or during this step.
6. Fitness-state snapshots/history (append-only, per the recommendation in `docs/design/fitness-state.md`).
7. `last_week_feedback` contract — exact field list is open (`docs/decisions/open.md` #4).

## Then: General planner (build top-down)

8. General planner phase generation — cut goal into dated phases (Base→Build→Peak→Taper). Deterministic calendar math + optionally-templated phase-objective wording. See `docs/design/planner-architecture.md`, Layer 1.
9. Wire the ongoing weekly planner to current phase and `last_week_feedback` (minimal, reuses the existing weekly planner) → goal-directed training end-to-end.
10. Stage planner skeleton — per-phase weekly skeleton (Mode A, qualitative). See `docs/design/planner-architecture.md`, Layer 2.

## Later (needs accumulated real data)

11. CTL/TSS activation (Mode C) and larger tiered adaptation — only after sufficient real data exists. Keep CTL, vacation/recovery handling, and phase re-cutting out of the immediate v1 implementation, per this milestone's non-goals.
- Aerobic-efficiency trend (same pace, lower HR over weeks) — a natural read over the fitness-state history once step 6 exists.

## Deferred

- Mini web-app (workout-history app covers strongest case for now).
- Environment-based metric selection (indoor watts vs outdoor speed/HR).
- Broad DB cleanup.
