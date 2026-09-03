# Multi-horizon planning workflow

Revised 2026-09-03. Every factual claim in the first draft was re-checked against
the code in `backend/app/`, not against what this document or the 2026-08-28 spec
asserted. Six claims were wrong. Each correction is stated inside the stage it
affects, and they are listed together in the next section so nobody rebuilds from
the old assumptions.

One example athlete carried through every stage: **Marc**, male, 34, 74 kg, 179 cm,
training for an Olympic-distance triathlon 20 weeks away. Weakest discipline:
swimming. Coaching style: normal.

Every stage says three things: what it does, what it was chosen over, and what it
costs. A stage with no stated cost has not been thought about hard enough.

The 2026-08-28 spec stays the source of truth for everything it covers. Where this
document reverses one of its decisions, the reversal is named and argued, not
slipped past.

---

## What the first draft got wrong

| The draft assumed | What the code actually does | Where it now lands |
|---|---|---|
| Stage 1's cold-start prompt rule is new work | Almost all of it already ships in the planner's system prompt, including the zero-volume rule and the ban on exact pace or power | Stage 1 |
| Evidence state and the self-reported baseline still need adding to the prompt | Both are already sent on every planner call | Stage 1 |
| Stage 5 runs after existing volume and hard-session safety checks | Those checks do not exist. Only an availability check exists, and it refuses instead of repairing | Stage 5 |
| The pain tap has a feedback table waiting for it | There is no feedback table. The one the 2026-08-28 spec described has been dropped | Stage 6 |
| Per-week actual minutes per discipline must be computed | Already computed, Monday-anchored, in the athlete's own timezone | Stages 6 and 7 |
| A per-sport fitness number exists that can move up or down | No such number exists. Confidence is a data-quality score, not fitness | Stage 7 |

Two more findings that were not claims in the draft, but break stages in it:

**There is no scheduler.** Nothing in this system runs on a timer. There is no job
queue, no cron, and no APScheduler in the dependency lock file. Every action today
starts with the athlete tapping something. Stage 7's "runs in the background every
week" has no runtime to run in.

**Heart rate is filtered out on the live path.** The filter the draft relies on is
real, but on the only intake path a normal athlete uses it removes everything.
Stage 6 explains this and picks a substitute.

Also worth knowing while reading the older spec: its iOS and HealthKit sections
describe a path that no longer exists. The iPhone companion app was built and then
fully removed, and its credentials table was dropped by migration
`0045_remove_mobile_sync` (`STATE.md`). Workout history now arrives only as an Apple
Health export, a TCX file, or a screenshot.

---

## What this layer may not re-decide

Five decisions from 2026-08-28 constrain everything below. They are listed here
because the first draft quietly broke three of them.

**Phase is arithmetic and lives in code.** Section 4.9 argues it, section 4.10 fixes
the boundaries: 19 weeks or more is `BASE`, 7 to 19 is `BUILD`, 3 to 7 is `PEAK`,
1 to 3 is `TAPER`, and no race date is `GENERAL`. Stage 3 below no longer asks a
model for the phase.

**The model owns volume, code catches danger.** Section 4.6. No code-side rule
computes a target volume. The only hard bound is a rejection above roughly a 50
percent increase on last week's actual load.

**Pain is a switch, not a dial.** Section 4.6. Reported moderate or severe
discomfort cuts load, at every coaching style, before the model is called.

**Propose, never impose.** Section 7.4. Nothing changes a running plan without a tap.

**The plan states its own intent.** Section 4.7. One sentence per week, written by
the model, carried into the next week's briefing. Note that this is designed and not
yet built: the stored plan has no intent field today
(`app/schemas/weekly_plans.py:43-60`).

---

## What we collect, once, at onboarding

Verified against the live onboarding flow. Steps run in this order: goal, race date,
physical profile, availability, equipment, health limitations, and last the baseline
form (`STATE.md`, onboarding sequence). Everything below already exists except the
two fields marked new.

- Goal and race date. The race date is optional and nullable.
- Sex, age, weight, height. Collected, stored, and never yet shown to the planner.
- Availability, as a confirmed structure of per-day disciplines and time windows,
  not free text (`app/schemas/availability.py:52-57`).
- Equipment and health limitations.
- A self-reported baseline per sport the goal needs, asked as a Telegram Mini App
  form rather than a chat prompt.

| Sport | What is asked | Marc's answers |
|---|---|---|
| Running | sessions/week, minutes/week, longest recent run, recent race result (optional) | 3/week, 90 min/week, 35 min longest, no recent race |
| Cycling | sessions/week, minutes/week, longest recent ride, riding environment, riding confidence, recent FTP (optional) | 2/week, 100 min/week, 60 min longest, outdoor, confident, **FTP 230 W** |
| Swimming | sessions/week, minutes/week, longest continuous swim in metres, swimming environment, pool length (optional), recent 400 m time (optional) | 0/week, 0 min, 0 m, environment **NONE**, no pool length, no recent time |
| Triathlon | prior experience, self-rated weakest discipline, open-water confidence | none, **swimming**, not confident |

Two corrections to the draft's version of this table. Pool length is asked and was
missing (`app/schemas/baseline.py:53`). Swimming environment is required, so Marc
cannot leave it blank: he answers `NONE`, which is a stated fact, not an absence
(`app/schemas/baseline.py:52`).

### New field one: coaching style

Conservative, normal, or demanding. One value on the athlete profile, chosen at
onboarding, editable afterwards through the existing profile settings flow.

**Considered instead: infer it.** Watch how the athlete responds to load and adjust
silently. Rejected for now because there is no adherence signal yet to infer from,
and because a silent inference cannot be argued with. An athlete who feels the plan
is too soft has no way to say so.

**What it costs.** One more onboarding question, on a flow that is already nine
steps long, answered before the athlete has any experience of the coach to judge
against. Expect the first answer to be close to meaningless and the first edit,
weeks later, to be the real one.

### New field two: desired sessions per week, per discipline

A soft target, not a promise. Marc asks for running 3, cycling 2, swimming 3,
because he wants to fix his weak swim.

**Considered instead: derive it from availability.** Availability already says which
days allow which sport, so the number of possible swim days is computable. Rejected
because possible is not wanted. Marc has five days that allow swimming and wants
three swims, and the gap between those two numbers is the actual preference.

**What it costs.** A second number the athlete can set that the plan will not always
honour, which is a reliable source of disappointment unless the wording is careful
every time it appears.

### The feasibility check, specified

The draft said Marc is "checked on the spot against his stated availability" and left
the check undefined. Two engineers would build that differently, so here is the rule.

For each discipline with a requested count, count the days where the athlete is
available and that discipline is listed for the day. Call that the eligible day
count. The check passes when the requested count is at most the eligible day count.
One session per discipline per day, for this check only.

Marc has swimming listed on five days and asks for three swims. That passes. Had he
asked for six, he would be told at once: "You have five days that allow swimming, so
six swims a week will not fit. Three or four is realistic."

**Considered instead: allow two sessions of the same sport in one day.** A real
coach sometimes does that. Rejected for the feasibility check because doubles are a
progression decision, not a starting assumption, and counting them here would let an
impossible request pass silently.

**What it costs.** The check is cruder than the plan it predicts. The planner can
still put two runs on one day if it judges that right, so a request the check passed
can still turn out to be more than a week can hold.

**A live defect this check will hit.** The availability structure spells strength as
`strength_training`, while the discipline enum spells it `STRENGTH`
(`app/schemas/availability.py:10`, `app/domain/enums.py:98`). The existing
plan-versus-availability check compares those two strings directly
(`app/services/weekly_planning/service.py:170-173`), so any strength session fails
the check and the whole plan is rejected as an availability conflict. Hiking has no
availability spelling at all. This is a bug in shipped code, not in this design, and
whichever piece of work touches that comparison first should fix the mapping in one
place.

---

## Stage 1 - Week one, before any workout exists

**We have:** the onboarding table above. No workout history at all.

**Correction: most of this stage already ships.** The draft presented the cold-start
rule as new work. It is not. The planner's system prompt already tells the model
what `SELF_REPORTED` means, already says to plan conservatively, already says to use
duration ranges rather than exact pace or power, and already says that zero stated
volume means one short easy introductory session rather than a volume target to
maintain (`app/workflows/prompts/weekly_planning.py:28-36`). Marc's FTP of 230 W is
already in the context and already excluded from the session text by that rule.

The evidence state per discipline is already in the prompt
(`app/services/weekly_planning/service.py:504-507`), and so is the whole
self-reported baseline (`app/services/weekly_planning/service.py:499-503`). The
readiness gate already lets a self-reported athlete through with no workouts at all
(`app/services/weekly_planning/evidence.py:80-89`).

So Marc gets a sane first week today, and the earlier draft was proposing to build it
twice.

**What is genuinely new in this stage:** three additions to the prompt.

1. Physical profile: age, sex, height, weight. Stored since onboarding and never
   sent, because the profile reader returns only availability and health limitations
   (`app/repositories/profiles.py:45-54`). The 2026-08-28 spec already calls for this
   in section 4.2.
2. Coaching style, and what it means numerically.
3. Desired sessions per discipline, stated as a target to aim at, not a rule.

**We get back:** a first week with one easy run of about 30 minutes, one easy ride of
about 45 minutes with no power number attached, and one short "get in the water and
get comfortable" swim. Nothing hard anywhere.

**Considered instead: hold the plan until real workouts exist.** That is what the
system did before the whole-athlete floor was built, and it meant a triathlete with a
weak swim got nothing at all. Rejected then, still rejected.

**What it costs.** The first week is built entirely on numbers Marc typed about
himself, which people routinely overstate. The conservatism in the prompt is the only
protection, and telling a model to go easy does not make it go easy. The 2026-08-28
spec flags this in section 3.2 and asks for a test that reads the generated plan and
asserts the thin-sport session is actually short and easy. That test still does not
exist and this document does not change that.

---

## Stage 2 - Workouts start syncing

Every workout Marc logs updates his evidence. The readiness check runs fresh on every
plan request, so nothing needs triggering
(`app/services/weekly_planning/service.py:409-437`).

The states move in this order as evidence arrives: `SELF_REPORTED` while the athlete
has typed numbers and logged nothing, `THIN` at one or two real sessions, and
`WELL_EVIDENCED` at three sessions across two distinct days inside the 30-day planner
window (`app/services/weekly_planning/evidence.py:18-34`, `app/config.py:81`).

### The correction that matters: logging a workout currently shrinks the plan

This is not a claim from the draft. It is a defect the draft's "nothing special
happens" framing would have carried straight into the build.

Marc says he runs three times a week, so running starts at `SELF_REPORTED`, and the
prompt tells the model to plan up to about his stated volume
(`app/workflows/prompts/weekly_planning.py:28-36`). He then logs one real run.
Running drops to `THIN`, and the prompt tells the model that `THIN` means one short,
easy, clearly introductory session (`app/workflows/prompts/weekly_planning.py:26-27`).

His plan gets smaller because he trained. The self-reported numbers are still true,
still stored, and still in the context, but the state label has overridden them.

**The fix, and it belongs in this layer because this layer is what makes it
visible.** Evidence state and self-reported volume are two separate inputs, not one
ladder. The prompt must say that `THIN` describes how much the system has verified,
not how much the athlete does, and that a `THIN` sport with a self-reported baseline
is planned at no less than the self-reported volume unless the safety rules say
otherwise. The state still governs intensity: `THIN` still means no hard sessions.

**Considered instead: keep the sport at `SELF_REPORTED` until it reaches
`WELL_EVIDENCED`.** Simpler to state, and it removes the demotion entirely. Rejected
because it throws away true information: one logged run is real evidence and the
model should see that it exists. It also makes the transition abrupt, jumping from
self-reported to fully evidenced with nothing in between.

**What it costs.** Two inputs that can disagree, and a prompt that has to explain how
they interact. A model given "the athlete says 90 minutes a week, we have verified
25" may split the difference in ways nobody predicted. This needs the same
read-the-generated-plan test as stage 1.

---

## Stage 3 - The macrocycle: what the next block is for

**Trigger, stated precisely enough to implement:** the first plan request at which at
least one target discipline is in state `WELL_EVIDENCED` and no macrocycle row
exists. For Marc that is week 3, when running and cycling have three sessions across
two days each and swimming is still empty.

**Correction: the phase is not the model's to decide.** The first draft asked a model
for "the phase structure for the weeks ahead" and got back "BUILD, peaking around
week 13, taper in the last 3." Two problems. The 2026-08-28 spec decided phase stays
in code precisely so it cannot drift, and its table puts Marc at 20 weeks out in
`BASE`, not `BUILD` (section 4.10). And the rest of that answer just restates the
same table's 7-week and 3-week boundaries. The model call was either reproducing
arithmetic or contradicting it.

**So the split is:** code computes the phase from the race date, every week, from the
existing table. The macrocycle owns the three things arithmetic cannot give.

1. **The priority discipline.** Which sport gets more than its own evidence would
   justify.
2. **The one-sentence reason.** Written by the model, shown to the athlete, and
   carried into every weekly prompt.
3. **An optional, explicit deviation from the default block structure**, with a
   stated why. The 2026-08-28 spec invites this itself: it notes that a long runway
   would often have base split in two, and that its boundaries are an uncoached
   opinion (section 4.10).

**We build this prompt:**

> Real evidence so far: running and cycling are at a consistent 3 to 4 sessions a
> week each. Swimming: nothing logged, self-reported as zero, named by the athlete as
> his weakest discipline, and he has asked for 3 sessions a week.
> Profile: male, 34, 74 kg, 179 cm. 20 weeks to an Olympic triathlon.
> Current phase, computed: BASE. Coaching style: normal.
> Name the priority discipline and say why in one sentence. If the default block
> structure is wrong for this athlete, say so and say why. Otherwise leave it alone.

**We get back:**

> Priority: swimming. "Swimming has no real training behind it yet and the athlete
> has already named it as his limiter, so give it steady, safe volume for the next
> several weeks while running and cycling hold roughly flat."

**Stored once and reused every week**, until the goal changes or stage 7's offer is
accepted. It is not regenerated weekly.

**Considered instead: regenerate the macrocycle every week.** Always current, no
staleness problem, no invalidation rule to get wrong. Rejected because a priority
that can change every Monday is not a priority. The whole value of this row is that
it holds a decision still across weeks so the model can build on it.

**What it costs.** A stored opinion that outlives the evidence it was formed from. If
Marc starts swimming three times a week and improves fast, the row still says
swimming is the priority until something invalidates it. Stage 7 is the only thing
that notices, it only notices when things go badly, and it will not notice this case
at all.

**Invalidation, specified:** the macrocycle is discarded and regenerated when the
goal signature changes (a different goal template or supporting template, which the
planner already computes at `app/services/weekly_planning/service.py:702-712`), when
the race date changes, or when Marc accepts stage 7's offer.

**Deliberately not included: expiry after N weeks.** A timer would paper over the
staleness problem above without detecting anything. If the row goes stale, the honest
fix is a check that can see it, not a clock.

**Still undecided:** what happens to the macrocycle when there is no race date at
all. The phase is `GENERAL`, the block structure question is meaningless, and a
priority discipline may still be useful. Nobody has thought this through and it
should not be invented here.

---

## Stage 4 - The weekly plan, every week

**We read:** the macrocycle's priority and reason, the computed phase, this week's
real evidence, availability, desired session counts, equipment, health limitations,
coaching style, and from week 2 onward the previous check-in from stage 6.

**We add to the existing prompt:**

> Phase: BASE, 20 weeks to the event. Priority discipline: swimming, because [the
> sentence from stage 3]. Running and cycling: WELL_EVIDENCED. Swimming:
> SELF_REPORTED, zero logged volume. Coaching style: normal, so the safety ceiling is
> a 50 percent increase over last week's actual load. Desired sessions a week:
> running 3, cycling 2, swimming 3. Aim for these, do not force them.

**We get back:** a normal week where swimming gets more attention than its own
evidence alone would justify, because the macrocycle said why.

### The trap: the plan cache will hide the new inputs

The planner already fingerprints its inputs and returns the stored plan unchanged
when the fingerprint matches (`app/services/weekly_planning/service.py:514-520`).
That fingerprint covers evidence, goal, and availability only
(`app/services/weekly_planning/service.py:113-148`).

Add the macrocycle, the coaching style, and the check-in to the prompt without adding
them to the fingerprint, and this happens: Marc does his check-in on Sunday, asks for
a plan, and gets back the plan generated before the check-in, marked "existing", with
no model call and no error. Silent and very hard to notice.

Every new input to the prompt goes into the fingerprint. This is one line of work and
one of the easiest things in this document to forget.

**What this stage costs.** The prompt grows by a good margin, and the 2026-08-28 spec
chose `function_calling` partly to keep the prompt small (section 4.3). That choice
still holds and is already shipped (`app/integrations/llm/live.py:95-98`), but the
saving it bought is being spent here.

---

## Stage 5 - Checking the plan before it is shown

**Correction: the checks this stage claimed to run after do not exist.** The draft
said this stage fires "after the existing safety checks (is the volume jump too big,
did a weak sport get a hard session) already passed." Neither check exists. The only
thing that runs after generation today is the availability comparison, and it does
not repair: a plan that does not fit availability is thrown away and the athlete is
told nothing useful (`app/services/weekly_planning/service.py:303-308`).

So this stage is not one piece of work. It is three, in order.

**First, build the code checks the 2026-08-28 spec already specified** (sections 4.5
and 4.6). They are cheap, deterministic, and testable.

- Reject a total weekly load more than the coaching style's ceiling above last week's
  actual load. Load means total minutes across target sports, which is the honest
  measure available today (section 4.6).
- Reject any increase at all when moderate or severe discomfort was reported.
- Reject a `HARD` session for any sport in `THIN`, `NONE`, or `SELF_REPORTED`.
- Keep the availability check, and make it name what it rejected.

**Second, give all of them one shared repair attempt.** The plan is regenerated once,
told only which constraints failed. If it still fails, refuse rather than show
something wrong. This matches section 4.1 step 8.

**Third, and only then, the model critic:**

> Here is the week just generated, and here is what it was supposed to be for: the
> macrocycle's priority, and the week's own stated intent. Does the week match what
> it claims to be doing? Name one specific problem or say it is fine. Examples of a
> problem: a stated easy week whose numbers went up, a hard bike the day before a key
> run, the same session repeated all week.

**Reversal named: this adds a model call to the normal path.** The 2026-08-28 spec
says of the two new stages, "Both new stages are code. No extra model calls in the
normal path" (section 4). This stage breaks that.

The reversal is defensible: it is one extra call per athlete per week, the same order
of cost as the planner call itself, and it catches a class of problem that no code
check can express. "This week says it is easy and it is not" is a judgement about
whether a plan means what it says. But it is a real cost and it doubles the
provider-failure surface for a plan request.

**Running the code checks first is what makes the cost acceptable.** A plan that
fails a cheap deterministic check never reaches the critic.

**Considered instead: no critic, just the code checks.** Cheaper, simpler, one fewer
failure mode. Rejected because the checks only catch the extremes, and the failure
this stage exists for is a plan that is safe, fits availability, and is quietly not
what it says it is. That is exactly what a code rule cannot see.

**Considered instead: let the critic rewrite the plan itself.** Rejected because a
critic that rewrites is a second planner with less context, and there would then be
two places where a plan can be authored.

**What it costs, beyond the call.** A critic that finds a problem every week is worse
than no critic, because the repair budget gets spent on taste rather than defects. If
it fires on more than a small minority of weeks, the prompt is wrong and it should be
turned off until it is fixed. Nobody has run this against a real model yet.

**Where the verdict lives.** The plan row already carries revisions and superseding
(`app/db/models.py:750-800`), so the verdict is a column on the plan row it judged:
the outcome, the named problem if any, and whether a repair was attempted. Not a
separate table, because a verdict has no life of its own.

---

## Stage 6 - The weekly check-in

**When it happens:** when Marc asks for the next week's plan, and the most recently
completed Monday-to-Sunday week has not been reflected on yet. Not daily. On the
other days, logging a workout screenshot is still the whole interaction.

Note the edge this creates. The planner always plans the Monday strictly after today
(`app/services/weekly_planning/service.py:732-741`). If Marc asks on a Wednesday, the
week being reflected on is the one that ended on Sunday, and the three days of the
current week are not covered. That is acceptable and should be said out loud in the
question, not silently ignored.

**What counts as reflected on, specified.** A week is reflected on once a check-in
row exists for that week start. The row is written when the check-in is offered, not
when it is answered, and the pain level and free text are both nullable. So a
check-in is offered at most once per completed week, and an athlete who ignores it
is not asked again for that week.

**Considered instead: write the row only when the athlete answers.** Rejected because
silence is explicitly allowed here, so an athlete who never answers would be asked
again on every plan request. That is the fastest way to train someone to ignore the
bot, which is the same argument the 2026-08-28 spec uses for not chasing missed
training (section 7.5).

**What it costs.** A week Marc genuinely meant to answer but tapped past is gone. He
cannot come back to it, and the row will sit there empty. Reopening a check-in is not
designed here and should not be invented in the build.

**Reversal named: the 2026-08-28 spec chose per-session feedback, not weekly.**
Section 6 decided to ask after each session, one tap, and gave a real reason: effort
only means something next to the specific session it belongs to, and a weekly summary
loses that.

The reversal stands on two grounds. The first is that the same section flags its own
risk: five prompts a week may become annoying, and if the athlete stops answering the
signal degrades silently. Nothing has been built yet, so no one has learned which way
that goes. The second is that the plan is weekly, so a weekly answer arrives already
shaped for the decision it feeds.

**What the reversal costs, stated honestly.** A weekly answer cannot tell you which
session hurt. "Running felt too easy" is a week-level statement, and if Marc had one
easy week and one hard session in it, the answer is ambiguous. Per-session effort
remains the better signal for fatigue detection, and section 6's argument for it is
not refuted here, only deferred. If the weekly question turns out to be too coarse,
adding per-session effort later is additive, not a rewrite.

**One thing must not happen: both surfaces asking about pain.** If per-session
feedback is built later, the pain question lives in exactly one of them.

### What we compute before asking anything

**Correction: most of this already exists.** The draft said we would run the fitness
calculation over the seven days that ended. True, and the window is already a
parameter, so no calculator change is needed. But the per-week, per-discipline totals
the check-in needs are also already computed, bucketed by Monday, in the athlete's
own timezone, by the workout-history dashboard
(`app/services/workout_history.py:128-167`). That is the aggregation to reuse.

So the computed half of the check-in is: sessions and minutes actually done per
discipline for the week, distance and pace where known, and planned against actual.

**Planned against actual is computable today** and needs nothing new. The stored plan
holds a duration for every session (`app/schemas/weekly_plans.py:17-24`), so planned
minutes per discipline is a sum over the stored plan, and actual minutes per
discipline is the bucket above. Note what it cannot do: plan sessions still have no
identity, so this says the week ran 40 minutes short on swimming, never which swim
was skipped. Session identity is section 7.1 of the older spec and is not built.

### The heart-rate signal, corrected

The draft said we would compute "this week's pace relative to heart rate, compared
against Marc's own recent baseline," from readings "good enough to trust, exactly as
already filtered elsewhere."

The filter is real. Only instant readings and readings covering 60 seconds or less
count as reliable, and only those feed the average and maximum
(`app/services/fitness/calculator.py:27-30`, `136-139`).

The problem is what reaches that filter. Marc logs by screenshot, which carries one
average and one maximum heart rate for the whole workout
(`app/schemas/manual_import.py:58-59`). A whole-workout average is exactly the coarse
kind the calculator excludes: it sets a flag saying coarse data is present and is
otherwise dropped (`app/services/fitness/service.py:40-43`). Fine-grained readings
exist only for Apple Health export and TCX imports, which is a file the athlete
uploads occasionally, not a weekly habit.

So the signal as drafted would be empty almost every week, and empty silently.

**Decided: use the athlete's own coarse averages for this comparison, clearly
labelled as coarse.** Comparing Marc's screenshot-reported average heart rate this
week against his own screenshot-reported averages over the previous weeks is
like-for-like. The product already shows this number to him in the history view
(`app/schemas/workout_history.py:61`), so it is not a new kind of trust.

The reliable aggregate stays as it is. This is a second, separately named number for
a narrower question, not a loosening of the calculator's standard.

**Considered instead: leave the signal out until real heart-rate data arrives.**
Honest, and it keeps one definition of trustworthy heart rate. Rejected because it
removes the only evidence of improvement the system can currently produce, and the
2026-08-28 spec already names that as the weakest point in the whole design (section
5.3). A labelled weak signal beats no signal, as long as the label travels with it.

**What it costs, and it is more than it looks.** Both the pace and the heart rate are
week-level aggregates across every session in that sport. If Marc swaps one easy run
for one hard run, the aggregate pace improves and the aggregate heart rate rises, and
the ratio moves for reasons that have nothing to do with fitness. The number reacts
to the shape of the week, not only to the athlete. It must reach the model as an
observation with that caveat attached, never as a verdict.

**Still undecided:** whether screenshot-reported average heart rate is consistent
enough across the apps athletes screenshot to compare week to week. Nobody has
looked. Until someone does, this signal is a hypothesis with a number attached.

### What we ask

Two separate things.

- **A one-tap question:** any pain or discomfort this week. None, mild, moderate,
  severe. This is the only part that feeds the hard rule that cuts load, so it has to
  be a plain unambiguous answer, not a sentence a model interprets in the moment.
- **An open question:** "How did this week feel? Anything worth calling out?" Free
  text, entirely optional.

**Correction: there is nowhere to store either.** The 2026-08-28 spec said the
feedback table and the twelve-step conversation enum already existed and were well
shaped (section 6). They do not exist now. Nothing in `backend/app/` references
`activity_feedback` or `WorkoutFlowStep`, and there is no such table in the model
file (`app/db/models.py`). So the check-in needs its own new table, and this is the
only new storage the check-in requires.

The smallest table that works: one row per athlete per completed week, holding the
week start, the pain level, the free text when given, and the computed comparison as
a JSON document. Keep it as history rather than consuming it once, so Marc's own
trajectory can be shown back to him later.

**Marc's week 4.** Pain tap: none. Free text: "Running felt too easy, I think I could
push harder." The computed numbers agree: his running pace at a similar average heart
rate was faster than his own recent weeks. Both go into stage 4's next prompt as they
are, with no extraction step in between:

> Athlete reported running felt too easy. Week-level running pace at a similar
> week-level average heart rate was faster than his recent weeks, which is consistent
> with that, though both numbers are coarse and mix easy and hard sessions. No pain
> reported.

**And the next plan reflects it**, in the model's own words: "Good sign that the easy
runs feel comfortable, that is the base building working as intended. Keeping two of
your three runs easy, but swapping the third for a steadier, more demanding session
so you keep being challenged without giving up the aerobic work the easy days are
doing."

**Silence is fine.** The computed numbers alone still reach stage 4. Nothing is lost
by skipping the questions, only by skipping the screenshot.

**What the free text costs.** It goes to the model uninterpreted, which is the right
call, and it means the athlete's words can steer the plan in ways no rule bounds. The
pain tap is separate precisely because that one thing must not be steerable.

---

## Stage 7 - Is the plan actually working

**Correction one: there is no background.** The draft said a check "runs in the
background every week, no model involved." Nothing in this system runs on a timer. So
the check runs at the start of a plan request, right after the check-in and before
the plan is generated. It is still cheap and still involves no model.

**Considered instead: add a scheduler.** A job queue would let the coach reach out
unprompted, which is a genuinely different product. Rejected as out of scope here: it
is a new runtime, a new failure mode, and a new class of message the athlete did not
ask for. Attaching to a plan request costs nothing and loses only the ability to
speak first.

**What that costs.** An athlete who stops asking for plans never gets checked on,
which is exactly the athlete most worth checking on. That is a real hole and this
design does not close it.

**Correction two: there is no fitness number.** The draft asked whether "the priority
sport's fitness number is moving up, flat, or down." No such number exists. The
calculator produces volume aggregates and a confidence score, and confidence measures
how good the data is, not how fit the athlete is
(`app/services/fitness/calculator.py:25`, `428`). The 2026-08-28 spec says this
plainly: there is currently no honest way to say whether the athlete is improving,
only whether they are doing more (section 5.3).

So the check uses two inputs, both computable today.

1. **Adherence**, per discipline: actual minutes divided by planned minutes, over
   completed weeks that had a plan. Both halves are defined in stage 6. Weeks with no
   plan, or with zero planned minutes for that discipline, are skipped rather than
   counted as zero.
2. **The coarse pace-at-heart-rate comparison** from stage 6, carrying its caveat.

The second is a weak signal and is used only to soften the message, never to trigger
it. Adherence alone decides whether anything is said.

**The trigger:** the priority discipline's adherence has been below 70 percent for
two consecutive completed weeks that had plans.

**These two numbers are guesses. Label them as guesses in the code.** Seventy percent
and two weeks are not derived from anything. They were picked because 70 percent is a
clearly missed week rather than a slightly light one, and because one bad week is
noise while two is a pattern. Nobody has seen a single week of real adherence data.
Both numbers live in configuration, in one place, and the first thing to do once real
data exists is look at what adherence actually distributes like before defending
either.

**Reversal named: the 2026-08-28 spec said there is only one threshold.** Section 7.6
says explicitly that with the ask-why conversation deferred, "only the 15 percent
drift proposal exists. There is no second threshold and no escalation to manage."
This adds a second one.

They govern different horizons and should be built as one escalating idea, not two
independent ones. The 15 percent drift check watches the week that is running now and
proposes a change to it. This one watches completed weeks and questions whether the
block is right. Written as separate mechanisms with separate numbers, they will drift
apart and produce two messages about the same problem in the same week. Whoever
builds the second one should read the first one's code and make the relationship
explicit.

**If nothing looks wrong:** nothing happens and nothing is sent.

**If the trigger fires**, one model call writes one message:

> "Swimming has been light the last two weeks. Want me to ease that part of the plan
> back and take another look in a few weeks, or keep pushing as is?"

Marc taps accept or decline. Accepting is what sends stage 3 back to run again with
fresh evidence. The macrocycle never changes on its own, which is section 7.4's rule
applied to this horizon.

**Note what does not exist yet:** there is no accept-or-decline proposal mechanism in
the bot today. Inline keyboards are everywhere in onboarding
(`app/bot/keyboards.py`), so the pieces are there, but the draft's "the same way he
would for any other proposal" describes a pattern that has not been built once.

**What this stage costs.** It only ever notices things going badly. Marc could take
up swimming with enthusiasm, improve fast, and outgrow a macrocycle that still calls
swimming his limiter, and nothing here would say a word. A check that fires only on
failure leaves stale-but-succeeding plans running indefinitely.

---

## Coaching style, in practice

The same week looks different depending on the one setting Marc chose at onboarding.

| | Conservative | Normal | Demanding |
|---|---|---|---|
| How far a week may jump over last week's actual load | +30% | +50% | +70% |
| How often a lighter week is suggested | every 3 weeks | every 4 weeks | every 5 weeks |

Reported pain always cuts load, at every setting. That rule does not move.

**Where the numbers come from, honestly.** Only the middle column is anchored. Fifty
percent is the safety net the 2026-08-28 spec already decided (section 4.6, "an
increase beyond roughly half again"), so normal inherits it. The 30 and 70 are twenty
points either side, chosen for symmetry and nothing else. Twenty is a guess and
should be labelled as one in configuration.

The light-week cadence numbers are weaker still. Three, four, and five weeks are
common convention in endurance coaching and are not derived from anything about this
athlete.

**The cadence row also sits badly against a decision already made.** Section 4.6 says
periodisation is judgement and the model owns it, and section 4.7 has the model state
each week's intent. A code rule that inserts a light week every fourth week is
exactly the "week roles and sequences" the older spec considered and rejected, and
section 10 lists it as out of scope.

**Resolved: the cadence is a hint in the prompt, not a rule in code.** The prompt says
how many weeks it has been since the last lighter week and what cadence this athlete's
style suggests. The model decides. Nothing rejects a plan for having the wrong
cadence.

**What that costs.** A hint can be ignored, so an athlete on conservative may go six
weeks without a lighter week and nothing will stop it. The alternative, a hard rule,
was rejected twice for good reasons, so this is the price of consistency with the rest
of the design rather than an oversight.

**Removed from the draft: the "introducing a new sport" row.** It read "very
gradually / steadily / steadily, slightly faster," which is not implementable. Two
engineers would build three different things from it, and none of them would differ
from what the load ceiling already does. If introducing a new sport needs its own
rule, that rule has to name a number.

---

## Session-count targets, in practice

Marc asked for running 3, cycling 2, swimming 3. The feasibility check at onboarding
confirmed those fit his available days.

**How the target reaches the plan:** as a line in the prompt saying to aim for these
counts and not to force them.

**Decided: the quality gate does not check session counts.** A plan that delivers two
swims instead of three is not wrong. It may be exactly right in a week where Marc
reported pain, or where the macrocycle called for a lighter block.

**Considered instead: enforce the counts as a check.** Rejected because it would turn
a preference into a constraint, and it would fight the coaching-style ceiling and the
pain rule in weeks where those two say to do less. When two rules disagree about the
same week, the safety one must win, and the simplest way to guarantee that is not to
create the second rule.

**What it costs.** Marc can ask for three swims a week and quietly get two, week
after week, with nothing telling him it is happening. That is the honest trade for
not forcing it, and it argues for surfacing requested against planned counts
somewhere the athlete can see, rather than for making it a hard rule.

---

## Where each new piece lives

- **Coaching style** and **desired sessions per discipline**: two fields on the
  athlete profile, asked together at onboarding, editable through the existing
  profile settings flow. One tunes intensity, the other tunes frequency.
- **The macrocycle**: one row per athlete holding the priority discipline, the
  one-sentence reason, any stated deviation from the default block structure, and
  what it was built from. Replaced when the goal changes or stage 7's offer is
  accepted. The phase is not stored, because it is computed from the race date every
  time.
- **The weekly plan**: unchanged in shape, now also reading the macrocycle, the
  computed phase, the coaching style, the desired counts, and the previous check-in.
  Every one of those goes into the input fingerprint.
- **The quality gate verdict**: columns on the plan row it judged.
- **The weekly check-in**: a new table, one row per athlete per completed week,
  holding the pain level, the free text when given, and the computed comparison. This
  is genuinely new storage, because the table the 2026-08-28 spec assumed no longer
  exists.
- **The progress check**: one row each time it runs, for the same reason.

---

## Still genuinely undecided

Listed rather than invented.

1. **What a macrocycle means with no race date.** The phase is `GENERAL` and the
   block structure question is meaningless. A priority discipline may still help.
   Nobody has worked this through.
2. **Whether screenshot-reported average heart rate is comparable week to week.**
   Different apps report it differently. Until someone looks at real screenshots, the
   pace-at-heart-rate signal is a hypothesis.
3. **The 70 percent and two-week numbers**, and the 20-point spread on the coaching
   style ceiling. Guesses, labelled as such, to be revisited against real adherence
   data.
4. **Whether the weekly check-in and per-session effort coexist long term.** This
   document defers per-session effort and does not refute the 2026-08-28 spec's
   argument for it.
5. **How the two adherence thresholds relate.** The 15 percent mid-week drift
   proposal and this document's 70 percent block-level check should be one escalating
   mechanism, and neither is built yet, so the shape of that is still open.
6. **How much of stage 5's critic fires in practice.** Unknown until it runs against
   a real model. If it flags most weeks, the design is wrong, not just the prompt.
7. **What happens to a macrocycle when the athlete goes quiet.** Marc could reach
   `WELL_EVIDENCED`, get a macrocycle, stop training for six weeks, and drop back to
   `THIN` everywhere. The stored priority then rests on evidence that no longer
   exists. Stage 7 will not catch it, because there were no plans to measure
   adherence against. Neither the trigger nor the invalidation rule in stage 3 covers
   this, and a rule invented here would be a guess.
