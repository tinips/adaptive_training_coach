# Adaptive Training Coach Documentation

This directory is the canonical documentation set for the next architecture and
implementation stages. Code remains the source of truth for `BUILT` behavior.

## Start here

**What does the bot do today?** After onboarding, it generates one first-week
probe menu (unscheduled sessions the athlete places on their own days), the
athlete can then log workouts by screenshot or TCX import. The production
flow stops there: no session link, no evaluation, no fitness-state update, no
automatic transition to ongoing planning.

**What is fully working in production?** Everything tagged
`BUILT, PRODUCTION-WIRED` in the table below: first-week menu generation
end to end, and workout capture.

**What is implemented but not wired into the bot?** Everything tagged
`BUILT, DORMANT`: the ongoing (dated) weekly planner and the dated-plan
comparator both run and are tested, but nothing in `backend/app/bot/main.py`
calls them.

**What is only designed?** Everything tagged `DESIGNED`, `PROPOSED`, or
`OPEN DECISION`: the first-week evaluator, fitness-state history, the General
and Stage Planners, and CTL/TSS-driven planning. None of these exist in code.

**What should I read next?** See [Canonical documents and reading
order](#canonical-documents-and-reading-order) below; five to seven documents,
in order.

**What is the single next implementation?** Get the E6/E7 evaluator tables in
[Open decisions](decisions/open.md) approved (a product decision, not
engineering work), then execute
[First-week evaluator](briefs/backlog/first-week-evaluator.md). See the
[roadmap](roadmap.md) for the full build order.

## Status vocabulary

- `BUILT` — code exists. Always paired with exactly one reachability tag:
  - `PRODUCTION-WIRED` — the live bot/API actually calls this code on an
    athlete's path today.
  - `DORMANT` — the code runs and is tested, but nothing in the live bot/API
    calls it yet.
- `DESIGNED` — target behavior is agreed, but implementation does not exist.
- `PROPOSED` — a recommended design that still needs acceptance.
- `OPEN DECISION` — implementation would encode product behavior that requires
  approval.

A class, table, migration, or test existing in isolation does not make a
feature production-reachable — check for `PRODUCTION-WIRED` specifically, not
just `BUILT`.

## Current project status

| Capability | Status |
|---|---|
| First-week menu generation, validation, fallback, persistence, and Telegram rendering | `BUILT, PRODUCTION-WIRED` |
| Screenshot workout capture | `BUILT, PRODUCTION-WIRED` (disabled by default) |
| TCX workout capture | `BUILT, PRODUCTION-WIRED` (enabled by default) |
| Ongoing dated weekly planning (`OngoingWeeklyPlanner`) | `BUILT, DORMANT` — exists as service code, nothing in the bot instantiates it |
| Dated-plan comparison and `weekly_plan_outcomes` persistence (`compare_week()`/`compare_finished_week()`) | `BUILT, DORMANT` — no production caller, no Telegram command exposes it |
| No-HR weekly prescription invariant | `BUILT, PRODUCTION-WIRED`; completed-workout HR remains valid evidence |
| First-week repair-loop reconstruction is crash-safe for every discipline | `BUILT, PRODUCTION-WIRED`; fixed 2026-09-07, see [Locked decisions](decisions/locked.md) |
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

1. [Roadmap](roadmap.md) — current status and incremental build order; start
   with its "Build order (start here)" section.
2. [Locked decisions](decisions/locked.md) — accepted behavior, with its current
   implementation status.
3. [Open decisions](decisions/open.md) — approval gates and recommended options;
   the only decisions currently blocking work.
4. [First-week planner](design/first-week-planner.md) — the production-wired
   first-week generation path.
5. [First-week evaluator](design/first-week-evaluator.md) — explicit matching,
   per-session comparisons, weekly aggregation, and feedback handoff; the
   active next milestone.
6. [Fitness state](design/fitness-state.md) — baseline, observed state, history,
   trends, confidence, and storage alternatives.
7. [Planner architecture](design/planner-architecture.md) — General, Stage, and
   Weekly Planner responsibilities and adaptation boundaries.

[Load model](design/load-model.md) covers qualitative, emerging, and future
CTL/TSS-driven planning modes; read it when that milestone becomes active, not
before. Each design document owns the behavior and vocabulary for its topic.
The roadmap references those designs rather than duplicating their complete
rules.

## Active milestone and implementation briefs

The active implementation milestone is the first-week evaluator. Its next
brief is [First-week evaluator](briefs/backlog/first-week-evaluator.md). The HR
schema prerequisite is complete, including the first-week repair-loop
reconstruction fix; evaluator work is intentionally gated only by the proposed
E6 numerical tolerances and E7 signal/coverage thresholds in
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

## Archive candidates

Not deleted by this task; flagged here for a separately approved documentation
cleanup.

- `docs/CLAUDE.md` — the historical sprint prompt plus a status appendix. It is
  not a canonical design. Once its status appendix is no longer useful,
  `PROPOSED`: archive it outside the canonical set or delete it in a
  separately approved cleanup.
- `docs/brainstorm/` — empty except for a `.gitkeep`. Nothing to archive;
  remove the placeholder or leave it, either is harmless.
- Repository-root `CLAUDE.md` and `STATE.md` — contain known stale
  implementation claims about fitness snapshots/evaluation and Apple Health
  import (for example, `CLAUDE.md`'s data-model section names
  `athlete_fitness_snapshots` as if it already exists). They are outside the
  authorized `docs/` edit boundary and require a separate, explicitly approved
  update; current implementation status in this directory is code-audited and
  supersedes them.
- No design or brief in the canonical list (the ten documents named at the top
  of this index) is currently obsolete enough to archive. Repeated status
  summaries have been reduced to links and concise matrices where practical.

## Known current-code inconsistencies and risks

These are code observations, not evidence that the proposed architecture is
built.

- `backend/app/bot/messages.py` still tells athletes in the live `HELP` response
  that no training plan is generated, while `FirstWeekPlanner` is
  production-wired. The user-facing copy needs a separate code change.
- `ReferenceHeartRateZones.caveat` says observed workout HR will refine the
  estimate "over time," but no zone-refinement service or fitness-state history
  exists. Treat the statement as future intent until implemented.
- The current TCX-only training-file service still uses Apple-Health-named job,
  status, and repository types. This is naming/cleanup debt, not a live Apple
  Health ZIP adapter.
- `fitness_window_days` defaults to 14 but is unused by application code; weekly
  planning actually supplies `planner_window_days`, default 30, to a calculator
  whose module description still says "14-day." Future state code must use
  explicit evidence bounds.
- The dormant dated comparator queries UTC-midnight week bounds even though it
  derives the week from the athlete's local date. Do not copy that boundary
  behavior into first-week eligibility without the timezone decision and tests.

## Maintenance rule

Move a capability to `BUILT` only after checking both code and production
composition; tag it `PRODUCTION-WIRED` or `DORMANT`, never `BUILT` alone. When
an open decision is approved, move it from `open.md` to `locked.md`, update the
owning design, and only then implement the selected behavior. After
implementation, update the corresponding brief, roadmap, and this index from
verified code.
