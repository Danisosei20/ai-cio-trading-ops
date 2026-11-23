# Short-basket signal — design

**Intent:** Give the desk a read-only proposal for a $500, five-name short basket, ranked by weakness from the desk's own locally stored bars. It is a signal for a human to review, not an order.

**Why:** The user asked for the desk to "trade like" a hindsight short basket. Coding the hindsight picks directly would overfit one week. A rule that ranks the weakest names from point-in-time artifacts is reproducible and can be validated out-of-sample.

## Goals

- Rank candidate symbols by recent price weakness using only locally stored bars.
- Propose five shorts sized to a $500 total, with entry reference price, shares, notional and source shown.
- Fail closed: no bars, malformed bars or too few sessions → explicit unavailable state.
- Show it in the viewer as a clearly labelled signal-only panel.

## Non-goals

- No order placement, no broker call, no write endpoint.
- No change to the go/no-go gate, risk controller, scheduler or stage agents.
- No claim that the rule is profitable — it must be validated out-of-sample before any live use.

## Constraints

- Paper-only posture unchanged; viewer remains loopback GET-only.
- Every value sourced from a real artifact; source displayed verbatim.
- Signal uses only bars from completed sessions before the proposal — no look-ahead.
- Reuse existing dark tokens; no new accent color; no animation loops.
- Python 3.11 stdlib, `unittest`, vanilla HTML/CSS/JS.

## Decisions

- Signal = percentage change from the first session's opening close to the last session's final close in the latest `outputs/orchestrator/*/bars_*.json` set. Weakest (most negative) ranks first.
- Latest bar run chosen by modification time, not filename order.
- Symbols with fewer than two distinct sessions, missing/non-finite prices or empty bars are skipped.
- Equal-weight allocation: `capital / count` per name, fractional shares allowed (paper sizing).
- The proposal is labelled "signal only — no orders placed" in the UI.

## Validation before live use

An out-of-sample check must run walk-forward over multiple non-overlapping windows before the rule influences any order path. Results belong in the report, not in the UI.

## What is left to the builder

Exact payload keys, DOM ids and tests are in the implementation plan.
