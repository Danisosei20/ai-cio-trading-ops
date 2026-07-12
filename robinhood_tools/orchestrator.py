from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable, Iterable

from .database import CioDatabase
from .errors import PolicyViolation
from .lifecycle import ExitDecision, PositionObservation, evaluate_exit
from .runtime import RuntimeSettings


@dataclass(frozen=True)
class ScreenedIdea:
    symbol: str
    score: int
    qualified: bool
    summary: str


@dataclass(frozen=True)
class DailyReviewResult:
    run_key: str
    action: str
    symbol: str | None
    detail: str


class DailyOrchestrator:
    """Restart-safe coordinator. Host callbacks retain market/broker connector ownership."""

    def __init__(self, database: CioDatabase, settings: RuntimeSettings):
        self.database = database
        self.settings = settings

    def run_daily(self, account_label: str, screen: Callable[[], Iterable[ScreenedIdea]],
                  *, day: date | None = None) -> DailyReviewResult:
        run_day = day or date.today()
        run_key = f"{run_day.isoformat()}:{account_label}:daily-review"
        if not self.database.claim_daily_run(run_key):
            raise PolicyViolation("Daily review already ran or is running for this account and date.")
        try:
            ideas = sorted((idea for idea in screen() if idea.qualified), key=lambda item: item.score, reverse=True)
            if not ideas:
                result = DailyReviewResult(run_key, "no_action", None, "No Action Recommended")
            else:
                idea = ideas[0]
                try:
                    lifecycle = self.database.get_trade_lifecycle(idea.symbol)
                except PolicyViolation:
                    lifecycle = self.database.begin_trade_lifecycle(idea.symbol)
                result = DailyReviewResult(run_key, "review", idea.symbol, f"Continue {lifecycle['task_name']}: {idea.summary}")
            self.database.complete_daily_run(run_key, "completed", result.detail)
            return result
        except Exception as exc:
            self.database.complete_daily_run(run_key, "failed", str(exc))
            raise

    def monitor_open_positions(
        self,
        observations: Callable[[str], PositionObservation],
        *,
        target_return: Callable[[str], Decimal],
    ) -> list[tuple[str, ExitDecision]]:
        owner = str(uuid.uuid4())
        decisions: list[tuple[str, ExitDecision]] = []
        for lifecycle in self.database.list_trade_lifecycles():
            if lifecycle["status"] != "open":
                continue
            symbol = lifecycle["symbol"]
            if not self.database.acquire_lifecycle_lease(symbol, owner):
                continue
            try:
                decision = evaluate_exit(observations(symbol), target_return=target_return(symbol))
                decisions.append((symbol, decision))
            finally:
                self.database.release_lifecycle_lease(symbol, owner)
        return decisions
