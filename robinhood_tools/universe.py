from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .errors import PolicyViolation


@dataclass(frozen=True)
class Sp500Snapshot:
    """A host-supplied, current S&P 500 membership snapshot.

    `index_etfs` is the owner-authorized allowlist (e.g. SPY, QQQ); empty
    preserves the original constituents-only behavior.
    """

    symbols: frozenset[str]
    as_of: str
    source_url: str
    index_etfs: frozenset[str] = frozenset()

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

    def require_eligible_purchase(self, symbol: str, *, max_age_hours: int = 24) -> None:
        """Constituent OR allowlisted index ETF (owner-authorized 2026-10-08)."""
        if symbol.upper() in {item.upper() for item in self.index_etfs}:
            self._require_fresh(max_age_hours)
            return
        self.require_current_member(symbol, max_age_hours=max_age_hours)

    def _require_fresh(self, max_age_hours: int) -> None:
        observed = datetime.fromisoformat(self.as_of)
        if observed.tzinfo is None:
            raise PolicyViolation("Membership timestamp must include a timezone.")
        age = datetime.now(timezone.utc) - observed.astimezone(timezone.utc)
        if age.total_seconds() < 0 or age.total_seconds() > max_age_hours * 3600:
            raise PolicyViolation("Membership data is stale; refresh it from a current source.")
        if not self.source_url.startswith("https://"):
            raise PolicyViolation("Membership evidence must include an HTTPS source URL.")
