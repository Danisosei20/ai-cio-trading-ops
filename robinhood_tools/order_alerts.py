"""One Slack message for every order the desk actually places.

The desk's notification story had a hole. The rich post-trade summary lives
in `PaperAutoExecutor._notify_after_purchase` (the approval-flow equity
path), but the four paths that actually trade today — options entries, the
options guard's exits, equity signal orders, the 15:45 flatten — placed
orders with no Slack message at all. The operator only saw what the
end-of-run summaries happened to include, and only after the run finished.

`post_order_alert()` is the single seam. Each placement point calls it
immediately after the broker confirms the order. It never raises: a Slack
outage must not turn a placed order into a crash, and the order already
lives in the ledger, the audit log, and the broker.
"""
from __future__ import annotations

from decimal import Decimal


def _money(value) -> str:
    try:
        return f"${Decimal(str(value)):,.2f}"
    except Exception:  # noqa: BLE001 - formatting is cosmetic, never fatal
        return f"${value}"


def _int(value) -> str:
    try:
        return str(int(Decimal(str(value))))
    except Exception:  # noqa: BLE001
        return str(value)


def option_message(*, side: str, contract, qty, limit, premium=None,
                   reason: str = "", order_id: str = "",
                   approval_id: str = "") -> str:
    """Compact record of one option order (opener or sell-to-close)."""
    if side == "buy":
        verb, label = "BUY", "opener"
    else:
        verb, label = "SELL-TO-CLOSE", "exit"
    lines = [
        f"*AI CIO — PAPER ORDER PLACED* ({label})",
        f"• `{contract}` — {verb} x{_int(qty)} @ {limit} limit",
    ]
    if premium is not None:
        lines.append(f"• Premium: {_money(premium)}")
    if reason:
        lines.append(f"• Reason: {reason}")
    if order_id:
        lines.append(f"• Broker order: `{order_id}`")
    if approval_id:
        lines.append(f"• Approval: `{approval_id}`")
    lines.append("_Alpaca paper only — no live funds._")
    return "\n".join(lines)


def equity_message(*, side: str, symbol, qty, limit, stop=None, target=None,
                   order_id: str = "", approval_id: str = "") -> str:
    """Compact record of one equity order (buy entry or sell exit)."""
    verb = "BUY" if side == "buy" else "SELL"
    label = "opener" if side == "buy" else "exit"
    lines = [
        f"*AI CIO — PAPER ORDER PLACED* ({label})",
        f"• *{str(symbol).upper()}* — {verb} x{_int(qty)} @ {limit} limit",
    ]
    if stop is not None:
        lines.append(f"• Bracket stop: {stop}")
    if target is not None:
        lines.append(f"• Take profit: {target}")
    if order_id:
        lines.append(f"• Broker order: `{order_id}`")
    if approval_id:
        lines.append(f"• Approval: `{approval_id}`")
    lines.append("_Alpaca paper only — no live funds._")
    return "\n".join(lines)


def post_order_alert(*, settings, message: str, env_path: str = ".env") -> str:
    """Send one order alert. Returns 'sent'/'failed'; never raises.

    A failed alert is recorded nowhere critical on purpose: the order is
    already in the ledger, and blocking a trade on a chat message would be
    exactly backwards.
    """
    try:
        from .settings import load_env
        from .slack_web_api import SlackWebApiNotifier

        notifier = SlackWebApiNotifier.from_values(load_env(env_path))
        notifier.send_approval(channel_id=settings.channel_id, message=message)
        return "sent"
    except Exception:  # noqa: BLE001 - Slack must never break the order path
        return "failed"