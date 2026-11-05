# Downside signal paths for the desk — design

Date: 2026-11-05
Owner: desk operator (Danis)
Status: awaiting approval

## Intent and why

The desk is built to find long entries. On 2026-11-05 the market fell hard
(NDX -5.78%, QQQ -5.93%, Composite -5.24%) and the desk produced no downside
position. It is not blocked from shorting — the code can — but every path that
finds candidates is tuned to find strong names, and the one path that does find
shorts ran in dry-run and then hit a notional cap.

The goal is that a down tape produces a *put candidate* and a *day-trade short*
instead of nothing, while every existing safety bound still applies and nothing
goes live without the Friday 16:45 review signing off.

## Goals

1. A bear regime is computed once, from the SPY-vs-50-day-SMA rule already in
   the repo, and is available to every signal path.
2. In bear regime the 15:10 options watch produces put candidates from the
   **weakest** names in the universe, not from the strongest.
3. Day-trade short sizing no longer dies on arrival at the notional cap.
4. The long screen is byte-for-byte unchanged.
5. All of the above provable in the offline certification harness.

## Non-goals

- No change to the long entry logic, its scoring, or its ranking.
- No change to exit policy (options -15% stop, trail at +25%, floor peak x 0.40).
- No change to the Time Machine odds gate or its fail-closed behaviour.
- No change to the $10,000/week objective or its reporting.

## Anti-goals

- Shorting the *strongest* names on a down day (counter-trend, squeeze-prone).
- Trading anything live before the Friday review.
- Raising a safety bound so a single tape can take a large slice of equity.

## Constraints

- Research LLM calls remain bounded (a real run measured 544s vs the 300s
  ticker budget) — that path stays fail-closed and does not gate this work.
- `PortfolioRiskController` hard-codes `_max_total_notional = 5000`,
  `_max_open_positions = 5`, `_max_daily_loss_pct = 0.03`, duplicate cooldown
  300s, stale-data 120s. The controller is the atomic gate every execution path
  passes through; it must stay that way.
- Options entries run through `options_pilot.py -> place_long_option`. Never bypass.
- Windows console is cp1252: new console output is ASCII.
- Two venvs; desk tests use `ai-cio-trading-ops\.venv` which has no pytest, so
  new tests are `unittest`.

## Approaches

**Approach 1 — reverse the momentum sign under bear regime** *(rejected)*
In bear regime a high-momentum name also becomes shortable. Cheapest change, but
it would short APP (+25.7% mom20) and REGN (+27%) on exactly the day they are
best squeeze candidates, and it inverts the momentum signal the long book is
calibrated on.

**Approach 2 — a separate short screen ranking the weakest names** *(chosen)*
Keep the long screen untouched. Add a second pass over the same universe, the
existing scorer with the sort reversed, used only when SPY < 50d SMA. Rationale:
what failed on 2026-11-05 was candidate *selection*, not permission — once the
zone gate was direction-aware, shorts passed it and died at the cap. Approach 2
adds the finding without touching the long logic, and the bear book stays
separately measurable in the scorecard so its edge can be judged on its own
rather than blended into long P/L. Cost: a second code path that can drift.

**Approach 3 — gate-only, unblock shorts** *(rejected)*
Correct but inert: on today's tape no put candidate is produced anyway, so
nothing would have changed.

## Decisions the operator made

- Fix the signal paths, keep dry-run. Nothing goes live until Friday 16:45.
- Bear gate is regime-gated on SPY < 50-day SMA, not a per-stock override.
- Approach 2, the separate short screen.
- Notional cap: separate books — $5,000 long + $5,000 short.
- Risk controller gets a signature change (optional direction field, old ledgers
  still load) so a `sell` on a symbol not in the long book becomes a short
  entry. This also fixes the pre-existing bug where short entries were invisible
  to exposure accounting.
- Screener writes the full screened universe to a new file at 09:45; the long
  ranking, scoring, and `latest.json` top-10 output are unchanged.
- Bought puts stay outside the controller's notional budget. They keep their own
  premium cap. Confirmed knowing that a short book and a put book can each be
  full-sized at the same time.

## How it gets built (the parts decided)

**Regime helper.** Extend the existing `robinhood_tools.market_prices.market_regime()`
so it also returns `RISK_OFF` when SPY trades below its 50-day SMA.
`options_watch_trade.py` and the day-trade path both call that one function.
The two other copies of the rule in the repo are left alone (one is broken and
out of scope, one is backtest-only), but the docstring notes that they exist so
the next person can converge them.

**Short screen.** The screener writes the full screened universe to a new file
alongside `latest.json`, ranked ascending by `mom20` (existing scorer, reversed
sort). `latest.json` — ranking, scoring, top-10 — is byte-identical to today.
Consumed only when regime is `RISK_OFF`.

**Options watch.** In `RISK_OFF`, candidate selection reads the short screen and
direction is `put`; in `RISK_ON` behaviour is unchanged. All downstream gates
(liquid-signal, zone confluence, premium cap, contracts, positions, earnings
blackout, session window) still apply, because the short feed only changes which
names and which direction are offered.

**Day-trade engine.** Dry-run stays on. Short sizing clamps to the remaining
short budget rather than dying at the cap: the binding constraint today was
notional (`0+5084.31 > 5000` for a QQQ short), not risk.

**Risk controller.** Notional accounting split by direction: `long_notional`
and `short_notional` each capped at `$5,000`, total exposure still reported.
`record_order` gains an optional `direction`; a `sell` on a symbol in the long
book releases it, a `sell` on a symbol not in the long book is a short entry,
and a `buy` to close a short releases the short book rather than opening a long.
Exits and the kill switch are unaffected. Every check stays atomic; the split is
done where exposure is recorded and where it is computed.

## What the builder decides

- Exact shape of the short screen's weak-name cut (bottom N vs a mom20 floor).
- Whether `market_regime()` caches SPY bars for the session.
- Test names and coverage in `tests/`.

## Additional constraints found while checking the design

**A latent defect the builder check surfaced, confirmed in source:**
`overnight_pilot.py:501` calls `uptrend_ok(spy_closes, 50)`, a function defined
nowhere in the repo. It is unguarded, so the overnight pilot crashes with
`NameError` whenever `len(spy_closes) >= 51`. In practice `period="60d"` yields
roughly 42 daily closes, so the branch is never taken and regime stays `CHOP`
forever — the overnight pilot has been silently unable to enter. This is a real
bug, independent of this work, and is out of scope here. It is recorded so it is
not lost; fix it in its own change.

Also confirmed during review:
- `robinhood_tools/market_prices.py` already has `market_regime(spy_closes)`
  returning `RISK_ON`/`CHOP`/`UNKNOWN`, tested, and used fail-closed by
  `tradingagents_paper_auto.py`. The working reference for SPY-bar fetching is
  that one (`period="1y"`, failure → `UNKNOWN`), not the broken block above.
- The options path (`options_pilot.py`) never instantiates a
  `PortfolioRiskController`; option premium is not in the exposure ledger today.
- `record_order()` has no direction concept: `side == "sell"` currently means
  *release* (`open_positions.pop`), which is why a short entry is invisible to
  exposure accounting.
- `outputs/screener/latest.json` persists only the top-N strongest rows
  (`scored[:top]`, `--top 10` at 09:45). The weakest names in the universe are
  not persisted anywhere.
- `weekly_scorecard.score_round_trips` FIFO-matches buys, so a short round trip
  (sell entry → buy exit) does not currently report as its own book.

## Builder-check findings and who settled them

**Regime contract — mine.** Extend the existing `market_prices.market_regime()`.
Vocabulary gains `RISK_OFF` (SPY 50d SMA below). On short history or a failed
SPY fetch it returns `UNKNOWN` and every entry path fails closed — the standing
rule that missing evidence is not permission to trade. SPY bars come from that
module's own fetch; no new network dependency, no new vocabulary elsewhere.

**Enforcement on a short that exceeds the short budget — mine.** Clamp `qty` to
the remaining short budget rather than skip, so a strong setup is traded smaller
instead of dropped; skip only when the clamped qty rounds to zero or would make
the stop distance proportionally meaningless (< 1 share equivalent). Longs keep
today's behaviour exactly.

**Bear book measurement — mine, out of scope.** The operator chose dry-run, so
no fills exist to score, and short-aware round-trip matching (buy/sell FIFO in
`weekly_scorecard.py`) is its own change. The "separately measurable" benefit of
Approach 2 therefore does not ship here; it ships as soon as the desk trades
shorts live.

**Direction precedence in `RISK_ON` — mine.** `gated_candidates` keeps deriving
direction per row (`mom20 >= 0 -> call`), so a negative-momentum row can still
produce a put in a bull regime. The verification claim below is corrected to
match the code, not the reverse.

**Confirmed by the operator** (above, and originally surfaced as the check's
top three): the risk-controller signature change, the extra screener output
file, and puts staying outside the controller's notional budget.

## Verification

- `scripts/paper_certification.py --no-broker` plus new checks: bear regime
  produces put candidates from the weakest names; bull regime leaves
  `gated_candidates` behaviour unchanged; `UNKNOWN` regime fails closed; the
  split cap blocks a $5,001 short while a $5,000 long is still approved; a
  short entry is now visible to exposure and a buy-to-cover releases it.
- Full desk suite green (`unittest discover -s tests`).
- `preflight.py --full` includes execution certification.
