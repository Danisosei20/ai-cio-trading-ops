# LLM fleet stations — design

**Intent:** Make all six Office fleet stations (Screen, Research, News, Liquid, Verify, Report) genuinely LLM-backed, so the "AI agents working together" shown in the viewer reflect real model reasoning rather than a deterministic script wearing an agent label.

**Constraint:** ADR 0001 stands — this is the research layer only. No station produces the go/no-go verdict or places an order.

## Goals

- Each station records an LLM-produced artifact with its reasoning and sources.
- Research and News already call `tradingagents_research.py`; keep them.
- Screen, Liquid, Verify and Report gain an LLM interpretation step over their real data, without replacing that data with model-generated numbers.
- Fail closed: no data, no key, or an LLM failure → an explicit error artifact, never invented analysis.

## Non-goals

- No change to the twelve orchestrator stages, the gate, or the order path.
- No model-generated prices, scores, or portfolio numbers.
- No new viewer tab; the Office stations already exist.

## Decisions

- Shared helper `fleet_llm(root, station, evidence)` in `scripts/fleet_llm.py`: builds a station-specific prompt, calls NVIDIA NIM (`nvidia/nemotron-3.5-lightning-30b-a3b`, key from `../TradingAgents/.env` or env), parses `{"analysis": str, "confidence": "low|medium|high", "cited": [int]}`. It emits `citations: [{index, label}]` (validated against evidence) and `sources: [artifact path]`.
- Evidence is real desk data: screener ranked rows, liquid signals, verify checks, and the fleet results dict.
- Citations are validated against the numbered evidence; invalid indices dropped.
- Each station writes `outputs/fleet_llm/<date>/<station>.json` and includes the path in its fleet result.
- The fleet's `office-state.json` continues to reflect task status; the viewer needs no change.

## What is left to the builder

Exact signatures and tests are in the implementation plan.
