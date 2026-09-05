# The First-Week Planner — Complete Flow

## Purpose (what it's for)

It generates the athlete's **very first week** right after onboarding. Its job is **not** to train toward the race — it's a **baseline/probe week**: prescribe sensible volume + controlled intensity, gather signal about the athlete, and stay safe. Goal context (event date, finish time) is deliberately **excluded** so week 1 measures the athlete rather than chasing the goal.

---

## Step 1 — Assemble the input

The planner does **not** dump the whole onboarding blob at the LLM. `WeeklyPlanningService._prepare()` (`backend/app/services/weekly_planning/service.py:887-1121`) assembles a scoped `prompt_context` dict:

**Included** (exact keys built in `_prepare()`):

-   `planner_mode` — `"FIRST_WEEK"`
-   `week_start`
-   `athlete_profile` — `birth_year`, `sex_category`, `weight_kg`, `height_cm`, `timezone`
-   `planned_disciplines` — the target contexts resolved from the athlete's goal + supporting goal
-   `confirmed_availability` (JSON) + `availability_constraints` (plain-language, day-by-day)
-   `health_limitations`
-   `equipment_and_access` — capability code, display name, kind, from `AthleteCapabilityRepository`
-   `recent_evidence` — aggregated workout evidence per discipline (session/active-day counts, no raw workout data)
-   `self_reported_baseline` — the full structured baseline per discipline (sessions/wk, minutes/wk, longest session, FTP, race result, swim data)
-   `evidence_state` — `SELF_REPORTED` / `THIN` / `WELL_EVIDENCED` / `NONE` per discipline
-   `preferences` — `coaching_style` (`CONSERVATIVE` / `NORMAL` / `DEMANDING`, defaults to `"NORMAL"` if no baseline preferences exist), `desired_weekly_sessions`, `desired_sessions_fit_availability`
-   `per_discipline_target_minutes`, `untrained_disciplines`
-   `first_week_baseline_tiers` — one of `UNPREPARED` / `DEVELOPING` / `TRAINED` / `WELL_TRAINED` per discipline (see Step 3)
-   `resolved_intensity_zones` — the output of Step 2, one per discipline

**Deliberately excluded:** event date, goal distance, finish time, target paces-as-prescription, and any HR-zone data. The goal exclusion is enforced precisely at `service.py:1082-1088` — `prompt_context["goal"]` is only ever set `if self.planner_mode == "ONGOING"`; the `else` branch (`FIRST_WEEK`) sets `first_week_baseline_tiers` and `resolved_intensity_zones` instead and never touches `goal` at all. There is no goal key in the FIRST_WEEK payload to redact — it is structurally never assembled.

**Why it's good:** scoping the input keeps the probe week focused on *measurement*, prevents premature goal-chasing, and reduces prompt noise (which we saw matters — irrelevant fields dilute instruction-following).

---

## Step 2 — Resolve intensity zones (code, before the LLM)

`resolve_first_week_zones()` (`backend/app/services/weekly_planning/zones.py:33-86`) computes, per discipline, one `ResolvedIntensityZones` object — `mode` (`NUMERIC` or `RPE_FALLBACK`), `metric`, and `easy`/`moderate`/`hard` bounds. This function has exactly one caller in the whole codebase (`service.py:992`, the FIRST_WEEK-only branch of `_prepare()`), so it only ever affects the first-week probe, never the ongoing planner.

**`NUMERIC` — exact formulas, in priority order:**

1.  **Cycling with FTP** (`baseline.cycling.recent_ftp_watts`) → `POWER_WATTS`:
    -   easy = `(round(ftp × 0.55), round(ftp × 0.75))`
    -   moderate = `(round(ftp × 0.76), round(ftp × 0.90))`
    -   hard = `(round(ftp × 0.91), round(ftp × 1.05))`
    -   e.g. FTP 260 → easy 143-195 W, moderate 198-234 W, hard 237-273 W.
2.  **Running with a race result** (`baseline.running.recent_race_result`, pace = `duration_seconds / distance_km`) → `PACE_SECONDS_PER_KM`:
    -   easy = `(round(pace × 1.10), round(pace × 1.25))`
    -   moderate = `(round(pace × 0.98), round(pace × 1.09))`
    -   hard = `(round(pace × 0.88), round(pace × 0.97))`
    -   e.g. a 10k in 42:00 (pace 252 s/km) → easy 277-315 s/km, moderate 247-275 s/km, hard 222-244 s/km. Note pace zones are inverted relative to effort: "easy" is the *largest* seconds-per-km (slowest), "hard" the *smallest* (fastest).
3.  **Swimming with a 400 m time** (`baseline.swimming.recent_400m_seconds`, threshold pace = `seconds / 4`, i.e. per-100 m) → `SWIM_PACE_SECONDS_PER_100M`, same ×1.10/1.25, ×0.98/1.09, ×0.88/0.97 bands as running.

If none of the three thresholds exist for a discipline, the zone falls back to **`RPE_FALLBACK`** — `metric="RPE"`, no numeric bounds, with the guidance string "No usable threshold is available: prescribe and record effort by RPE and breathing/feel, not pace, power, or heart-rate targets." This is the *only* remaining tail of `_resolve_discipline()`; there is no other exit.

**Heart rate is never part of this resolution any more.** An earlier version of this function had a fourth, lower-priority branch that could select `HEART_RATE_BPM` from observed workout HR (`BaselineCalculation.reliable_max_hr_bpm`) — that branch has been deleted entirely (`backend/app/services/weekly_planning/zones.py`, see `_resolve_discipline`). `resolve_first_week_zones()` now has no code path that can return `metric="HEART_RATE_BPM"`, for any discipline, under any input.

**Age-based HR zones exist, but as a completely separate, display-only concept.** `backend/app/services/athlete_zones.py` computes `ReferenceHeartRateZones` — a distinct Pydantic type, never `ResolvedIntensityZones` — from the athlete's birth year via the **Tanaka formula**: `maxHR = 208 - 0.7 × age`, where `age = current_year - birth_year` (whole years only; only birth year is stored, not a full date of birth — this is itself an approximation layered on top of Tanaka's own stated ±10-12 bpm uncertainty, which the rendered output states explicitly as a caveat). The easy/moderate/hard bands are 60-75% / 76-85% / 86-92% of max HR, computed by `hr_zone_bands()` (`zones.py:124-139`) — the *same* percentage function the old observed-HR branch used, kept as a small shared pure helper so both concepts use identical banding math without duplicating the magic numbers, even though only the display path calls it now.

The isolation is structural, not just a naming convention: `app/services/athlete_zones.py` **imports from** `weekly_planning.zones` (for `hr_zone_bands`, `power_zones`, `running_pace_zones`, `swim_pace_zones`), and nothing under `app/services/weekly_planning/` or `app/workflows/prompts/` imports anything back from `athlete_zones.py`. `ReferenceHeartRateZones` never enters `prompt_context` and is never seen by the LLM or the validator that gates the first-week prescription.

There is a second, independent enforcement layer beyond the resolver itself: `_first_week_zone_violations()` (`validation.py:382-415`) rejects any session whose `intensity.metric` isn't `RPE` when its zone is `RPE_FALLBACK` — and after the fix above, HR is `RPE_FALLBACK` for every first-week discipline. So even a model that ignored the prompt and invented a heart-rate target would be caught by validation and repaired back to RPE-only (see Step 6-7) before it ever reached the athlete. The "no HR prescription" guarantee holds at the resolver, the prompt instruction, and the validator — three independent layers, not one.

**Why it's good:** intensity targets are **grounded in the athlete's real data, computed deterministically** — not hallucinated by the model. This was the fix that stopped the model from inventing paces. Numbers when we have them, feel when we don't — and heart rate is available to the athlete for their own reference, without ever becoming something the plan tells them to hit.

---

## Step 3 — Compute baseline tier (code)

`resolve_first_week_tiers()` → `_tier_for()` (`backend/app/services/weekly_planning/tiers.py:37-70`) classifies each discipline from stated volume + recent evidence, in this exact order:

1.  **`UNPREPARED`** — `typical_weekly_sessions == 0` **and** `typical_weekly_duration_minutes == 0` **and** the evidence-derived `session_count == 0`. (Zero self-reported volume and zero recent workouts.)
2.  **`WELL_TRAINED`** — evidence state is `WELL_EVIDENCED` **and** (`typical_weekly_sessions >= 4` **or** `typical_weekly_duration_minutes >= 240` **or** the discipline's longest recent session `>= 90` minutes).
3.  **`TRAINED`** — `typical_weekly_sessions >= 3` **or** `typical_weekly_duration_minutes >= 150` **or** longest recent session `>= 60` minutes **or** evidence state is `WELL_EVIDENCED`.
4.  Otherwise **`DEVELOPING`**.

("Longest recent session" reads `longest_recent_run_minutes` for running, `longest_recent_ride_minutes` for cycling; swimming has no longest-session field in this check.)

This tier — not the model's guess — drives *how demanding* the week is, and is also enforced deterministically by the validator (Step 6): `UNPREPARED` disciplines are hard-capped at RPE ≤ 4, and `DEVELOPING`/`TRAINED`/`WELL_TRAINED` disciplines with a numeric zone and ≥2 sessions are *required* to include at least one controlled-moderate (RPE ≥ 5) session — the tier isn't just prompt guidance, it's a checked invariant.

**Why it's good:** "fit vs. beginner" is decided by code from data, not left to the model to infer. This is what makes a trained athlete get moderate work while a beginner stays easy — and the validator won't let the model skip the moderate work a trained athlete is owed, or sneak it in for someone who isn't ready.

---

## Step 4 — The prompt (v8)

`FIRST_WEEK_PLANNER_PROMPT_VERSION = 8` (`backend/app/workflows/prompts/weekly_planning.py:15`; persisted per-plan as `prompt_version` on `weekly_training_plans`). A **probe/familiarization** system prompt (`_FIRST_WEEK_PLANNER_SYSTEM_PROMPT`) instructs the model to:

-   Design a menu (no scheduling — athlete places sessions themselves)
-   Treat `resolved_intensity_zones` as authoritative (numeric within bands, or RPE fallback — never invent pace/power/HR when a discipline is `RPE_FALLBACK`)
-   Scale demand by **tier + coaching style** (demanding + prepared → harder within the tier's safety floor; unprepared/conservative → easier); coaching style never overrides the unprepared rule
-   Forbid maximal/all-out/VO2max tests
-   **Strength = movements, not numbers:** duration-only in `targets`; no sets, reps, loads, percentages, or numeric RPE anywhere in strength targets or execution text. Describe the movement and, for equipped strength, its relative load in words ("light", "moderate") rather than a number — e.g. "controlled goblet squats with a light weight," never "3×10 squats" or "squats at 60% of 1RM." The prompt gives **two** worked examples: one bodyweight session, one equipped-gym session, so the model has a template for either equipment situation rather than defaulting to generic, equipment-blind phrasing.
-   Honor desired sessions/discipline (but don't force a zero-baseline discipline hard)
-   Produce distinct sessions, one short complete `purpose` each (≤120 characters, one sentence)
-   Emit guardrails + logging instructions

**Why it's good:** hard rules live in code/schema; the prompt handles *judgment* (session design, purpose wording, movement selection). Position and structure were tuned so rules aren't lost — the two-example strength rule in particular was added after an equipment-blind repair (see Step 7) was silently flattening a gym athlete's session content down to the same generic text as a beginner's; giving the model a concrete gym example fixed that at generation time, first pass, without ever needing the repair to fire.

---

## Step 5 — The output schema (structured, discriminated)

The model must return a `FirstWeekPlanPrescription` (`backend/app/schemas/weekly_plans.py:176-189`), a discriminated union on `discipline`:

-   **`FirstWeekEnduranceSession`** (RUNNING/CYCLING/SWIMMING) → `targets: SessionTargets`, which can hold `duration_minutes` (required), plus `distance_meters`, `average_hr_bpm`, `hr_range_bpm`, `average_power_watts`, `pace_seconds_per_km`, `swim_pace_seconds_per_100m`, `rpe` — all optional except duration.
-   **`FirstWeekStrengthSession`** (STRENGTH) → `targets: StrengthSessionTargets`, which has **only** `duration_minutes` (`gt=0, le=360`). The forbidden fields (sets, reps, loads, RPE) don't exist as attributes on this type at all — a strength session literally cannot carry them; a test (`test_strength_targets_are_rejected_by_the_first_week_schema`) confirms `extra="forbid"` rejects them outright at the schema boundary.

Every session (`PlanSession` base) carries: `discipline`, `purpose` (one short sentence, ≤120 chars, enforced at validation), `intensity` (`IntensityTarget`: `metric`, `target_range`, `rpe_range`, `guidance`), `objective`, `targets`, `execution` (free text, ≤800 chars).

`IntensityTarget.metric` is one of `RPE` / `HEART_RATE_BPM` / `POWER_WATTS` / `PACE_SECONDS_PER_KM` / `SWIM_PACE_SECONDS_PER_100M` at the type level (`HEART_RATE_BPM` remains a valid literal — nothing constructs it for a first-week session any more, but the type itself is a superset of what actually gets produced, not a runtime guarantee by itself; the runtime guarantee is Step 2 + Step 6 together).

`FirstWeekPlan` (the persisted, code-finalized form) adds: `plan_kind` (`"FIRST_WEEK_MENU"`), `guardrails[]` (≥1), `logging_instructions[]` (≥1), `tests[]` (always empty for a probe week), `sessions_per_discipline`, `total_minutes_per_discipline` — the last two computed by code from the sessions, not trusted from the model, and cross-checked against the actual session list (`require_accurate_summaries`).

**Why it's good:** encoding the strength constraint in the *schema type* (not just prose) is what flipped first-pass validity from 0/5 to 5/5. **Hard constraints → schema; soft guidance → prose.**

---

## Step 6 — Validation (deterministic, the source of truth)

`validate_first_week_plan()` (`backend/app/services/weekly_planning/validation.py:205-276`) is the full rule set applied to every generated menu, before anything is shown to the athlete. Every code below is FIRST_WEEK-specific (the ongoing weekly planner has a separate, non-overlapping rule set):

| Code | Condition |
|---|---|
| `FIRST_WEEK_PURPOSE_NOT_CONCISE` | `purpose` is not exactly one sentence (single `.`/`!`/`?` ending) of ≤120 characters. |
| `FIRST_WEEK_ZONE_CONFLICT` | The session's `intensity.metric`/`target_range` doesn't match the resolved zone for that discipline, or falls outside every one of its easy/moderate/hard bands. |
| `FIRST_WEEK_RPE_REQUIRED` | The zone is `RPE_FALLBACK` but the session's metric isn't `RPE`. **This is the second layer of the "no HR prescription" guard** — an `RPE_FALLBACK` zone (which HR always is now) with any non-RPE metric fails here regardless of what metric was attempted. |
| `HARD_ON_ZERO_BASELINE` | The discipline has zero stated *and* zero evidenced volume, and the session is hard (`intensity.is_hard`, i.e. `rpe_range[1] >= 7`). |
| `STRENGTH_OVER_SPECIFIED` | A strength session's execution text matches `_STRENGTH_PRESCRIPTION` (a regex for `sets`/`reps`/`loads?`/`kg`/`lb`/`%`/`NxN`/"one-rep", case-insensitive) — checked per-session, independently, not blanket-applied to every strength session in the menu. |
| `AVAILABILITY_CONFLICT` | No confirmed-available day/window fits the session's discipline and duration. |
| `FIRST_WEEK_DUPLICATE_SESSION` | Two sessions in the same discipline share an identical (purpose, intensity, objective, execution) signature. |
| `FIRST_WEEK_UNPREPARED_TOO_HARD` | Tier is `UNPREPARED` and any session in that discipline has `rpe_range[1] > 4`. |
| `FIRST_WEEK_CALIBRATION_SIGNAL_MISSING` | Tier is `DEVELOPING`/`TRAINED`/`WELL_TRAINED`, the zone is `NUMERIC`, there are ≥2 sessions, and *none* reaches `rpe_range[0] >= 5` — a prepared athlete must get at least one controlled-moderate session, not an all-easy week. |
| `SESSION_COUNT_UNDERSHOOT` | The athlete's stated `desired_weekly_sessions` for a non-zero-baseline discipline isn't met exactly by the session count. |

**Why it's good:** the LLM proposes, **code disposes.** Safety and correctness don't depend on model quality — a weaker/cheaper model produces equally *safe* plans because the validator is the floor. The strength and HR guards in particular are schema- and code-level, not prose the model could ignore.

---

## Step 7 — Repair loop, then fallback

Flow: **generate → validate → repair (bounded retries) → deterministic fallback**, in `WeeklyPlanningService._finalize_first_week()` (`backend/app/services/weekly_planning/service.py:679-`), with up to `_FIRST_WEEK_REPAIR_ATTEMPTS = 2` repair round-trips (line 101).

-   On validation failure, a `repair_message` containing the previous plan + only the specific violation codes/details is sent back to the LLM, asking it to fix only those → if the repaired plan then validates clean, the result is `model_repaired`.
-   Independently of the LLM round-trip, code-level repair (`_repair_first_week_menu()`, `validation.py:624-680`) runs first on each attempt: for `FIRST_WEEK_RPE_REQUIRED` / `FIRST_WEEK_ZONE_CONFLICT` / `HARD_ON_ZERO_BASELINE` / `UNSUPPORTED_TARGET`, *every* session's intensity is forced to easy RPE 3 and all numeric target fields are cleared. For `STRENGTH_OVER_SPECIFIED`, the repair is **surgical and per-session** (this is the current, fixed behavior): each strength session is checked independently for whether *it itself* has extra targets or matches the sets/reps/loads regex — a compliant sibling session is left completely untouched. For a session that does violate, only the offending sentence(s) are stripped from `execution` (split on sentence boundaries, drop any sentence containing the regex match), keeping the rest of the description intact; the generic fallback literal ("Use controlled form throughout and finish with plenty in reserve.") is used only if nothing usable (< 15 characters) survives the strip. This replaced an earlier version that overwrote the *entire* menu's strength execution text with that one generic literal the moment any strength session tripped the check — which flattened a gym athlete's equipment-specific session down to the same vague text as a beginner's bodyweight session, even when only one of the two sessions had actually violated.
-   If repair is exhausted (both attempts) or the schema itself fails to validate → a **deterministic, baseline-scaled fallback** menu (`_build_first_week_fallback()`), clearly labeled to the athlete as degraded, respecting requested session counts and skipping zero-baseline endurance disciplines rather than forcing them.
-   Every plan records `generation_source` — one of **`model`** (validated clean on the first attempt), **`model_repaired`** (validated clean after at least one repair round-trip), or **`fallback`** (repair exhausted or schema failure) — plus a fallback reason and error list when applicable, persisted in `validation_jsonb` on `weekly_training_plans`.

**Why it's good:** it **never hard-fails** on a new user's first plan, and you can measure quality (model vs. repaired vs. fallback rates) instead of guessing. The surgical strength repair specifically means a repair round-trip no longer costs equipment-appropriate detail for the sessions that were already fine.

---

## Step 8 — Rendering

The menu renders as compact cards (main line: discipline · duration · intensity + short metric range; then one purpose line), with a "no fixed schedule" note, guardrails, and logging instructions. Telegram HTML for bold; UTF-8 glyphs; message-length handled. Pace values in these cards go through the shared min:sec formatter (see "Unit formatting" below) rather than showing raw seconds.

---

## The final output (what the athlete gets)

A **menu** of sessions (2/discipline by default) they place on their own days, each with:

-   Discipline · duration · intensity label · numeric zone (if available) or RPE
-   A one-line purpose
-   Guardrails for placement (spacing, rest, safety)
-   Logging instructions (what to record — the signal-gathering)

---

## Related systems (feed the same planner, or feed off it)

These aren't steps in the generation pipeline above — they're adjacent capabilities that either supply data the planner reads, or read from what the planner (or the athlete's own baseline) produces.

### Actuals capture

Completed workouts are stored independently of any plan (`backend/app/db/models.py`): a universal `Workout` row (`athlete_id`, `discipline`, `started_at`, `duration_seconds`, `source`, `external_id`) plus exactly one discipline-specific detail row (`RunningWorkoutDetails`, `CyclingWorkoutDetails`, `SwimmingWorkoutDetails`, `StrengthWorkoutDetails`, etc.). A `Workout` carries no foreign key to any plan or planned session — matching is computed on demand elsewhere, not stored here.

`CyclingWorkoutDetails` now has `average_power_watts`/`max_power_watts` (nullable `Float`, migration `0051_cycling_power`) — the first actuals-side power fields in the schema, added specifically so smart-trainer/static-bike power can be logged and eventually compared against the FTP-derived power zones from Step 2.

The screenshot-import path (`ManualWorkoutImportRequest`, `backend/app/schemas/manual_import.py`) now extracts, when visible on screen: `average_pace_seconds_per_km` (running), `average_pace_seconds_per_100m` (swimming), `average_speed_kph`/`max_speed_kph` and `average_power_watts`/`max_power_watts` (cycling, prioritizing static-bike/smart-trainer displays), and `average_cadence`/`max_cadence` (running or cycling) — alongside the fields it already captured (discipline, timing, distance, calories, heart rate). The extraction prompt (`backend/app/integrations/llm/vision.py`) instructs the model to leave any of these empty rather than invent a value when the screenshot doesn't show it. `WorkoutScreenshotService.request_heart_rate()`/`provide_heart_rate()` (`backend/app/services/workout_screenshot/service.py`) let the athlete supply average/max HR by hand if the screenshot itself didn't show it — an explicit ask, not an inference.

Pace/speed derivation from distance + duration was already in place before this round of changes and is unchanged: every `*WorkoutDetailsData` model (`backend/app/schemas/workouts.py`) has a `model_validator` that recomputes `average_pace_seconds_per_km` / `average_speed_kph` / `average_pace_seconds_per_100m` from distance and moving duration whenever both are present, for every import source (screenshot, TCX, FIT, Apple Health) — this recomputed value overwrites any directly-extracted reading, by design, matching how every other import source already behaves. Power and cadence have no such derivation (they can't be computed from distance/duration), so for those fields, direct extraction is the only source.

### Unit formatting

`format_pace_min_sec(seconds_per_unit, *, unit_label)` (`backend/app/services/formatting.py`) is the one function that converts a stored seconds-based pace into the min:sec an athlete sees (e.g. `296, unit_label="/km"` → `"4:56/km"`; `95, unit_label="/100m"` → `"1:35/100m"`). Pace stays in seconds everywhere else for math and comparison. It's used by the plan-session-card renderer (`_intensity_range` in `backend/app/bot/messages.py`), the athlete's stated goal-pace display (`_performance_target_lines`), and the `/zones` command below — every place a pace reaches the athlete now goes through this one function rather than each renderer doing its own seconds→minutes conversion.

### The `/zones` command

A standalone, read-only, plan-independent Telegram command (`CoachBotApplicationService.zones()` in `backend/app/bot/service.py`, dispatched like `/profile`; no LLM call). It shows:

-   **Heart rate** — the age-estimated Tanaka zones from Step 2, with the approximation caveat, or a prompt to add a birth year if one isn't on file.
-   **Running pace** and **swimming pace** — from a recent race result / 400 m time in the baseline, if present, rendered in min:sec via the formatter above.
-   **Cycling power** — from FTP, if present.
-   For any of the three non-HR zones with no numeric source, an explicit "no numeric source yet — use RPE/feel" line instead.

Unlike the planner's own zone resolver, `/zones` shows a zone whenever the matching baseline value exists, regardless of recent workout evidence — it's explicitly plan-independent (`resolve_athlete_display_zones()`, `backend/app/services/athlete_zones.py:77-113`).

---

## Why it's a good first-week planner (the summary)

1.  **Adapts to the athlete** — verified live: fit athlete got pace/power + moderate work (405 min); beginner got RPE-only, easy, lower volume (220 min). Re-verified after the strength-repair fix: the fit athlete's gym-equipped strength session now comes back gym-appropriate *and* compliant on the first pass, no repair needed.
2.  **Grounded, not hallucinated** — zones computed in code from real data, including a heart-rate estimate that's available for reference without ever being prescribable.
3.  **Safe by construction** — safety in the validator, not dependent on model smarts; the HR guarantee alone has three independent layers (resolver, prompt, validator+repair).
4.  **Reliable** — repair + fallback mean it always delivers something valid; repair no longer costs equipment-specific detail on sessions that were already fine.
5.  **Observable** — `generation_source` tells you exactly what produced each plan.
6.  **Measurement-focused** — probe design (no goal-chasing, logging built in) so the *next* stage has real data to work from, and actuals capture (power, pace, speed, cadence, on-request HR) is growing to give that next stage something real to compare against.
7.  **Menu-mode** — athlete places sessions, and you capture *revealed* availability.
