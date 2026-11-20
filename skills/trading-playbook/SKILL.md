---
name: trading-playbook
description: Use when the desk operator must screen or rank candidates, recommend, enter, manage, or exit any option or stock trade (Alpaca paper or approval-gated live), pick option contracts/expiries, set quantities within desk caps, choose whether to chase or re-stage an order, or explain a trade decision. Also use when a requested contract/expiry conflicts with a scouting default or a gate.
---

# Trading Playbook

Day-trader posture, paper-desk execution. Preserve capital first; the most common correct decision is **NO TRADE**. This playbook encodes what the desk learned running real paper cycles — follow it exactly, do not improvise rules.

## Owner-Directed Contracts Win

If the owner names an exact contract (e.g. "CRWD 275C, Oct 16") or a horizon ("~7 days"), trade **THAT** contract — not the scout's alternative. The scout (`options_pilot.py` → `pick_long_option`) optimizes for a generic policy and often selects a different expiry/strike:

- Default scout pick: nearest liquid chain in DTE 7–60, delta ≈ 0.30–0.60 (sweet spot ~0.45), vol ≥ 100, OI ≥ 500, spread ≤ 10%. In practice it lands **~42 DTE (monthly)** with delta ~0.47.
- Near-dated contracts (e.g. 5–7 DTE) are **rejected by scout liquidity filters**, not by desk policy. A named 7-day contract is a legitimate owner-directed order that bypasses only the scout's DTE/liquidity ranking — **all other gates still run** (risk review, premium cap, earnings, universe, position cap, session window).

Place owner-directed contracts through `place_long_option` with the exact OCC symbol (e.g. `CRWD261016C00275000`) so review → risk engine → approval → ledger → guarded placement all still apply. Never silently upgrade a named strike or expiry to fit a delta target. If a hard gate truly blocks it (position cap, premium cap, earnings blackout, universe, session), **say so explicitly and offer options** — never substitute a different contract and call it the order.

## Desk Rules (quick reference)

| Rule | Value |
|---|---|
| Venue | Alpaca paper by default; live Robinhood only with explicit per-trade owner approval |
| Instruments | Long calls/puts + stock; S&P 500 constituents + ETF allowlist SPY QQQ DIA IWM XLK XLF |
| Contracts per ticket | ≤ 3 |
| Open option positions | ≤ 5 |
| Premium cap | ~$1,500–3,000 per position, passed per-order (`--premium-cap`) |
| Earnings blackout | 5 days around next earnings (look it up, e.g. `lookup_earnings_date`) |
| Session window | 10:15–15:45 ET (`paper_autonomy` in config); overnight 15:45 enter / 09:40 exit |
| Day-trade income | Take small gains: **take +2% on premium, cut at −25%** (options) |
| Stock guard | +2% target / −1% stop, trailing (guarded hourly) |
| No chase | Limit at the reviewed ask; never raise into a drifted ask |

## Selection Judgment

- **Momentum healthy**: 21d strength, RSI ≤ ~70 usable; **avoid RSI > ~75–77** (overbought gas). 52-week-high names within ~2% and RSI ~66 are strong-but-usable.
- **Gate**: SPY near all-time high = RISK_ON satisfied; a broad market break below key levels flips the gate.
- **IV context**: low-IV index ETFs (QQQ) = cheap convexity, tight spreads; higher-IV single names pay more premium — size accordingly.
- **Breakeven** ≈ strike + premium (call); VR = strike − premium below it. State it in every recommendation.
- Check deterministic facts before any recommendation: next earnings, universe membership, open position count, premium at ask × qty vs cap, session window, quote freshness. Never assert these as assumed.

## Entry Mechanics

- Place during session window with a limit at the reviewed ask; note the fill is at ask.
- **Day orders (GFD) expire unfilled at 16:00 ET** — Alpaca later reports them `cancelled`. That is an expired-not-filled order, no position.
- If the ask drifted up past your limit, **do not chase or raise**; let it expire and **re-stage in the next session window** (one-shot dated jobs with a stamp file prevent duplicate re-fires).
- Owner-directed restages: reuse `tmp/restage_owner_contract.py`-style flow (exact OCC, cap, override reason recorded in audit).

## Manage & Exit

- Take-profit **+2%** on the premium as soon as it moves; cut at **−25%** premium loss. Do not widen these without the owner.
- Stock positions: guard trails +2% target / −1% stop.
- Watch 3–5 DTE time decay; prefer to exit before expiry week pin risk. Hourly monitor (schedule C) watches the book.

## Gates & Overrides

- The deterministic risk engine **always** runs and can veto any LLM/agent proposal. Never bypass it silently.
- Owner-directed overrides are explicit, reasoned, and logged (`override_reason` in the audit trail) — never quiet, never automatic.
- Never expose broker keys to an LLM; agents get market/account info, never secrets.

## Common Mistakes

| Excuse | Reality |
|---|---|
| "The compliant expiry is 10 days out" | The 7-day owner ask is legitimate; scout DTE rejection ≠ desk prohibition |
| "This strike fits delta better" | Named strike wins; re-run risk on the named contract |
| "Desk default stop is −50%" | Owner plan: +2% take / −25% stop |
| "Position cap means one of them has to go" | Cap is ≤5; sequence and say both, offer the plan |
| "`risk_limits` says 1 position / $25 order" | Those are the LIVE route caps only. Paper stock uses `paper_autonomy` (≤$500); options use per-order premium caps + the desk ≤5 / ≤3 rules |
| "I'll raise the limit to catch the move" | No chase; re-stage next window |
| "Estimated premium, assumed earnings, assumed universe" | Compute them — gates are deterministic |

## Ops & Token Economy

- The 10:20 ET weekday LLM run is the only scheduled token consumer; keep the desk **zero-burn** when no LLM analysis is required — use the deterministic scout/guard path.
- launchd/TCC: background jobs run from Desktop paths with `/opt/homebrew/bin/python3` or the TradingAgents venv; repo `.venv` may be blocked. Logs to `outputs/`.
- Verify by evidence: check order status via the paper backend, positions list, and ledger before claiming a fill.

See `ai-trading-desk` (ops workflow, risk gates) and `ai-cio-portfolio-manager` (evidence policy, market signals) for the wider requirements.