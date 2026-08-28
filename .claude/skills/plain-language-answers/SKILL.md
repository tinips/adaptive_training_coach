---
name: plain-language-answers
description: Use when writing any user-facing answer, finding, recommendation, or design discussion in this repository, especially when reporting what code does or why something is broken.
---

# Plain Language Answers

## Overview

Albert reads and writes this codebase, so the substance can stay technical. What
does not work is technical *shorthand*: identifiers used as nouns, file:line
citations doing the work of sentences, and raw tool output pasted as evidence.

**Core principle: keep the precision, drop the compression.** Plain does not mean
vague, softer, or shorter on findings. It means the meaning arrives before the
machinery.

## The Recipe

Write every finding in this order. The order is the skill.

1. **The point, in one ordinary sentence.** What is true, and what it means for
   the athlete or the product. A person who has not read the file should follow it.
2. **The mechanism, in words.** Describe behaviour, not identifiers. Say what the
   code *does*, as if narrating it to someone looking over your shoulder.
3. **A concrete consequence**, when the rule has a surprising effect. Use a real
   example with real numbers.
4. **The evidence, last and small.** File and line in parentheses at the end of
   the sentence it supports.

## Rules That Hold Everywhere

- **Never let a code identifier be the subject of a sentence.** Write "the gate
  only opens when every sport qualifies", not "`PlanReadiness.ready` is `all(...)`".
- **Gloss each identifier the first time it appears in a message**, then prefer
  the plain name for the rest of that message.
- **Numbers carry units and a verdict.** "It tells the coach you ride at 0 km/h,
  which is false" beats "`elapsed_speed_kph=0.0`".
- **Summarize tool output; do not paste it.** Paste only when Albert asked to see
  it, or when the exact text is the finding.
- **One idea per paragraph.** Short paragraphs, plenty of them.
- **Tables compare things.** They do not dump structure.
- **English is a second language here.** Prefer common words and short clauses.
  Avoid idioms, stacked subordinate clauses, and sentences over about 25 words.

## Keep The Substance

Plainness is a style change, never a content change.

- Do not drop findings to make the answer shorter.
- Do not soften a real defect into "something to look at".
- Do not remove a recommendation or a disagreement. Stress-testing still applies.
- Do not hide uncertainty. Say "I have not checked this" in those words.

## Before Sending

Read the first sentence of each section alone. If those sentences by themselves
tell Albert what he needs to know and decide, the answer is in the right shape.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Opening with a file path or a symbol name | Open with what happens and who it affects |
| A table whose columns are code artifacts | A table whose columns are decisions or trade-offs |
| Three findings compressed into one dense paragraph | Three short paragraphs, one each |
| "The `all()` semantics veto the plan" | "One weak sport blocks the plan for the strong ones" |
| Pasting a query result as proof | State what it showed, cite where it came from |
