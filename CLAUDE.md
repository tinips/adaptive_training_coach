# Adaptive Training Coach — Project Instructions

## What this is

An AI endurance-training coach. Telegram bot, Python/FastAPI backend, Postgres, DeepSeek V4 Flash. Generates and adapts personalized training plans for endurance athletes (running, cycling, swimming, triathlon, + strength as support). Supports any endurance goal (marathon, single-sport, or triathlon) — the design is discipline-agnostic.

## Core architecture principle (the most important rule)

**LLM proposes, deterministic code disposes.**

- Safety, correctness, structure, and math live in CODE (validators, schemas, resolvers) — NEVER in prompt prose.
- The LLM's job is narrow: session DESIGN and language/interpretation.
- Hard constraints → schema/types (make invalid states unrepresentable).
- Soft guidance → prompt prose.
- If a guarantee matters, enforce it in the schema or validator, not by asking the model nicely.

## Key domain rules (non-negotiable)

- **Prescribe pace/power, verify with HR.** Plans prescribe metrics the athlete controls and that come from their real data (power from FTP, pace from race results). Heart rate is NEVER prescribed — it's shown for reference and used by the evaluator to verify effort. This must hold in EVERY planner (first-week AND ongoing), enforced at resolver + validator level.
- **HR zones are age-estimated** (Tanaka 208−0.7×age) → treat as a soft/approximate reference, never a prescription target.
- **Strength is duration-only** — no sets/reps/loads (enforced at schema level; the fields don't exist on the strength target type).
- **Confidence-tiered load:** don't depend on CTL/TSS early (unreliable for new athletes). Plan qualitatively first (Mode A); activate CTL math (Mode C) only once enough reliable data exists (data-driven gate, not calendar).

## Planner hierarchy (three zoom levels)

The three layers are the locked **design target**; General and Stage Planner
milestones are not built yet:

1. **General Planner** — will cut goal→race into dated phases (Base→Build→
   Peak→Taper) with deterministic calendar math; the LLM will write phase
   objective text.
2. **Stage Planner** — will lay out one phase's week-by-week skeleton (load +
   focus + deload per week), not full workouts.
3. **Weekly Planner** — is built. First-week mode is production-wired;
   ongoing mode exists as a service but is not the composed production default.

Shared guards (no-HR, strength-duration-only, zone validity, availability) live
in the Weekly Planner harness for both of its modes — never re-implemented per
mode. A guarantee enforced in one mode must be enforced in the other.

## Data model (4 roles — not a table per view)

1. **Fitness state** — designed but not yet built; no fitness-history table or
   current-state reader exists. See `docs/briefs/backlog/fitness-state.md`.
2. **Plans** — what they were told to do (weekly_training_plans).
3. **Actuals** — what they did (workouts + discipline detail rows).
4. **Evaluations** — how plan versus actual went. The first-week deterministic
   core and its versioned outcome storage are built and unit-tested but dormant:
   no Telegram flow wires linking, evaluation, or outcome persistence for an
   athlete. `weekly_plan_outcomes` is an unused legacy dated-plan table.

"Current/last/overall progress" is a designed future read model over these
streams, not a currently implemented feature or separate table.

## Evaluator rules (locked decisions)

- These rules describe the built-but-dormant first-week evaluator core; no
  Telegram link/evaluate/review flow is production-wired yet.
- Matching: first-week athletes explicitly link each logged workout to a
  planned session — no date/greedy inference. The separately designed,
  post-first-week screenshot-matching mechanism is documented in
  `docs/decisions/locked.md`.
- Missed sessions remain visible in completion reporting and are excluded from
  physiological/capability math; adherence is `MATCHED / (MATCHED + MISSED)`.
- HR effort check: SOFT FLAG only (never auto-adjusts zones); matters as a pattern, not a one-off.
- Judge sessions by INTENT (intensity) first, volume second. "More distance at same easy HR" = good (fitness). "More distance via higher HR on an easy day" = flag (broke the plan's structure).
- Evaluator emits FACTS + a suggested signal; the planner makes the final call.
- overall_signal computed by a deterministic rule table (not an LLM call) — consistent with "code disposes" and the rate-limit constraint.

## Observability (required)

Every generated plan tagged generation_source = model / model_repaired / fallback, persisted, so quality is measurable. Fallbacks must be safe, labeled to the athlete, and never a silent failure.

## Working conventions

- **Deploy ≠ pass tests.** Passing tests means the code is right, not that it's running. Rebuild the container (docker compose up -d --build) and verify LIVE before trusting a change.
- **Migrations are append-only for captured data** — downgrades must refuse or preserve, never silently drop columns holding data not stored elsewhere.
- **Explore/report before large changes**; write tests first (TDD); one agent writes to main.
- **Don't over-scope.** Ship a solid v1. Defer features unless clearly needed. Flag when a request exceeds current scope.

## Rate-limit awareness

LLM/agent calls are limited. Design and specs happen in conversation (free); agent cycles are for execution. Prefer complete, self-contained briefs over live iteration. Avoid unnecessary LLM calls in the product (the evaluator's signal is deterministic for this reason).
