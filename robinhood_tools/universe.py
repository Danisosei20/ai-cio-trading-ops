from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .errors import PolicyViolation


@dataclass(frozen=True)
class Sp500Snapshot:
    """A host-supplied, current S&P 500 membership snapshot."""

    symbols: frozenset[str]
    as_of: str
    source_url: str

    def require_current_member(self, symbol: str, *, max_age_hours: int = 24) -> None:
        observed = datetime.fromisoformat(self.as_of)
        if observed.tzinfo is None:
            raise PolicyViolation("S&P 500 membership timestamp must include a timezone.")
        age = datetime.now(timezone.utc) - observed.astimezone(timezone.utc)
        if age.total_seconds() < 0 or age.total_seconds() > max_age_hours * 3600:
            raise PolicyViolation("S&P 500 membership data is stale; refresh it from a current source.")
        if not self.source_url.startswith("https://"):
            raise PolicyViolation("S&P 500 membership evidence must include an HTTPS source URL.")
        if symbol.upper() not in {item.upper() for item in self.symbols}:
            raise PolicyViolation(f"{symbol.upper()} is not verified as a current S&P 500 constituent.")
