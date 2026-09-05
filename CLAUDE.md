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

1. **General Planner** — runs once; cuts goal→race into dated phases (Base→Build→ Peak→Taper). Deterministic (calendar math); LLM only writes phase objective text.
2. **Stage Planner** — lays out one phase's week-by-week skeleton (load + focus + deload per week). Deterministic progression rules. NOT full workouts.
3. **Weekly Planner** — generates the actual sessions for one week, filling the skeleton slot within the athlete's real constraints. This is where the LLM works.

Shared guards (no-HR, strength-duration-only, zone validity, availability) live in a COMMON harness both planners use — never re-implemented per planner. A guarantee enforced in one planner must be enforced in all.

## Data model (4 roles — not a table per view)

1. **Fitness state** — how fit now (athlete_fitness_snapshots, one row per week, append-only = the progress history).
2. **Plans** — what they were told to do (weekly_training_plans).
3. **Actuals** — what they did (workouts + discipline detail rows).
4. **Evaluations** — how plan vs actual went (weekly_plan_outcomes).

"Current/last/overall progress" = reads over these streams, NOT separate tables.

## Evaluator rules (locked decisions)

- Matching: athlete explicitly links each logged workout to a planned session (Option A) — no date/greedy inference.
- Missed sessions: DISCARDED, never penalized (drop out of completion rate).
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
