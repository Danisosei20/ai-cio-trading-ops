#!/usr/bin/env python3
"""Broker-verified short eligibility for new short entries (ADR 0002, DRAFT).

Uses the account's own Alpaca paper transport and the supported REST fields:
``/v2/assets/{symbol}`` (``shortable`` + ``borrow_status``) and ``/v2/account``
(``shorting_enabled`` and restriction flags). ``easy_to_borrow`` is deprecated
and deliberately ignored. No locate/available-to-borrow quantity is invented.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

BROKER = "alpaca"
DEFAULT_MAX_AGE_SECONDS = 120


def _result(symbol: str, *, now: datetime, reason: str = "", eligible: bool = False,
            shortable: Any = None, borrow_status: Any = None, source: str = BROKER,
            retrieved_at: datetime | None = None, account: str = "") -> dict:
    return {
        "symbol": str(symbol).strip().upper(),
        "broker": BROKER,
        "account": account,
        "shortable": shortable,
        "borrow_status": borrow_status,
        "available_to_borrow": None,
        "data_source": source,
        "retrieved_at": (retrieved_at or now).isoformat(),
        "max_age_seconds": DEFAULT_MAX_AGE_SECONDS,
        "eligible": bool(eligible),
        "reason": reason or ("eligible" if eligible else "not verified"),
    }


def _stamp(value: Any, now: datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return now


def validate_short_eligibility(symbol: str, *, account: dict, asset: dict,
                               now: datetime | None = None,
                               retrieved_at: datetime | None = None,
                               max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS) -> dict:
    """Validate broker facts; any gap or disagreement rejects the short entry."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    result = _result(symbol, now=now, account=str(account.get("id", "")))
    result["max_age_seconds"] = max_age_seconds

    if retrieved_at is not None:
        retrieved = _stamp(retrieved_at, now)
        if (now - retrieved) > timedelta(seconds=max_age_seconds):
            result["reason"] = "stale borrow evidence"
            return result

    asset_symbol = str(asset.get("symbol", "")).strip().upper()
    if asset_symbol != result["symbol"]:
        result["reason"] = "symbol mismatch in broker asset record"
        return result

    if account.get("shorting_enabled") is not True:
        result["reason"] = "account short permission not verified"
        return result
    for flag in ("trading_blocked", "account_blocked", "trade_suspended_by_user"):
        if account.get(flag) is True:
            result["reason"] = f"account restricted: {flag}"
            return result
    if str(account.get("status", "")).upper() != "ACTIVE":
        result["reason"] = "account not active"
        return result

    if str(asset.get("status", "")).lower() != "active" or asset.get("tradable") is not True:
        result["reason"] = "asset not active/tradable"
        return result
    result["shortable"] = asset.get("shortable")
    if asset.get("shortable") is not True:
        result["reason"] = "asset not shortable"
        return result
    result["borrow_status"] = asset.get("borrow_status")
    if result["borrow_status"] != "easy_to_borrow":
        result["reason"] = "borrow status missing or not easy_to_borrow"
        return result

    result["eligible"] = True
    result["reason"] = "eligible: shortable and easy_to_borrow"
    return result


def fetch_short_eligibility(symbol: str, transport: Any, *,
                            now: datetime | None = None,
                            max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS) -> dict:
    """Fetch broker facts via the existing paper transport; fail closed on error."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    clean = str(symbol).strip().upper()
    try:
        asset = transport.request("GET", f"/v2/assets/{quote(clean)}")
        account = transport.request("GET", "/v2/account")
    except Exception as exc:  # noqa: BLE001 - broker failure is never an approval
        return _result(clean, now=now, reason=f"broker error: {type(exc).__name__}: {exc}")
    if not isinstance(asset, dict) or not isinstance(account, dict):
        return _result(clean, now=now, reason="broker response malformed")
    result = validate_short_eligibility(
        clean, account=account, asset=asset, now=now,
        max_age_seconds=max_age_seconds)
    result["account"] = str(account.get("id", result["account"]))
    return result


def short_entry_allowed(result: dict) -> bool:
    return bool(result.get("eligible"))
