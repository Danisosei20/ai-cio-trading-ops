from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .errors import PolicyViolation


@dataclass(frozen=True)
class PortfolioState:
    equity: Decimal
    cash: Decimal
    position_weights: dict[str, Decimal]
    sector_weights: dict[str, Decimal]
    pending_approvals: int
    approved_capital_today: Decimal
    buying_power: Decimal | None = None
    realized_pnl_today: Decimal = Decimal("0")
    realized_pnl_week: Decimal = Decimal("0")
    open_positions: int = 0
    settled_cash: Decimal | None = None
    unsettled_cash: Decimal = Decimal("0")
    pending_order_commitments: Decimal = Decimal("0")

    @property
    def available_buying_power(self) -> Decimal:
        return self.cash if self.buying_power is None else self.buying_power

    @property
    def settled_cash_available(self) -> Decimal:
        settled = self.settled_cash
        if settled is None:
            settled = self.cash - max(self.unsettled_cash, Decimal("0"))
        return max(settled - max(self.pending_order_commitments, Decimal("0")), Decimal("0"))

    @property
    def available_for_new_orders(self) -> Decimal:
        buying_power = max(
            self.available_buying_power - max(self.pending_order_commitments, Decimal("0")), Decimal("0")
        )
        return min(buying_power, self.settled_cash_available)


@dataclass(frozen=True)
class RiskLimits:
    max_position_weight: Decimal = Decimal("0.10")
    max_sector_weight: Decimal = Decimal("0.30")
    min_cash_weight: Decimal = Decimal("0.05")
    max_daily_approved_capital: Decimal = Decimal("5000")
    max_pending_approvals: int = 3
    max_spread_pct: Decimal = Decimal("0.005")
    max_order_pct_avg_volume: Decimal = Decimal("0.001")
    max_order_value: Decimal = Decimal("999999999")
    max_symbol_exposure: Decimal = Decimal("999999999")
    min_cash_dollars: Decimal = Decimal("0")
    max_open_positions: int = 999999
    max_daily_loss: Decimal = Decimal("999999999")
    max_weekly_loss: Decimal = Decimal("999999999")

    def validate_purchase(
        self,
        portfolio: PortfolioState,
        *,
        symbol: str,
        sector: str,
        order_value: Decimal,
        avg_daily_dollar_volume: Decimal,
    ) -> None:
        if portfolio.equity <= 0:
            raise PolicyViolation("Portfolio equity must be positive.")
        if order_value > self.max_order_value:
            raise PolicyViolation("Purchase exceeds the maximum order value.")
        current_symbol_value = portfolio.position_weights.get(symbol, Decimal("0")) * portfolio.equity
        if current_symbol_value + order_value > self.max_symbol_exposure:
            raise PolicyViolation("Purchase would exceed the maximum total exposure for this symbol.")
        if portfolio.open_positions >= self.max_open_positions and symbol not in portfolio.position_weights:
            raise PolicyViolation("Maximum number of open positions has been reached.")
        if portfolio.realized_pnl_today <= -self.max_daily_loss:
            raise PolicyViolation("Daily loss limit reached; new purchases are disabled.")
        if portfolio.realized_pnl_week <= -self.max_weekly_loss:
            raise PolicyViolation("Weekly loss limit reached; new purchases are disabled.")
        if order_value > portfolio.available_for_new_orders:
            raise PolicyViolation(
                f"Insufficient buying power after settled-cash and pending-commitment checks: "
                f"${portfolio.available_for_new_orders} available, "
                f"but ${order_value} is required. No approval was created."
            )
        resulting_weight = portfolio.position_weights.get(symbol, Decimal("0")) + order_value / portfolio.equity
        if resulting_weight > self.max_position_weight:
            raise PolicyViolation("Purchase would exceed the maximum position weight.")
        resulting_sector = portfolio.sector_weights.get(sector, Decimal("0")) + order_value / portfolio.equity
        if resulting_sector > self.max_sector_weight:
            raise PolicyViolation("Purchase would exceed the maximum sector weight.")
        if (portfolio.settled_cash_available - order_value) / portfolio.equity < self.min_cash_weight:
            raise PolicyViolation("Purchase would breach the minimum cash reserve.")
        if portfolio.settled_cash_available - order_value < self.min_cash_dollars:
            raise PolicyViolation("Purchase would breach the minimum cash-dollar reserve.")
        if portfolio.approved_capital_today + order_value > self.max_daily_approved_capital:
            raise PolicyViolation("Purchase would exceed the daily approved-capital limit.")
        if portfolio.pending_approvals >= self.max_pending_approvals:
            raise PolicyViolation("Too many approvals are already pending.")
        if avg_daily_dollar_volume <= 0 or order_value / avg_daily_dollar_volume > self.max_order_pct_avg_volume:
            raise PolicyViolation("Order is too large relative to average daily dollar volume.")
