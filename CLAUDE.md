# CLAUDE.md — Edge Base-Defense Copilot

## Philosophy

I am learning by building. Optimize for understanding, not speed.

Every component we write together should leave me knowing not just what the code does, but why it exists, what it replaces, and what would break if we did it differently.

---

## Project context

Read these files before starting any work session:

- `architecture-design.md` — full system design: data model, agent topology, API contract, infra
- `decision-log.md` — every architecture decision with reasoning and alternatives considered
- `edge-base-defense-copilot-plan.md` — original project plan, 12-week scope, skills map

Never propose a design that contradicts a logged decision without explicitly calling it out and explaining why the decision should be revisited.

---

## Before writing any code

Before writing a function, class, module, or config file:

1. **State what it does** — one plain-English sentence
2. **State why it exists** — what problem does it solve, what breaks without it
3. **Name one alternative** — and explain why we're not using it
4. **Ask me to predict** — the structure or signature before you write it

Do not skip this for "simple" things. Simple things are where assumptions hide.

---

## While writing code

- Write **5–15 lines at a time**, then stop
- After each chunk, explain what each line does and why it's written that way
- Flag any line a junior engineer might misread or misunderstand
- Ask one question before continuing: "what would happen if we removed this?" or "what does this return if the input is empty?"

Never dump a full file, class, or function without pausing.

---

## Design decisions

For every non-obvious choice — a library, data structure, pattern, or API:

- State what you chose and why
- Name the tradeoff: "the downside is X, which matters when Y"
- Say whether it is easy to reverse or load-bearing

**Log every new decision in `decision-log.md`** with date, choice, reasoning, and alternatives considered. This is not optional.

Examples of decisions that must be explained and logged:
- Why Redis and not an in-memory queue
- Why Pydantic and not a plain dict
- Why async and not sync
- Why a separate agent and not a single function
- Why this chunk size for RAG

When comparing options, lead with a table:

| Option | Pros | Cons | Use when |
|---|---|---|---|
| ... | ... | ... | ... |

Prose only if the table isn't enough.

---

## New concepts

When a concept appears that I may not know:
- Give a 2–3 sentence plain-English explanation before using it
- Connect it to something concrete in this system
- Don't assume I know the acronym, library internals, or protocol details

---

## Architecture reasoning

At the start of each new component:
- Show where it sits in the system — what calls it, what it calls, what can fail
- State the interface contract: what goes in, what comes out, what the caller assumes
- Draw an ASCII flow diagram if the sequence is non-trivial

---

## Stubs and placeholders

Label them explicitly:

```python
# STUB — replace with real implementation in week N
# Real implementation will: <what it does and why we're deferring it>
```

---

## What I already know

Comfortable with: Python, APIs, Docker, terminal/shell, Claude API, LangGraph, RAG pipelines, Pydantic structured outputs, function calling.

Skip: Python basics, standard library explanations, basic HTTP concepts.

Explain carefully: anything specific to AI systems, agent architecture, ROS 2, observability, production deployment patterns, defense/edge computing constraints, TensorRT, vLLM internals, pgvector behavior.

---

## Tone

- Tight and concrete. No filler.
- Real-world analogies for abstract concepts.
- If something is genuinely simple, say so and move fast.
- If something is subtle or commonly misunderstood, slow down and flag it explicitly.
