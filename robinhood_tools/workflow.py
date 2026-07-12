from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import date, timedelta
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

from .analysis import ExitPlan, TradeCandidate
from .database import CioDatabase
from .errors import PolicyViolation
from .logging import get_logger
from .lifecycle import ExitDecision
from .models import EquityOrderRequest, Order, OrderReview
from .risk import PortfolioState, RiskLimits
from .service import RobinhoodTradingService


class Notifier(Protocol):
    def send_approval(self, *, channel_id: str, message: str) -> dict: ...


class Journal(Protocol):
    def append(self, event: dict) -> None: ...


class CioWorkflow:
    """Coordinates analysis through notification; never treats notification as approval."""

    def __init__(
        self,
        service: RobinhoodTradingService,
        database: CioDatabase,
        notifier: Notifier,
        journal: Journal,
        risk_limits: RiskLimits,
        *,
        channel_id: str,
        approval_window_minutes: int = 120,
    ):
        self.service = service
        self.database = database
        self.notifier = notifier
        self.journal = journal
        self.risk_limits = risk_limits
        self.channel_id = channel_id
        self.approval_window_minutes = approval_window_minutes
        self.logger = get_logger()

    def prepare_purchase(
        self,
        *,
        request: EquityOrderRequest,
        candidate: TradeCandidate,
        portfolio: PortfolioState,
        sector: str,
        exit_plan: ExitPlan,
    ) -> tuple[ApprovalRecordLike, OrderReview]:
        correlation_id = str(uuid.uuid4())
        if request.side != "buy":
            raise PolicyViolation("prepare_purchase accepts buy requests only.")
        candidate.validate(
            max_spread_pct=self.risk_limits.max_spread_pct,
            max_position_weight=self.risk_limits.max_position_weight,
        )
        order_value = request.notional or (request.quantity or Decimal("0")) * candidate.intended_price
        try:
            self.risk_limits.validate_purchase(
                portfolio, symbol=request.symbol, sector=sector, order_value=order_value,
                avg_daily_dollar_volume=candidate.snapshot.avg_daily_dollar_volume,
            )
        except PolicyViolation as exc:
            if "Insufficient buying power" in str(exc):
                self.notifier.send_approval(
                    channel_id=self.channel_id,
                    message=self._insufficient_funds_message(request, portfolio, order_value, str(exc)),
                )
            raise
        review = self.service.review_equity_order(request)
        approval = self.database.create(
            request, review, window_minutes=self.approval_window_minutes
        )
        self.database.save_exit_plan(exit_plan.symbol, asdict(exit_plan))
        self.database.audit(
            "approval_created",
            {"symbol": request.symbol, "snapshot_id": candidate.snapshot.digest(), "score": candidate.score},
            correlation_id=correlation_id, approval_id=approval.approval_id,
        )
        message = self._message(request, candidate, portfolio, order_value, approval.approval_id)
        try:
            delivery = self.notifier.send_approval(channel_id=self.channel_id, message=message)
            self.database.record_delivery(
                approval_id=approval.approval_id, channel_id=self.channel_id, status="sent",
                attempted_at=datetime.now(timezone.utc).isoformat(),
                message_ts=delivery.get("message_ts"), message_link=delivery.get("message_link"),
                retry_count=int(delivery.get("retry_count", 0)),
            )
            self.database.open_reply_window(
                approval.approval_id, self.channel_id, delivery.get("message_ts"), minutes=10
            )
            self.database.audit("slack_delivered", delivery, correlation_id=correlation_id, approval_id=approval.approval_id)
        except Exception as exc:
            self.database.record_delivery(
                approval_id=approval.approval_id, channel_id=self.channel_id, status="failed",
                attempted_at=datetime.now(timezone.utc).isoformat(), error=str(exc),
            )
            self.database.audit("slack_failed", {"error": str(exc)}, correlation_id=correlation_id, approval_id=approval.approval_id)
            raise PolicyViolation("Slack delivery failed; placement remains blocked.") from exc
        self.journal.append({
            "event": "recommendation", "approval_id": approval.approval_id,
            "symbol": request.symbol, "snapshot_id": candidate.snapshot.digest(), "score": candidate.score,
        })
        due = {day: _add_weekdays(date.today(), day).isoformat() for day in (1, 5, 20)}
        self.database.schedule_learning(approval.approval_id, due)
        self.logger.info("approval_notification_sent", extra={"context": {
            "correlation_id": correlation_id, "approval_id": approval.approval_id, "symbol": request.symbol,
        }})
        return approval, review

    def claim_morning_run(self, account_label: str, day: date | None = None) -> str:
        run_key = f"{(day or date.today()).isoformat()}:{account_label}:morning-review"
        if not self.database.claim_daily_run(run_key):
            raise PolicyViolation("This account's morning review has already been claimed; duplicate run blocked.")
        return run_key

    def execute_approved(self, request: EquityOrderRequest, *, approval_id: str, review_id: str, confirmed: bool) -> Order:
        order = self.service.place_equity_order(
            request, approval_id=approval_id, review_id=review_id, confirmed=confirmed
        )
        self.journal.append({"event": "placement", "approval_id": approval_id, "order_id": order.id, "status": order.status})
        self.notifier.send_approval(
            channel_id=self.channel_id,
            message=self._execution_status_message(request, approval_id, order),
        )
        self.database.resolve_reply_window(approval_id, "executed")
        self.database.cleanup_terminal_reply_windows()
        return order

    def prepare_sale(
        self,
        *,
        request: EquityOrderRequest,
        decision: ExitDecision,
        estimated_profit: Decimal,
    ) -> tuple[ApprovalRecordLike, OrderReview]:
        """Create a sell review in the symbol's existing lifecycle task."""
        if request.side != "sell":
            raise PolicyViolation("prepare_sale accepts sell requests only.")
        if decision.action == "hold":
            raise PolicyViolation("The exit decision is hold; no sell approval may be created.")
        lifecycle = self.database.get_trade_lifecycle(request.symbol)
        if lifecycle["status"] != "open":
            raise PolicyViolation("A sale must continue the symbol's existing open lifecycle task.")
        review = self.service.review_equity_order(request)
        approval = self.database.create(request, review, window_minutes=self.approval_window_minutes)
        self.database.mark_sell_pending(request.symbol, approval_id=approval.approval_id)
        size = f"${request.notional}" if request.notional else f"{request.quantity} shares"
        message = (
            f"**AI CIO — Sell Review**\n"
            f"`{request.symbol}` · continue task **{lifecycle['task_name']}**\n\n"
            f"**Proposed order**\n- Side: **SELL**\n- Size: **{size}**\n"
            f"- Order: `{request.order_type}` · `{request.time_in_force}`\n"
            f"- Estimated profit: **${estimated_profit}** (final profit depends on fill)\n\n"
            f"**Exit logic**\n{decision.reason}\n"
            f"Suggested fraction: **{decision.suggested_fraction:.0%}**\n\n"
            f"**Approval**\nApproval ID: `{approval.approval_id}`\n"
            "Approve only in Codex after the broker review. A Slack reply does not authorize execution."
        )
        delivery = self.notifier.send_approval(channel_id=self.channel_id, message=message)
        self.database.record_delivery(
            approval_id=approval.approval_id, channel_id=self.channel_id, status="sent",
            attempted_at=datetime.now(timezone.utc).isoformat(),
            message_ts=delivery.get("message_ts"), message_link=delivery.get("message_link"),
            retry_count=int(delivery.get("retry_count", 0)),
        )
        self.journal.append({
            "event": "sell_review", "approval_id": approval.approval_id,
            "symbol": request.symbol, "reason": decision.reason,
        })
        return approval, review

    def report_filled_sale(self, *, symbol: str, order: Order, realized_profit: Decimal) -> None:
        if order.side != "sell" or order.status != "filled":
            raise PolicyViolation("Profit notification requires a filled sell order.")
        lifecycle = self.database.close_trade_lifecycle(
            symbol, order_id=order.id, realized_profit=str(realized_profit)
        )
        self.notifier.send_approval(
            channel_id=self.channel_id,
            message=(
                f"**Position sold — {symbol.upper()}**\n"
                f"- Task: **{lifecycle['task_name']}**\n"
                f"- Broker order ID: `{order.id}`\n"
                f"- Realized profit: **${realized_profit}**\n"
                "The lifecycle is closed; learning checkpoints remain scheduled."
            ),
        )
        self.journal.append({
            "event": "sale_filled", "symbol": symbol.upper(), "order_id": order.id,
            "realized_profit": str(realized_profit),
        })

    def _execution_status_message(self, request: EquityOrderRequest, approval_id: str, order: Order) -> str:
        if order.status == "filled":
            headline = "**Trade successful — filled**"
        elif order.status == "partially_filled":
            headline = "**Trade partially filled**"
        elif order.status in {"queued", "confirmed"}:
            headline = "**Order submitted — not yet filled**"
        elif order.status == "rejected":
            headline = "**Order rejected by broker**"
        else:
            headline = f"**Order status: {order.status}**"
        return (
            f"{headline}\n"
            f"- Symbol: `{request.symbol}`\n"
            f"- Side: **{request.side.upper()}**\n"
            f"- Approval ID: `{approval_id}`\n"
            f"- Broker order ID: `{order.id}`\n"
            f"- Broker state: **{order.status}**\n"
            "This status follows a Codex-authorized placement; Slack did not authorize execution."
        )

    def reconcile(self, approval_id: str, order: Order) -> None:
        if not order.id:
            raise PolicyViolation("Broker reconciliation requires an order ID.")
        if order.status in {"queued", "confirmed", "filled", "partially_filled"}:
            with self.database.connect() as db:
                db.execute(
                    "UPDATE approvals SET status='executed', order_id=?, executed_at=datetime('now') "
                    "WHERE approval_id=? AND status='reconciliation_required'",
                    (order.id, approval_id),
                )
        self.journal.append({"event": "reconciliation", "approval_id": approval_id, "order_id": order.id, "status": order.status})

    def _message(
        self,
        request: EquityOrderRequest,
        candidate: TradeCandidate,
        portfolio: PortfolioState,
        order_value: Decimal,
        approval_id: str,
    ) -> str:
        snapshot = candidate.snapshot
        return (
            f"**AI CIO — Trade Approval Request**\n"
            f"`{request.symbol}` · Score **{candidate.score}/100** · Signal **{snapshot.signal.title()}**\n\n"
            f"**Proposed order**\n"
            f"- Side: **{request.side.upper()}**\n"
            f"- Size: **{'$' + str(request.notional) if request.notional else str(request.quantity) + ' shares'}**\n"
            f"- Current price: **${snapshot.price}**\n"
            f"- Intended price: **${candidate.intended_price}**\n"
            f"- Order: `{request.order_type}` · `{request.time_in_force}`\n\n"
            f"**Funds and sizing**\n"
            f"- Buying power: **${portfolio.available_buying_power}**\n"
            f"- Proposed cost: **${order_value}**\n"
            f"- Buying power after order: **${portfolio.available_buying_power - order_value}**\n"
            f"- Reviewed sizing: **{'$' + str(request.notional) if request.notional else str(request.quantity) + ' shares'}**\n"
            f"- Want a different size? In Codex, specify the exact **dollar amount or share quantity**. "
            f"Do not approve this ID; a fresh broker review is required.\n\n"
            f"**Why this trade**\n{candidate.thesis}\n\n"
            f"**Market quality**\n"
            f"- Volume: {snapshot.session_volume:,} · 20-day avg: {snapshot.avg_volume_20d:,}\n"
            f"- Relative volume: **{snapshot.relative_volume}x**\n"
            f"- Bid/ask spread: **{snapshot.spread_pct:.3%}**\n"
            f"- 20-day volatility: **{snapshot.realized_volatility_20d:.1%}**\n"
            f"- Next earnings: **{snapshot.next_earnings_date or 'Not available'}**\n\n"
            f"**Risk controls**\n"
            f"- Counter-argument: {candidate.counter_argument}\n"
            f"- Invalidation: **${candidate.invalidation_level}**\n"
            f"- Target/review: {candidate.target_or_review_condition}\n"
            f"- Reward/risk: **{candidate.reward_risk}:1**\n\n"
            f"**Sources**\n" + "\n".join(f"- [{source.title}]({source.url})" for source in snapshot.sources) + "\n\n"
            f"**Approval**\nApproval ID: `{approval_id}`\n"
            f"Approve only in Codex with this exact ID. **A Slack reply does not authorize execution.**"
        )

    def _insufficient_funds_message(
        self, request: EquityOrderRequest, portfolio: PortfolioState, order_value: Decimal, reason: str
    ) -> str:
        affordable_shares = (
            portfolio.available_buying_power / request.limit_price
            if request.limit_price and request.limit_price > 0
            else None
        )
        suggestion = (
            f"At the reviewed limit price, theoretical maximum is approximately "
            f"**{affordable_shares:.4f} shares** before cash-reserve and other risk limits."
            if affordable_shares is not None
            else "Choose a smaller dollar amount in Codex for a fresh review."
        )
        return (
            f"**AI CIO — Insufficient Buying Power**\n"
            f"`{request.symbol}` · **NO APPROVAL CREATED**\n\n"
            f"**Funds**\n"
            f"- Buying power: **${portfolio.available_buying_power}**\n"
            f"- Requested cost: **${order_value}**\n"
            f"- Shortfall: **${order_value - portfolio.available_buying_power}**\n\n"
            f"**What to do**\n{suggestion}\n"
            f"Return to Codex and specify an exact smaller **dollar amount or share quantity**. "
            f"A fresh broker review and new approval ID will be required.\n\n"
            f"Reason: {reason}\n**Slack replies do not authorize execution.**"
        )


class ApprovalRecordLike(Protocol):
    approval_id: str


def _add_weekdays(start: date, days: int) -> date:
    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current
