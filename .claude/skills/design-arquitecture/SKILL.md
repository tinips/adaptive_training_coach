---
name: multi-agent-design-walkthrough
description: Use this skill whenever the user is designing, reviewing, or trying to understand a multi-agent or multi-LLM-call implementation — a workflow with several Claude/LLM steps or agents passing work to each other. Trigger this when the user describes an agent pipeline and wants help planning it, when they say a design feels too complex or they're "missing details," when they ask to understand each step/agent one at a time, when they ask about edge cases in an agent workflow, or specifically when they ask how context or data is passed between LLM calls or agents. Applies both to building a brand-new multi-agent design from scratch and to walking through/reviewing a design the user already has (pasted in, described verbally, or referenced from a prior chat). Do NOT use this for single-LLM-call tasks with no agent handoffs, or for general coding help unrelated to agent architecture.
---

# Multi-Agent Design Walkthrough

Helps the user design or understand a multi-agent / multi-LLM-call implementation, one agent (or one step) at a time, never all at once.

## Core rule: one agent at a time, wait for confirmation

Never dump the full pipeline design or a full explanation of every agent in one response. Go agent-by-agent (or step-by-step if the user's workflow isn't agent-based but has multiple sequential LLM calls):

1. Cover ONE agent/step fully (see "What to cover" below).
2. End that response with a short question checking the user actually followed it and agrees with it — not a generic "does this make sense?" but something concrete, like confirming the exact handoff data or asking whether an edge case actually applies to their system.
3. Only move to the next agent after the user responds. Do not pre-summarize what's coming next in a way that jumps ahead of their pace.

If the user tries to get the whole design in one go ("just give me the whole thing"), it's fine to comply, but flag briefly that going step by step is available if they get lost, and don't fully abandon the structure — still separate agents visually (headers, dividers) even in a single response.

## Keep a pipeline anchor before diving into any single step

Before designing or walking through step 1 in detail, make sure a short written anchor exists: one paragraph (a few sentences) stating the end goal of the whole pipeline and the rough list of steps/agents involved, even if some of those later steps are still fuzzy. This is not optional context, it's the reference point every individual step gets designed against.

- If the user hasn't given you one yet, ask for it or draft one yourself from what they've described, and confirm it with them before going further.
- Keep this anchor visible/referenced throughout the conversation — when designing step 1, explicitly design it with the later steps' likely needs in mind, not in isolation.
- If a repo/codebase already exists, check it for an existing overview (README, design doc, comments) before asking the user to restate it.

## Revisit earlier steps when a later one reveals a gap

Once a step has been designed and tested, it's not permanently frozen. If, while working on a later step, it becomes clear an earlier step's schema/format/logic is missing something the later step needs:

1. Say so explicitly — name exactly what's missing and which earlier step it affects.
2. Propose the adjustment to the earlier step before continuing forward.
3. Get the user's confirmation on the adjustment, then resume forward progress.

This is expected, normal iteration, not a failure of the design. Don't silently patch around a gap in a later step (e.g., adding a workaround) when the actual fix belongs in an earlier step's design.

## Building new vs. reviewing existing

**If the user has an existing design** (pasted, described, or in a prior conversation they reference): don't redesign it. Restate their design back to them, agent by agent, in this format, and flag gaps or vague spots as you go rather than silently filling them in.

**If the user is building from scratch**: before designing anything, get the essentials —
- What's the end goal / final output of the whole pipeline?
- What are the rough stages they're already thinking of, even if fuzzy?
- Any constraints: latency, cost, specific model per agent, existing tools/APIs involved?

Don't ask this as a wall of questions — use the ask_user_input tool if available, or ask 1-2 at a time in prose. Then design one agent at a time using the same "what to cover" checklist, proposing a step, getting confirmation or edits, then moving to the next.

## What to cover for every single agent/step (no exceptions)

Each agent's explanation must include all four of these — this is the user's explicit standing requirement, not optional detail:

### 1. Role
One or two sentences: what does this agent actually do, in plain terms.

### 2. Exact input format
What does this agent receive? Be literal, not vague:
- Is it the full upstream conversation/output, or a filtered/summarized version?
- What's the actual shape — a JSON object with named fields, a plain text blob, a structured prompt template?
- If you don't know yet (early design phase), say so explicitly and propose a candidate shape rather than gesturing at "the context."

### 3. Exact output format / handoff to the next agent
Same standard as above, but for what this agent produces and hands forward. Never write "passes the result to the next agent" without specifying the literal fields or structure of that result. If the next agent only needs part of this agent's output, say which part.

### 4. Edge cases and failure modes for this step
List concretely, for this specific agent, not generically:
- What if the input is missing, empty, or malformed?
- What if this agent's output doesn't match the expected format the next agent needs?
- What if the agent's answer is low-confidence, contradicts an earlier agent, or times out?
- Any domain-specific failure mode obvious from what this agent does.

### 5. Why this design, briefly
One short note on why this is structured this way rather than an alternative (e.g., why a separate agent instead of folding this into the previous one, why summarize instead of passing full context). Keep this tight — a sentence or two, not a debate. If there's a real tradeoff, name it, but don't manufacture one if the choice is obvious.

## Formatting

Use a clear header per agent (e.g. `## Agent 2: Extraction`), keep the five subsections short and scannable, and avoid restating the whole pipeline diagram every time — the user has already seen the overview once at the start.

## What NOT to do

- Don't explain two agents in one turn, even if related.
- Don't say "context is passed to the next step" without literally specifying the data shape.
- Don't invent edge cases generically ("network errors," "rate limits") if there's a more specific failure mode obvious from what this particular agent does — cover the specific one, generic infra failures are secondary.
- Don't silently redesign an existing design the user is trying to understand — flag issues, don't fix them without asking.