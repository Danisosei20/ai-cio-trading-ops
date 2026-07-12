from __future__ import annotations

from typing import Protocol
import time

from .analysis import MarketSnapshot
from .risk import PortfolioState
from .universe import Sp500Snapshot


class MarketDataAdapter(Protocol):
    def snapshot(self, symbol: str) -> MarketSnapshot: ...
    def sp500_membership(self) -> Sp500Snapshot: ...


class PortfolioAdapter(Protocol):
    def portfolio(self, account_id: str) -> PortfolioState: ...


class SlackHostAdapter(Protocol):
    """Implemented by the connected host; credentials never enter this repository."""
    def send_approval(self, *, channel_id: str, message: str) -> dict: ...


class UnavailableHostAdapter:
    def __getattr__(self, name):
        raise RuntimeError(f"Connected host adapter operation {name!r} is unavailable; fail closed.")


class RetryingNotifier:
    """Retries notification delivery only; it never retries broker placement."""
    def __init__(self, delegate: SlackHostAdapter, attempts: int = 3):
        self.delegate = delegate
        self.attempts = attempts

    def send_approval(self, *, channel_id: str, message: str) -> dict:
        last = None
        for attempt in range(self.attempts):
            try:
                result = self.delegate.send_approval(channel_id=channel_id, message=message)
                return {**result, "retry_count": attempt}
            except Exception as exc:
                last = exc
                if attempt + 1 < self.attempts:
                    time.sleep(0.01 * (2 ** attempt))
        raise RuntimeError(f"Slack delivery failed after {self.attempts} attempts") from last
