from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable, Iterable

from .daily_controls import (
    BrokerStateSnapshot,
    FreshnessManifest,
    changed_since_yesterday,
    require_no_broker_state_drift,
)
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
    execution_eligible: bool = True
    blocked_reason: str = ""
    market_regime: str = "unknown"


@dataclass(frozen=True)
class DailyReviewResult:
    run_key: str
    action: str
    symbol: str | None
    detail: str
    shadow_symbol: str | None = None
    changes: tuple[str, ...] = ()


class DailyOrchestrator:
    """Restart-safe coordinator. Host callbacks retain market/broker connector ownership."""

    def __init__(self, database: CioDatabase, settings: RuntimeSettings):
        self.database = database
        self.settings = settings

    def run_daily(
        self,
        account_label: str,
        screen: Callable[[], Iterable[ScreenedIdea]],
        *,
        day: date | None = None,
        freshness: FreshnessManifest | None = None,
        broker_state: Callable[[], BrokerStateSnapshot] | None = None,
        learning_due_dates: Callable[[date], dict[int, str]] | None = None,
        review_state: dict | None = None,
        stale_after: timedelta = timedelta(minutes=30),
    ) -> DailyReviewResult:
        run_day = day or date.today()
        run_key = f"{run_day.isoformat()}:{account_label}:daily-review"
        if not self.database.claim_daily_run(run_key, stale_after=stale_after):
            raise PolicyViolation("Daily review already ran or is running for this account and date.")
        try:
            self.database.save_run_checkpoint(run_key, "preflight", "running", {})
            if freshness is not None:
                report = freshness.require_complete()
                self.database.save_freshness_manifest(run_key, freshness.generated_at, report)
            if broker_state is not None:
                require_no_broker_state_drift(self.database, broker_state())
            self.database.save_run_checkpoint(run_key, "preflight", "completed", {})

            saved_screen = self.database.run_checkpoint(run_key, "screen")
            if saved_screen and saved_screen["status"] == "completed":
                screened = [ScreenedIdea(**item) for item in saved_screen["payload"]["ideas"]]
            else:
                screened = sorted(screen(), key=lambda item: item.score, reverse=True)
                self.database.save_run_checkpoint(
                    run_key, "screen", "completed",
                    {"ideas": [
                        {
                            "symbol": item.symbol,
                            "score": item.score,
                            "qualified": item.qualified,
                            "summary": item.summary,
                            "execution_eligible": item.execution_eligible,
                            "blocked_reason": item.blocked_reason,
                            "market_regime": item.market_regime,
                        }
                        for item in screened
                    ]},
                )

            qualified = [idea for idea in screened if idea.qualified]
            shadow = qualified[0] if qualified else None
            shadow_id = f"shadow:{run_key}"
            self.database.record_shadow_recommendation(
                recommendation_id=shadow_id, run_key=run_key,
                symbol=shadow.symbol if shadow else None, score=shadow.score if shadow else None,
                action="paper_candidate" if shadow else "no_action",
                market_regime=shadow.market_regime if shadow else "unknown",
                payload={
                    "summary": shadow.summary if shadow else "No qualifying shadow-equity idea.",
                    "execution_eligible": shadow.execution_eligible if shadow else False,
                    "blocked_reason": shadow.blocked_reason if shadow else "",
                    "paper_only": True,
                },
            )
            if shadow and learning_due_dates is not None:
                self.database.schedule_learning(shadow_id, learning_due_dates(run_day))
            self.database.save_run_checkpoint(
                run_key, "shadow", "completed", {"recommendation_id": shadow_id, "symbol": shadow.symbol if shadow else None}
            )

            executable = [idea for idea in qualified if idea.execution_eligible]
            if not executable:
                blocked = shadow.blocked_reason if shadow and shadow.blocked_reason else "no idea cleared every execution gate"
                result = DailyReviewResult(
                    run_key, "no_action", None, f"No Action Recommended — {blocked}.",
                    shadow_symbol=shadow.symbol if shadow else None,
                )
            else:
                idea = executable[0]
                try:
                    lifecycle = self.database.get_trade_lifecycle(idea.symbol)
                except PolicyViolation:
                    lifecycle = self.database.begin_trade_lifecycle(idea.symbol)
                result = DailyReviewResult(
                    run_key, "review", idea.symbol, f"Continue {lifecycle['task_name']}: {idea.summary}",
                    shadow_symbol=shadow.symbol if shadow else None,
                )

            state = {
                "action": result.action,
                "symbol": result.symbol,
                "shadow_symbol": result.shadow_symbol,
                **(review_state or {}),
            }
            previous = self.database.previous_daily_review_state(account_label)
            changes = changed_since_yesterday(state, previous, keys=tuple(state))
            self.database.save_daily_review_state(account_label, state)
            result = DailyReviewResult(
                result.run_key, result.action, result.symbol, result.detail, result.shadow_symbol, changes
            )
            self.database.save_run_checkpoint(run_key, "decision", "completed", result.__dict__)
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
