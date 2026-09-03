# Multi-horizon planning workflow

One example athlete carried through every stage: **Marc**, male, 34, 74 kg, 179 cm,
training for an Olympic-distance triathlon 20 weeks away. Weakest discipline:
swimming. Coaching style: normal.

---

## What we collect, once, at onboarding

- Goal and race date.
- Sex, age, weight, height.
- Availability, equipment, health limitations.
- Coaching style: conservative, normal, or demanding.
- **Desired sessions per week, per discipline** — a soft target, not a promise.
  Marc asks for running 3x, cycling 2x, swimming 3x (he wants to fix his weak
  swim). Checked on the spot against his stated availability: does he actually
  have three day-slots that allow swimming? If not, he's told so immediately
  instead of finding out later from a plan that can't fit it.
- A self-reported baseline per sport the goal needs. For Marc, that's running,
  cycling, and swimming (triathlon needs all three), plus a triathlon-only block:

| Sport | What's asked | Marc's answers |
|---|---|---|
| Running | sessions/week, typical minutes/week, longest recent run, recent race time (optional) | 3/week, 90 min/week, 35 min longest, no recent race |
| Cycling | sessions/week, typical minutes/week, longest recent ride, riding environment, confidence, recent FTP test (optional) | 2/week, 100 min/week, 60 min longest, outdoor, confident, **FTP 230W, tested last month** |
| Swimming | sessions/week, typical minutes/week, longest continuous swim, environment, recent 400m time (optional) | 0/week, 0 min, 0 m, no pool access stated, no recent time |
| Triathlon | prior experience, self-rated weakest discipline, open-water confidence | none, **swimming**, not confident |

This is stored once, and it's what the very first plan is built from, before Marc
has synced a single workout.

---

## Stage 1 — Week one, before any workout exists

**We have:** everything in the table above. No workout history at all.

**We build this prompt for the planner:**

> Evidence state — running: SELF_REPORTED. cycling: SELF_REPORTED. swimming:
> SELF_REPORTED, zero recent volume.
> Self-reported baseline: running 3x/week ~30 min, cycling 2x/week ~50 min, FTP
> 230W, swimming none. Desired sessions/week: running 3, cycling 2, swimming 3.
> Profile: male, 34, 74kg, 179cm. Goal: Olympic triathlon, 20 weeks out.
> Coaching style: normal.
> Rule for SELF_REPORTED: no hard sessions, duration ranges only, never exact
> pace or power. Zero recent volume means one short easy introductory session,
> not "maintain zero."

**We get back:** a first week with one easy run (~30 min), one easy ride (~45
min, no power number attached even though we have his FTP), and one short,
easy, "get in the water and get comfortable" swim. Nothing hard anywhere.

---

## Stage 2 — Workouts start syncing, and the plan is checked every time

Every workout Marc syncs quietly updates his evidence. Nothing is triggered yet;
this is just data accumulating in the background.

Whenever Marc asks for a next week, the same readiness check runs fresh — it
is never a one-time gate. Week 2, still mostly self-reported: another
conservative week, same as week 1. Week 3: real running and cycling evidence
now exists (three real sessions, two real active days, no self-report needed).
Nothing special happens to trigger this — it's just true the next time the
check runs, so stage 3 fires on that same request.

---

## Stage 3 — The macrocycle: what the next block is for

**Trigger:** the first time any one sport's evidence becomes real. Not all
three — for Marc that's running and cycling in week 3, while swimming is
still empty.

**We build this prompt:**

> Real evidence so far: running and cycling look like a consistent 3-4
> sessions/week each. Swimming: nothing real yet, self-reported as zero,
> named by the athlete as his weakest discipline, and he's asked for 3
> sessions/week going forward.
> Profile: male, 34, 74kg, 179cm. 20 weeks to an Olympic triathlon.
> Coaching style: normal.
> Decide the phase structure for the weeks ahead, and which discipline needs
> priority, in one sentence.

**We get back:**

> Phase: BUILD, running to PEAK around week 13, TAPER in the last 3 weeks.
> Priority: swimming. "Swimming has no real training behind it yet and the
> athlete has already named it as his limiter — give it steady, safe volume
> for the next several weeks while running and cycling hold roughly flat."

This is stored once and reused every week after, until something invalidates
it (Marc's goal changes, or the progress check in stage 7 flags it as stale).
It is not regenerated every week.

---

## Stage 4 — The weekly plan, every week

**We have:** the macrocycle's current phase and priority (read, not
regenerated), this week's real evidence, availability, desired session counts,
equipment, health limitations, coaching style — plus, from week 2 onward, last
week's check-in from stage 6, when one exists.

**We build this prompt (the base of it already exists):**

> Phase: BUILD. Priority discipline: swimming, because [the one-sentence
> reason from stage 3]. Running and cycling: WELL_EVIDENCED, plan normally.
> Swimming: still SELF_REPORTED. Coaching style: normal — safety ceiling is a
> 50% increase over last week's actual load. Desired sessions/week: running 3,
> cycling 2, swimming 3.

**We get back:** a normal week where swimming gets more attention than its
own evidence alone would justify, because the macrocycle said why, aiming for
(not locked to) the requested session counts.

---

## Stage 5 — Checking the plan before it's shown

**Trigger:** right after the plan above is generated, and after the existing
safety checks (is the volume jump too big, did a weak sport get a hard
session) already passed.

**We build this prompt:**

> Here is the week just generated, and here is what it was supposed to be for
> (the macrocycle's priority, and this week's own stated purpose). Does the
> week actually match what it claims to be doing? Flag anything that doesn't
> — a stated "easy week" with rising numbers, a hard bike the day before a
> key run, the same session repeated all week.

**We get back:** either "fine as is," or one specific problem named plainly
enough to send back for a single rewrite. One rewrite is allowed, covering
both this check and the earlier safety checks together — not one attempt
each. If it's still wrong after that, the plan is refused rather than shown
to Marc.

---

## Stage 6 — The weekly check-in

**When it happens:** when Marc asks for the next week, and the week that just
finished hasn't been reflected on yet. Not daily — every other day, syncing a
workout screenshot is still the whole interaction.

**What we compute, before asking anything:** the same fitness calculation
that already exists, run over just the seven days that ended, instead of a
rolling 30. Per discipline: sessions and minutes planned versus actual,
distance and pace when known, and average and max heart rate — but only from
readings good enough to trust, exactly as already filtered elsewhere. From
that, one derived number: this week's pace relative to heart rate, compared
against Marc's own recent baseline for that sport — better, worse, or about
the same. That's the actual "how hard did this really feel" signal, computed
in code, not asked for.

**What we ask, two separate things:**

- A one-tap question: any pain or discomfort this week — none, mild,
  moderate, severe. This is the only part that feeds the hard rule that cuts
  load on real discomfort, so it has to be a plain, unambiguous answer, not a
  sentence a model has to interpret in the moment.
- An open question: "How did this week feel? Anything worth calling out?"
  Free text, entirely optional.

**Marc's week 4, for example.** Pain tap: none. Free text: "Running felt too
easy, I think I could push harder." The computed numbers agree — his pace at
a similar heart rate improved this week. Both go into stage 4's next prompt
as-is, no extraction step in between:

> Athlete reported running felt too easy; this week's running pace at a
> similar heart rate was faster than his recent baseline, consistent with
> that. No pain reported.

**And the next plan reflects it**, in the model's own words, not a canned
response: "Good sign that the easy runs feel comfortable — that's the base
building working as intended. Keeping two of your three runs easy, but
swapping the third for a steadier, more demanding session so you keep being
challenged without giving up the aerobic work the easy days are doing."

Silence on either question is fine. The computed numbers alone still reach
stage 4; nothing is lost by skipping the questions, only by skipping the
screenshot itself.

---

## Stage 7 — Is the plan actually working

**Trigger:** a quick check runs in the background every week, no model
involved: how many of the planned hours actually happened in the last two or
three completed weeks, and is the priority sport's fitness number moving up,
flat, or down.

**If nothing looks wrong:** nothing happens, nothing is sent to Marc.

**If swimming adherence has been under 70% for two weeks running, or its
fitness number is flat or falling for that stretch, we ask a model to write
one message:**

> "Swimming has been light the last two weeks — want me to ease that part of
> the plan back and take another look in a few weeks, or keep pushing as is?"

Marc taps accept or decline, the same way he would for any other proposal.
Accepting is what sends stage 3 back to run again with fresh evidence — the
macrocycle itself never changes on its own.

---

## Coaching style, in practice

The same week for Marc looks different depending on the one setting he chose
at onboarding:

| | Conservative | Normal | Demanding |
|---|---|---|---|
| How far a week can jump over last week's actual load | +30% | +50% | +70% |
| How often a lighter week is built into the block | every 3 weeks | every 4 weeks | every 5 weeks |
| Introducing a new sport (like Marc's swimming) | very gradually | steadily | steadily, slightly faster |

None of these ever touch the one rule that never moves: reported pain always
cuts the load, at every setting.

---

## Where each new piece lives

- **Coaching style** and **desired sessions per discipline** — two separate
  fields on the athlete's profile, asked together at onboarding, editable
  later. One tunes intensity, the other tunes frequency.
- **The macrocycle** (phase, priority discipline, the one-sentence reason) —
  one row per athlete, replaced only when it goes stale or the goal changes.
- **The weekly plan** — mostly unchanged, now also reads the macrocycle and,
  from week two onward, the previous check-in.
- **The quality check result** — stored alongside the plan it checked.
- **The weekly check-in** — one row per week: the pain tap, the free text
  (when given), and the computed diff. Kept as history, so Marc's own
  trajectory can be shown back to him later, not just used once and discarded.
- **The progress check** — one row every time it runs, same reasoning.
