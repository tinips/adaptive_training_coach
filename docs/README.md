# Adaptive Training Coach Documentation

This directory is the canonical documentation set for the next architecture and
implementation stages. Code remains the source of truth for `BUILT` behavior.

## Status vocabulary

- `BUILT` — code exists. Production reachability is stated separately.
- `DESIGNED` — target behavior is agreed, but implementation does not exist.
- `PROPOSED` — a recommended design that still needs acceptance.
- `OPEN DECISION` — implementation would encode product behavior that requires
  approval.

A class, table, migration, or test existing in isolation does not make a feature
production-reachable.

## Current project status

| Capability | Status |
|---|---|
| First-week menu generation, validation, fallback, persistence, and Telegram rendering | `BUILT` and production-wired |
| Screenshot workout capture | `BUILT`, production-wired, disabled by default |
| TCX workout capture | `BUILT`, production-wired, enabled by default |
| Ongoing dated weekly planning | `BUILT` service capability, not production-wired |
| Dated-plan comparison and `weekly_plan_outcomes` persistence | `BUILT` internal capability, no production trigger |
| No-HR weekly prescription invariant | `BUILT`; completed-workout HR remains valid evidence |
| Explicit first-week workout/session linking | `DESIGNED`, not implemented |
| First-week per-session and weekly evaluation | `DESIGNED`, not implemented |
| Fitness-state tracking/history | `PROPOSED`; storage choice deferred to its milestone |
| General Planner | `DESIGNED`, not implemented |
| Stage Planner | `DESIGNED`, not implemented |
| CTL/TSS-driven planning | `PROPOSED` later mode, not implemented |

After the current first-week plan is generated, the athlete can log workouts,
but the production flow stops there: no session link, first-week evaluation,
fitness-state update, feedback handoff, or automatic transition to ongoing
planning exists.

## Canonical documents and reading order

1. [Roadmap](roadmap.md) — current status and incremental build order.
2. [Locked decisions](decisions/locked.md) — accepted behavior, with its current
   implementation status.
3. [Open decisions](decisions/open.md) — approval gates and recommended options.
4. [First-week planner](design/first-week-planner.md) — the production-wired
   first-week generation path.
5. [First-week evaluator](design/first-week-evaluator.md) — explicit matching,
   per-session comparisons, weekly aggregation, and feedback handoff.
6. [Fitness state](design/fitness-state.md) — baseline, observed state, history,
   trends, confidence, and storage alternatives.
7. [Planner architecture](design/planner-architecture.md) — General, Stage, and
   Weekly Planner responsibilities and adaptation boundaries.
8. [Load model](design/load-model.md) — qualitative, emerging, and future
   CTL/TSS-driven planning modes.

Each design document owns the behavior and vocabulary for its topic. The
roadmap references those designs rather than duplicating their complete rules.

## Active milestone and implementation briefs

The active implementation milestone is the first-week evaluator. Its next
brief is [First-week evaluator](briefs/backlog/first-week-evaluator.md). The HR
schema prerequisite is complete; evaluator work is intentionally gated only by
the proposed E6 numerical tolerances and E7 signal/coverage thresholds in
`docs/decisions/open.md`.

Backlog briefs:

| Brief | Status | Depends on |
|---|---|---|
| [First-week evaluator](briefs/backlog/first-week-evaluator.md) | `DESIGNED`, not executed | Approval of E6 tolerances and E7 signal/coverage thresholds |
| [Fitness state](briefs/backlog/fitness-state.md) | `PROPOSED`, not executed | Evaluator outcome plus storage, seeding, correction, and confidence decisions |
| [General Planner phase foundation](briefs/backlog/general-planner-phase-foundation.md) | `PROPOSED`, not executed | Phase allocation, minimum-length, feasibility, and re-plan decisions |

Briefs describe implementation work, tests, and release checks. They do not
prove that the described functionality exists.

## Deterministic code, LLM, and product decisions

- Deterministic code owns calculations, calendar allocation, schemas,
  validation, safety checks, persistence, matching constraints, evaluation
  facts, and the eventual versioned signal table.
- The current LLM role is concrete weekly-session design and language inside
  deterministic constraints. The evaluator does not use an LLM. `PROPOSED` for
  General and Stage Planner v1: use deterministic templates, with no required
  LLM; their product policy remains deferred in the owning designs.
- Product decisions define meaning: signal thresholds, fitness-state storage
  and confidence, phase proportions,
  disruption budgets, and CTL/TSS activation gates.

## Historical and cleanup candidates

- `docs/CLAUDE.md` is the historical sprint prompt plus a status appendix. It is
  not a canonical design. After the status appendix is no longer useful,
  `PROPOSED`: archive it outside the canonical set or delete it in a separately
  approved documentation cleanup. It is not deleted by this sprint.
- No design or brief in the canonical list is currently obsolete enough to
  delete. Repeated status summaries have been reduced to links and concise
  matrices where practical.
- Repository-root `CLAUDE.md` and `STATE.md` contain known stale implementation
  claims about fitness snapshots/evaluation and Apple Health import. They are
  outside the authorized `docs/` edit boundary and require a separate update;
  current implementation status in this directory is code-audited.

## Known current-code inconsistencies and risks

These are code observations, not evidence that the proposed architecture is
built. They remain unchanged because this sprint is documentation-only.

- `backend/app/bot/messages.py` still tells athletes in the live `HELP` response
  that no training plan is generated, while `FirstWeekPlanner` is
  production-wired. The user-facing copy needs a separate code change.
- `ReferenceHeartRateZones.caveat` says observed workout HR will refine the
  estimate “over time,” but no zone-refinement service or fitness-state history
  exists. Treat the statement as future intent until implemented.
- The current TCX-only training-file service still uses Apple-Health-named job,
  status, and repository types. This is naming/cleanup debt, not a live Apple
  Health ZIP adapter.
- `fitness_window_days` defaults to 14 but is unused by application code; weekly
  planning actually supplies `planner_window_days`, default 30, to a calculator
  whose module description still says “14-day.” Future state code must use
  explicit evidence bounds.
- The dormant dated comparator queries UTC-midnight week bounds even though it
  derives the week from the athlete's local date. Do not copy that boundary
  behavior into first-week eligibility without the timezone decision and tests.

## Maintenance rule

Move a capability to `BUILT` only after checking both code and production
composition. When an open decision is approved, move it from `open.md` to
`locked.md`, update the owning design, and only then implement the selected
behavior. After implementation, update the corresponding brief, roadmap, and
this index from verified code.
