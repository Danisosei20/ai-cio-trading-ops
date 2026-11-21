# Free market data sources and external strategies — research note

Date: 2026-11-21
Status: research only. No trading logic was changed; these are candidates to evaluate, not wired into the desk.

## Free MCP / data sources

| Source | Data | Auth / cost | Notes |
|---|---|---|---|
| `yahoo-finance` (already configured) | quotes, OHLCV, fundamentals, options chains, screeners, news | free, no key | Verified live during acceptance pass. |
| `tradingview-free` (`tradingview-mcp-server` 0.8.1, self-hosted) | 39 tools: technicals, multi-timeframe, screeners, backtests, sentiment | free (MIT), no TradingView account | Added to user MCP config in an isolated venv; verified live with `combined_analysis` on NVDA. The hosted `mcp.tradingview.com` remains the paid option and is left as-is. |
| `maverick-mcp` (wshobson/maverick-mcp) | RSI/MACD/support-resistance, ATR sizing, trade journal, backtesting | free (MIT), no key for core | Not installed: requires Python 3.12+ / `uv`, host is 3.11. Candidate if a 3.12 runtime is added. |
| `tradingview-scraper` (omkarcloud) | TradingView-style REST technicals | free self-hosted | Not installed: 0 stars and scraping-ToS caveat; would need an MCP wrapper. |

Only official repos/PyPI packages were used; the research flagged impostor repos carrying malware, so no unofficial package was installed.

## Strategies worth evaluating (from public sources)

1. **Moving-average crossover (trend filter).** Long when short MA crosses above long MA; exit on reverse. Evidence: Brock/Lakonishok/LeBaron find positive, less volatile buy-period returns; later work shows profits shrink after costs and data snooping. Failure mode: whipsaws in sideways markets — use as a filter, not a standalone edge. Sources: Investopedia, Schwab, BLL 1992, Illinois agMAS.
2. **RSI mean reversion / divergence with trend filter.** Signal is the *exit* from extremes (<30/>70), ideally with price above the 200-day MA; bullish divergence = lower price low + higher RSI low. Failure mode: strong trends stay overbought/oversold; divergences fire repeatedly before a top. Sources: StockCharts ChartSchool, Investopedia.
3. **Volume-confirmed breakout.** Long only on close beyond resistance with above-average volume; stop below the broken level, target the measured range. Failure mode: fakeouts and opening-gap reversals. Sources: Investopedia.

## Monday / NVDA: call or put?

A bullish signal implies a **call (or shares)**, not a put — but it must survive confirmation:
- above-average volume and a close above resistance (not the Monday open alone);
- multi-timeframe trend alignment (price above 200-day MA, short MA > long MA, RSI in the 40–90 bull range);
- MACD above signal;
- weekend news/8-K check, since bad news released Friday can move Monday's open.

Veto/challenge checks: RSI overbought plus bearish divergence, extreme-low put/call ratio (complacency), and elevated IV into a catalyst (raises the call's breakeven). A put is a *confirmed momentum-failure* trade, not merely a disagreement with a bullish signal.

Live check at the time of this note: NVDA daily was strongly bearish (RSI ~15, death cross, price below EMA200, -12.5% day), VIX 35.9, desk regime RISK_OFF — so the desk correctly blocked all option entries regardless of direction.

Sources: Investopedia (call/put, put-call ratio, weekend effect, breakouts, volume), Fidelity (buying calls), Schwab (call/put strategies, MA crossovers), StockCharts ChartSchool (RSI, put-call ratio), ASU research on the weekend effect.
