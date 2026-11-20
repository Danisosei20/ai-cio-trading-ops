"""Hard paper-desk controls for new long-option entries."""
from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from math import isfinite
from pathlib import Path

from .errors import PolicyViolation
from .desk_policy import POLICY

MAX_VIX = POLICY.max_vix
MAX_QUOTED_IV = POLICY.quoted_iv_cap
MAX_PREMIUM = Decimal("500")
MAX_CONTRACTS = 1
MAX_DAILY_LOSS = Decimal("500")


def validate_quoted_iv(quoted_iv: float | None,
                       max_iv: float = MAX_QUOTED_IV) -> None:
    """Reject missing or excessive quoted IV for a new option entry."""
    if quoted_iv is None or not isfinite(quoted_iv):
        raise PolicyViolation("option entry blocked: quoted IV unavailable")
    if not isfinite(max_iv) or max_iv > MAX_QUOTED_IV:
        max_iv = MAX_QUOTED_IV
    if quoted_iv > max_iv:
        raise PolicyViolation(
            f"option entry blocked: quoted IV {quoted_iv:.1%} exceeds "
            f"{max_iv:.1%} cap")


def realized_pnl_today(database_path: str | Path, today: date) -> Decimal | None:
    """Read reconciled closed P&L for today; None means unreadable evidence."""
    try:
        con = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
        row = con.execute(
            "SELECT COALESCE(SUM(CAST(realized_profit AS REAL)), 0) "
            "FROM trade_lifecycles WHERE closed_at LIKE ?",
            (f"{today.isoformat()}%",),
        ).fetchone()
        con.close()
        return Decimal(str(row[0] or 0))
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return None


def current_vix() -> float | None:
    """Fetch latest VIX close; missing volatility evidence blocks entries."""
    try:
        import yfinance as yf

        frame = yf.Ticker("^VIX").history(period="2d", interval="1d")
        closes = frame["Close"].dropna() if frame is not None else []
        return float(closes.iloc[-1]) if len(closes) else None
    except Exception:  # noqa: BLE001 - fail closed at the risk gate
        return None


def validate_option_entry(*, vix: float | None, premium: Decimal,
                          contracts: int,
                          realized_pnl_today: Decimal | None) -> None:
    """Raise when a new option entry breaches a hard desk limit."""
    if vix is None:
        raise PolicyViolation("option entry blocked: VIX unavailable")
    if vix > MAX_VIX:
        raise PolicyViolation(f"option entry blocked: VIX {vix:.1f} above 25")
    if premium > MAX_PREMIUM:
        raise PolicyViolation(f"option entry blocked: premium ${premium:.2f} above $500 cap")
    if contracts > MAX_CONTRACTS:
        raise PolicyViolation(f"option entry blocked: contracts {contracts} above 1 limit")
    if realized_pnl_today is None:
        raise PolicyViolation("option entry blocked: daily P&L unavailable")
    if realized_pnl_today <= -MAX_DAILY_LOSS:
        raise PolicyViolation(f"option entry blocked: daily loss ${abs(realized_pnl_today):.2f} reached $500 limit")
