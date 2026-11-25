# Signal Expiration — Reviewable Specification (v1.0.0 companion)

Status: **shadow classification specification**. It introduces no new trading
permission, no new threshold and no change to live eligibility. Adopted constants
are quoted from existing code. Any operational adoption requires a spec version
bump, replay/out-of-sample evidence and owner approval.

## 1. Gap

The desk has no per-setup expiration contract. The time-bound rules that exist
are:

| Rule | Value | Source |
|---|---|---|
| Evidence freshness (live) | 300 s | `robinhood_tools/signal_freshness.py:81-86,126-128` |
| Bar staleness (5 m bars) | `max(15, 4 × interval)` = 20 min | `robinhood_tools/bar_validation.py:103-104` |
| No new entries | 15:15 ET | `scripts/daytrade_engine.py:51,87-92,648-651` |
| Flatten | 15:45 ET | `scripts/daytrade_engine.py:52,79-84` |
| Opening range / trust | first 6 completed bars; ≥12 bars | `scripts/daytrade_scout.py:38-39,152-156` |
| Parent / child authorization TTL | 8 h / 300 s | `scripts/execution_handoff.py:105`, `robinhood_tools/authorization_propagation.py:18,68` |

Replay-valid never implies live-eligible; post-close is analysis only.

## 2. Definitions

- `t_bar` — timestamp of the last **completed validated** 5-minute bar.
- `t_asof` — market-as-of / price-as-of quote backing the signal.
- `t_det` — signal detection time.
- `t_dec` — research decision time.
- `F = 300 s`, `B = 20 min`, `CUT = 15:15 ET`, `FLAT = 15:45 ET`.

## 3. Expiration rule (formalization of existing rules)

A signal is expired for a live entry at `t_dec` when any of:

1. `t_asof` missing, malformed, negative age, or `t_dec - t_asof > F`.
2. `t_dec - t_bar > B`.
3. In replay mode, `t_retrieved - t_asof > F`.
4. `t_asof` outside a regular or early-close session, feed/capability unsupported,
   or bars incomplete, duplicated, gapped or future-dated.
5. `t_dec > CUT` for a new entry — reported separately as a session cutoff, not
   as signal expiration.

Missing evidence fails closed; never estimate, interpolate or infer an expiry.

## 4. Classification (first match wins)

1. **TECHNICALLY_INELIGIBLE** — setup not in the approved whitelist, bars fail
   validation, feed/capability unsupported, future-dated quote, or a downstream
   zone/odds/risk/short-eligibility gate rejects.
2. **SESSION_CUTOFF_EXCEEDED** — `t_dec > 15:15 ET` for a new entry.
3. **INSUFFICIENT_EVIDENCE** — any required timestamp or evidence is absent.
4. **EXPIRED_BEFORE_DECISION** — evidence present and rules 1–4 above fail.
5. **ACTIONABLE_AT_DECISION** — all evidence present, technically eligible,
   within freshness, before cutoff, authorization valid if applicable.

`ACTIONABLE_AT_DECISION` is the only class that may be counted as an actionable
opportunity; a decision before 15:15 whose own signal expired cannot be counted.

## 5. Current 32 observations

Applying this to `docs/certification/phase-8-8-signal-latency.json`: 1
`SESSION_CUTOFF_EXCEEDED` (AMZN, 15:17:31 ET), 31 `INSUFFICIENT_EVIDENCE`,
0 `ACTIONABLE_AT_DECISION`. This is an evidence limitation, not a claim that the
signals were bad.
