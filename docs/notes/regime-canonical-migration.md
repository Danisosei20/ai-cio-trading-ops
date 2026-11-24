# Canonical market-regime migration (owner action required)

The owner has an **uncommitted staged change** in the live checkout that adds
`require_long_term` to `robinhood_tools/market_prices.py`. Phase 8.2 did not
modify or stage that file. A compatibility shim
(`robinhood_tools/regime_compat.py`) keeps clean checkouts correct today.

## Intended canonical interface

```python
def market_regime(spy_closes: list[float], *, require_long_term: bool = False) -> str:
    if len(spy_closes) < 51:
        return "UNKNOWN"
    if require_long_term and len(spy_closes) < 201:
        return "UNKNOWN"
    prior50 = spy_closes[-51:-1]
    mean50 = sum(prior50) / 50
    latest = spy_closes[-1]
    base = "RISK_ON" if latest > mean50 else "CHOP" if latest == mean50 else "RISK_OFF"
    if not require_long_term or base != "RISK_ON":
        return base
    mean200 = sum(spy_closes[-201:-1]) / 200
    return "RISK_ON" if latest > mean200 else "CHOP" if latest == mean200 else "RISK_OFF"
```

Thresholds (50/200 sessions, strict `>` comparison, CHOP on equality) are
unchanged from the staged fix. The staged `tests/test_market_prices.py` already
covers both configurations.

## Required owner action

1. Commit the staged `robinhood_tools/market_prices.py` and
   `tests/test_market_prices.py` in the main checkout.
2. Confirm `python -m unittest discover -s tests` passes there.
3. Then simplify `regime_compat.market_regime_compatible()` to delegate directly
   to `market_regime(closes, require_long_term=...)` and remove the legacy
   fallback in a follow-up reviewed PR.
4. Do not change RISK_OFF, VIX or any trading threshold during the migration.

Until step 3, the shim is intentional compatibility code, not dead code.
