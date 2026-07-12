from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class MonthlyMetrics:
    portfolio_return: Decimal
    benchmark_return: Decimal
    max_drawdown: Decimal
    win_rate: Decimal
    payoff_ratio: Decimal
    execution_slippage: Decimal
    thesis_accuracy: Decimal
    rejected_ideas: int
    avoided_losses: int
    strategy_changes: int

    @property
    def excess_return(self) -> Decimal:
        return self.portfolio_return - self.benchmark_return


def monthly_report(metrics: MonthlyMetrics) -> dict:
    return {
        "portfolio_return": str(metrics.portfolio_return),
        "benchmark_return": str(metrics.benchmark_return),
        "excess_return": str(metrics.excess_return),
        "max_drawdown": str(metrics.max_drawdown),
        "win_rate": str(metrics.win_rate),
        "payoff_ratio": str(metrics.payoff_ratio),
        "execution_slippage": str(metrics.execution_slippage),
        "thesis_accuracy": str(metrics.thesis_accuracy),
        "rejected_ideas": metrics.rejected_ideas,
        "avoided_losses": metrics.avoided_losses,
        "strategy_changes": metrics.strategy_changes,
    }


def export_audit_bundle(payload: dict, destination: str | Path) -> tuple[Path, str]:
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, default=str, sort_keys=True, separators=(",", ":")).encode()
    output.write_bytes(encoded + b"\n")
    digest = hashlib.sha256(encoded).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    return output, digest
