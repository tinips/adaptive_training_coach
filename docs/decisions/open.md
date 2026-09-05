# Open Decisions

Still to resolve. Do not resolve these unilaterally — they need explicit sign-off before implementation.

## From the first-week evaluator milestone (new)

1. **Evaluator trigger:** should evaluation be triggered manually by an "Evaluate week" button, automatically at week-end, or both? (Carried over from the earlier entry below — restated here because it's now a concrete blocker for the first-week evaluator, not a someday question.)
2. **Completion-rate denominator with intentional cancellations:** how should completion rate treat a planned session the athlete intentionally cancelled (as opposed to simply not doing)? Does it count toward the denominator at all, count as a third category alongside `MISSED`/`EXTRA`, or need its own status?
3. **Timing of explicit linking:** should workout-to-planned-session linking happen immediately after screenshot import (as part of that flow), or as a separate step the athlete does later (e.g. from a weekly review)?
4. **`last_week_feedback` field list:** what exact fields does this contract need? This blocks both the fitness-state snapshot design (`docs/design/fitness-state.md`) and the ongoing weekly planner's future consumption of it (`docs/design/planner-architecture.md`, Layer 3).
5. **Fitness snapshot seeding:** should the first fitness snapshot be created only after the first-week evaluation runs, or seeded at onboarding from the self-reported baseline and then updated by evaluation?
6. **Reconciliation between the locked first-week matching decision and the already-built ongoing-planner comparator:** `docs/decisions/locked.md` locks explicit athlete-linking (Option A) as the first-week evaluator's matching approach. The ongoing planner's `compare_week()` (`backend/app/services/weekly_planning/comparison.py`, already `BUILT` and tested) uses a different, already-shipped mechanism: greedy nearest-same-discipline-date matching. Two sub-questions: (a) is this divergence intentional and permanent (first-week and ongoing weeks are different enough contexts to warrant different matching strategies), or should the ongoing planner eventually move to explicit linking too? (b) if first-week outcomes end up reusing the `weekly_plan_outcomes` table, does its schema need to accommodate both matching strategies' output shapes, or does first-week need a distinct table? This was surfaced during doc verification, not decided.
7. **First-week outcome persistence shape:** does a first-week weekly outcome reuse `weekly_plan_outcomes` (extending its `comparison_jsonb` shape), or does it need a distinct table given the different matching mechanism? Related to question 6.

## Carried over from planner-architecture design

- Phase proportions per event type (marathon vs sprint vs long-course) — start with sensible defaults, tune later.
- CTL activation gates (weeks of data + adherence floor + valid thresholds) — exact thresholds.
- Slack budgets for adaptation tiers (how much a phase can absorb before escalating).

## Deferred, not urgent

- DB cleanup: targeted (Apple Health remnants) vs broad audit.
