---
name: architecture-walkthroughs
description: Use for every answer about this training coach repository, including short factual lookups. Every claim is labelled built, broken, designed, or expected, because most of this system is designed and not built. When the answer has steps - what happens when an athlete does something, why the system responds that way, whether a step should be code or a model, how a change would play out - walk one concrete athlete through the pipeline and give every step a cause and a verdict.
---

# Architecture Walkthroughs

## Overview

The hardest thing to get right in this repository is not the explanation. It is
knowing which parts of the explanation are true.

Most of this system is designed and not built. The weekly planner has been called
against a live model roughly three times, in one experiment. So a sentence like
"the coach goes easy on a new athlete" sounds like a description and is actually a
prediction. A review of the multi-horizon design found six claims of exactly that
shape, each of which had been repeated across drafts until it read as fact.

This skill exists to make that impossible. Every step in an explanation carries a
label saying what kind of claim it is, and every step says why it happens and
whether it should stay that way.

**Precedence, because two skills cover every answer here.** This skill sets the
structure: what a claim must be labelled, what a step contains, and what a verdict
has to say. `plain-language-answers` sets the prose: meaning before mechanism, no
identifier as the subject of a sentence, short clauses, common words, evidence last
and small. Where they seem to conflict, the prose rules apply inside each step, and
the structure rules decide what the steps are. Neither skill overrides the other,
because they are not about the same thing.

The style rules are not restated below. They still apply to every line.

## The four labels

Every step gets exactly one. This is the part that must never be skipped.

| Label | Means | Needs |
|---|---|---|
| **Built** | The code does this today, and I checked | File and line |
| **Broken** | The code does this today, and it is wrong | File and line, plus what goes wrong |
| **Designed** | A spec decided it. No code does it | Which spec and section |
| **Expected** | What we think a model will do. Nobody has measured it | Nothing. Say plainly that it is unmeasured |

**Built means I opened the file in this conversation.** Not that I remember it, not
that a design document says so, and not that it was true last month. This codebase
changes fast: a mobile sync path, a feedback table and a whole catalog-expansion
system have all been built and then deleted. If it is not checked, it is not Built.

**Expected is the label people forget.** Anything that depends on how a language
model responds is Expected, however confident the prompt is. A prompt that says "do
not prescribe HARD intensity" makes the instruction Built. It does not make the
model's obedience Built.

## The shape of one step

Four lines, in this order, every time.

1. **What happens.** One plain sentence, in athlete terms where possible.
2. **Why it happens.** The specific input or rule that caused it.
3. **The label**, with its evidence.
4. **The verdict.** Keep it or change it, and what the alternative is.

### "Why" means a cause, not a motive

This is the line that makes a walkthrough worth reading, and the easiest one to
write badly.

A motive is vague and unfalsifiable: "it goes easy because the athlete is new."

A cause is specific and checkable: "it goes easy because swimming has no workouts in
the 30-day window and the athlete filled in the swimming form, so the state is
`SELF_REPORTED`, and the system prompt tells the model that state means no hard
sessions and duration ranges only."

Always name the input, the field, or the prompt line that produced the behaviour. If
you cannot name one, the step is Expected and you should say so.

### The verdict is required even when nothing is wrong

A step that is right still gets a verdict, and the verdict must name the alternative
that lost. "This is fine" is filler. "Keep it. The alternative was requiring real
workouts before planning, which is what the gate used to do, and it refused every
triathlete with a weak swim" is a verdict.

Three standing tests for the verdict, taken from decisions this repository has
already made:

- **Arithmetic or judgement?** Arithmetic belongs in code, so it cannot drift and the
  athlete can check it. Judgement about a specific athlete in a specific week belongs
  in the model. This is the split in section 4.9 of the 2026-08-28 design.
- **Is it a safety bound?** Safety bounds belong in code, always, and they run before
  the model is called. A model asked to be encouraging will find a reason to keep the
  long run.
- **Is the model doing something a rule could do exactly?** Then it is a model call
  that will sometimes be wrong for no benefit. Say so, and name the rule.

## A worked step sequence

Marc has just finished onboarding. He has never swum. He asks for a plan.

**The gate lets him through with zero workouts.**
Why: readiness is true when the athlete has filled in any self-reported baseline,
whatever the workout count.
Built (`app/services/weekly_planning/evidence.py:80-89`).
Verdict: keep. The alternative was demanding real evidence in every target sport,
which is what this gate used to do, and a triathlete with a weak swim got no plan at
all, including for the running that was ready.

**Swimming is labelled `SELF_REPORTED`.**
Why: the calculator finds no swims in the window, so it returns nothing at all rather
than a zero (`app/services/fitness/calculator.py:64-66`), and Marc filled the swimming
part of the baseline form. Those two together select this label
(`app/services/weekly_planning/evidence.py:23-28`).
Built.
Verdict: keep. This is arithmetic over rows, which is exactly what code should own.
One dead branch worth knowing: the same condition also tests for a session count of
zero, and that half can never fire, because the schema requires the count to be
greater than zero for a calculation to exist at all
(`app/schemas/fitness.py:59`). Harmless, and a good example of why a causal sentence
has to name the branch that actually ran.

**The model is told what that state means.**
Why: the system prompt spells it out. No hard intensity, duration ranges rather than
exact pace or power, and if stated volume is zero, one short introductory session
rather than zero treated as a target.
Built (`app/workflows/prompts/weekly_planning.py:28-36`).
Verdict: keep the rule, but note what it is. This is an instruction, not an
enforcement. A deterministic check that rejects a hard session for a sport in this
state is decided in section 4.5 of the 2026-08-28 design and does not exist.

**The plan comes back easy.**
Why: because the prompt asked for it.
Expected. Nobody has measured this. The planner has been called against a live model
about three times, in one experiment, and none of those runs checked whether the
sessions were actually easy.
Verdict: this is the step that needs the deterministic backstop named above. Telling
a model to go easy is not the same as it going easy, and until a test reads the
generated plan and asserts the session is short and easy, this whole step is a hope.

Notice where the label flips. The first three steps are facts. The fourth, which is
the one everybody states most confidently, is the only one nobody has checked.

## Choosing what to walk through

One walkthrough covers one athlete through one pipeline. If a question spans two,
walk the one asked about and name the other in a sentence.

When picking a scenario to illustrate a design, prefer the ones that stress different
paths. These seven cover most of what this system has to survive, and the first two
are requirements written into section 1.1 of the 2026-08-28 design:

- An athlete with one target sport, not three.
- An athlete with no race date.
- Cold start: onboarding done, nothing logged.
- The athlete who follows the plan.
- The athlete who logs nothing for three weeks.
- The athlete who reports pain mid-week.
- The athlete whose goal is out of reach from where they are.

A design that only ever gets walked through with the three-sport triathlete will be
wrong for everyone else, and this repository has made that mistake before.

## When there is no sequence to walk

Not every question has steps. A question with one factual answer gets the answer, in
one or two sentences, with its label. Do not build a walkthrough around a single
fact.

The labelling still applies. "The planner window is 30 days, Built
(`app/config.py:81`)" is a complete answer. "The planner window is 30 days" is not,
because the reader cannot tell whether that is the code or the plan.

## Before sending

Read only the label column. If every step says Built, check again: in this repository
that is almost always wrong, and it usually means a Designed step got promoted by
being repeated.

Then read only the verdict lines. If they all say keep, the walkthrough has not
earned its length.

## Common mistakes

| Mistake | Fix |
|---|---|
| "The coach goes easy for a new athlete" | Split it: the prompt says to (Built), the model doing it (Expected) |
| A step labelled Built from memory or from a design doc | Open the file. Design documents in this repo go stale within weeks |
| "Why: to keep the athlete safe" | That is a motive. Name the state, the field or the prompt line |
| Verdict says "this is correct" | Name the alternative that lost, or drop the verdict line |
| A defect labelled Built because the code runs | Broken. Code that runs and does the wrong thing is the worst thing to mislabel |
| Walking the happy path only | Pick a scenario from the list above that breaks something |
| A full walkthrough for a one-line question | Answer it in one line, with a label |
