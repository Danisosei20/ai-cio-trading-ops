"""Authoritative price quotes: Alpaca first, yfinance fallback, stale rejected.

Order of preference (both are real market data, neither is invented):
1. Alpaca stock snapshot (IEX feed, exchange timestamp on every quote).
2. yfinance 1-minute bars (only where yfinance is installed).
Anything older than `max_age_minutes` fails closed — a trade on a stale
quote is worse than no trade. Every quote carries source + timestamp so
the ledger and UI can show data-as-of.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from .errors import PolicyViolation


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: Decimal
    bid: Decimal | None
    ask: Decimal | None
    as_of: str  # ISO-8601, exchange timestamp
    source: str  # "alpaca-iex" | "yfinance-1m"


def _age_minutes(as_of: str, now: datetime) -> float:
    text = as_of.replace("Z", "+00:00")
    # Python <3.11 fromisoformat handles at most 6 fractional digits.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    ts = datetime.fromisoformat(text)
    if ts.tzinfo is None:
        raise PolicyViolation("Quote timestamp lacks a timezone.")
    return (now.astimezone(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds() / 60


def from_alpaca_snapshot(symbol: str, payload: dict) -> Quote:
    """Parse an Alpaca /v2/stocks/{symbol}/snapshot response."""
    try:
        trade = payload["latestTrade"]
        price = Decimal(str(trade["p"]))
        as_of = str(trade["t"])
        quote = payload.get("latestQuote") or {}
        bid = Decimal(str(quote["bp"])) if quote.get("bp") else None
        ask = Decimal(str(quote["ap"])) if quote.get("ap") else None
    except (KeyError, TypeError, ValueError) as exc:
        raise PolicyViolation(f"Alpaca snapshot for {symbol} is incomplete.") from exc
    if price <= 0:
        raise PolicyViolation(f"Alpaca snapshot for {symbol} has no price.")
    return Quote(symbol.upper(), price, bid, ask, as_of, "alpaca-iex")


def from_yfinance(symbol: str, frame) -> Quote:
    """Parse a yfinance 1-minute history frame (last row)."""
    if frame is None or len(frame) == 0:
        raise PolicyViolation(f"No yfinance bars for {symbol}.")
    last = frame.iloc[-1]
    ts = frame.index[-1]
    as_of = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
    if "tzinfo" not in dir(ts) or ts.tzinfo is None:
        raise PolicyViolation(f"yfinance bars for {symbol} lack timezone.")
    return Quote(symbol.upper(), Decimal(str(last["Close"])), None, None,
                 as_of, "yfinance-1m")


def get_quote(symbol: str, *, data_client=None, max_age_minutes: int = 5,
              now: datetime | None = None) -> Quote:
    """Fresh quote or PolicyViolation. Never returns stale data silently."""
    now = now or datetime.now(timezone.utc)
    errors = []
    if data_client is not None:
        try:
            quote = from_alpaca_snapshot(symbol, data_client.stock_snapshot(symbol))
            age = _age_minutes(quote.as_of, now)
            if age < 0 or age > max_age_minutes:
                raise PolicyViolation(
                    f"Alpaca quote for {symbol} is {age:.1f}m old (limit {max_age_minutes}m).")
            return quote
        except Exception as exc:  # noqa: BLE001 - fall through to backup source
            errors.append(f"alpaca: {type(exc).__name__}")
    try:
        import yfinance as yf  # noqa: PLC0415

        quote = from_yfinance(symbol, yf.Ticker(symbol).history(period="1d", interval="1m"))
        age = _age_minutes(quote.as_of, now)
        if age < 0 or age > max_age_minutes:
            raise PolicyViolation(
                f"yfinance quote for {symbol} is {age:.1f}m old (limit {max_age_minutes}m).")
        return quote
    except ImportError:
        errors.append("yfinance: not installed")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"yfinance: {type(exc).__name__}")
    raise PolicyViolation(f"No fresh quote for {symbol} ({'; '.join(errors)}).")
