---
status: accepted
---

# Keep the go/no-go gate deterministic; AI agents are research-only

The desk's twelve orchestrator stages behind the go/no-go decision remain
deterministic scripts. LLM-backed agents (News, ResearchFleet and the other
fleet tasks) may gather evidence and explain or propose, but they never produce
the `proceed`/`review`/`halt` verdict, never change a gate threshold, and never
place an order. We chose this on 2026-11-22 after the user asked for "all agents
to be AI agents": a non-deterministic model in the gate would make the same
inputs produce different verdicts, which breaks the fail-closed guarantee and
the auditable proof matrix. The cost is that the gate stages are not themselves
LLM agents; the benefit is that the safety contract stays testable and stable.

## Considered options

- **Make the gate stages LLM agents** — rejected: a model's output cannot be
  relied on to fail closed identically on repeat, and the decision could no
  longer be replayed from artifacts alone.
- **Keep the gate deterministic and add AI as a research layer** — accepted:
  AI adds explanations and proposals; the gate consumes only sourced artifacts.

## Consequences

- A future reader should not "upgrade" a gate stage to an LLM call. Changing
  this decision requires a new ADR and a full safety review.
- New AI capabilities belong in the research layer (read-only artifacts and
  viewer tabs), not in `scripts/desk_orchestrator.py` or the stage registry.
