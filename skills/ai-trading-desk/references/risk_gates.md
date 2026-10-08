# Risk Gates (deterministic, non-overridable)

Code, not models. Every order passes ALL of these; any failure blocks.
Enforced in `robinhood_tools/paper_flow.py` + `risk_engine.py`; tested in
`tests/test_paper_flow.py` + `tests/test_risk_engine.py`.

1. **Mode**: `paper_auto` + Alpaca broker, autonomy enabled, human-approval
   flag off for autonomous paths. `TRADING_ENABLED=true` refuses everything.
2. **Kill/state**: emergency kill off, no symbol cooldown.
3. **Session**: weekday 10:15–15:30 ET + Alpaca clock open (regular session).
4. **Universe** (buys): verified current S&P500 member or allowlisted
   index ETF (SPY, QQQ, DIA, IWM, XLK, XLF), evidence ≤24h.
5. **Earnings** (buys): next date known and >5 trading days out. Unknown = block.
6. **News**: material negative news blocks autonomous entry.
7. **Size**: order ≤ $500 cap (and ≤ symbol cap), spread within limit.
8. **Shape**: DAY limit at reviewed intent, no chasing, no extended hours.
9. **Review → ledger → place**: fingerprint-bound review ID, expiring
   approval, atomic reservation, idempotent client key, reconcile on doubt.
10. **Position guard**: stops (-8% default) and targets (+20%) evaluated on
    broker marks; triggered exits sell through gates 1–9.

Confidence minimum (75) and regime score hurdles (90/93/97) apply where the
pipeline supplies scored candidates; missing evidence fails closed, never
assumed. Sells skip buy-only gates (universe/earnings) but keep all others.
Options are disabled by policy until the owner authorizes the change.
