#!/usr/bin/env python3
"""Structured Alpaca market-data access diagnostics (read-only, no secrets).

Classifies HTTP failures from the actual provider response instead of treating
all 403s alike. Never bypasses entitlements, never fabricates bars, and never
substitutes cached/synthetic prices for current authorized data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable
from urllib.error import HTTPError, URLError

ENTITLEMENT_MESSAGE = "subscription does not permit querying recent"

LATEST_ENDPOINTS = ("snapshot", "trades/latest", "quotes/latest")
HISTORICAL_ENDPOINTS = ("bars", "trades", "quotes")


@dataclass
class AccessResult:
    endpoint: str
    category: str
    status: int | None = None
    feed: str = "unknown"
    symbol: str = ""
    provider_message: str = ""
    retrieved_at: str = ""
    attempts: int = 1
    credential_context: str = "not supplied"
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_http(status: int | None, body: str, *, endpoint: str = "",
                  feed: str = "unknown", symbol: str = "") -> str:
    """Classify a provider failure from status + body, not status alone."""
    text = str(body or "").lower()
    if status == 403 and ENTITLEMENT_MESSAGE in text:
        return "entitlement_realtime_sip"
    if status == 403 and "subscription" in text and "invalid" not in text:
        return "entitlement_data_access"
    if status == 400 and "invalid feed" in text:
        return "unsupported_feed"
    if status in (401, 403):
        return "authentication_or_permission"
    if status == 429:
        return "rate_limited"
    if status is not None and 500 <= status < 600:
        return "provider_outage"
    if status is None:
        return "connection_failure"
    if 200 <= status < 300:
        return "ok"
    return f"http_{status}"


def validate_feed(feed: str, *, endpoint: str) -> tuple[bool, str]:
    """Reject unsupported / misleading feed combinations before any request."""
    value = str(feed or "").strip().lower()
    if value not in {"iex", "sip", "delayed_sip"}:
        return False, f"unsupported feed: {feed!r}"
    base = str(endpoint or "").strip().lower()
    if "bars" in base and value == "delayed_sip":
        return False, "delayed_sip is not valid for historical bars"
    if value == "sip" and any(name in base for name in LATEST_ENDPOINTS):
        return True, ("real-time SIP requires an entitled subscription; "
                      "403 will be classified, never silently retried on SIP")
    if value == "delayed_sip":
        return True, "consolidated data delayed ~15 minutes; not real-time"
    return True, "feed supported"


def describe_feed(feed: str, *, endpoint: str = "") -> str:
    ok, note = validate_feed(feed, endpoint=endpoint)
    return f"feed={feed} ({note if ok else 'INVALID: ' + note})"


def probe(request_fn: Callable[[str, str, str], tuple[int | None, str, dict]],
          *, endpoint: str, feed: str, symbol: str,
          now: Callable[[], str] = utc_now) -> AccessResult:
    """Run one read-only request through an injected transport.

    ``request_fn(endpoint, feed, symbol)`` returns ``(status, body, headers)``.
    No credentials are read or emitted here; the caller owns auth context.
    """
    valid, note = validate_feed(feed, endpoint=endpoint)
    if not valid:
        return AccessResult(endpoint=endpoint, category="unsupported_feed",
                            feed=feed, symbol=symbol, provider_message=note,
                            retrieved_at=now())
    status, body, headers = request_fn(endpoint, feed, symbol)
    category = classify_http(status, body, endpoint=endpoint, feed=feed, symbol=symbol)
    return AccessResult(endpoint=endpoint, category=category, status=status,
                        feed=feed, symbol=symbol,
                        provider_message=str(body or "")[:300], retrieved_at=now(),
                        evidence={"headers": {
                            k: v for k, v in dict(headers or {}).items()
                            if str(k).lower().startswith("x-feed") or
                            str(k).lower().startswith("x-ratelimit")}})


def read_only_health_report(results: list[AccessResult], *, note: str = "") -> dict:
    """Fail-closed summary; never claims current prices from cached data."""
    return {
        "track": "alpaca_market_data_health",
        "generated_at": utc_now(),
        "results": [r.as_dict() for r in results],
        "categories": {r.endpoint: r.category for r in results},
        "permitted_latest_feeds": sorted({r.feed for r in results
                                          if r.category == "ok" and r.status == 200}),
        "orders_submitted": 0,
        "entry_permission": "none - diagnostics only",
        "note": note or ("Classification only. Cached or synthetic prices are never "
                         "current authorized market data."),
    }


def from_http_error(exc: HTTPError, *, endpoint: str, feed: str, symbol: str) -> AccessResult:
    body = ""
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    return AccessResult(endpoint=endpoint,
                        category=classify_http(exc.code, body, endpoint=endpoint,
                                               feed=feed, symbol=symbol),
                        status=exc.code, feed=feed, symbol=symbol,
                        provider_message=body[:300], retrieved_at=utc_now(),
                        evidence={"exception": type(exc).__name__})


def from_url_error(exc: URLError, *, endpoint: str, feed: str, symbol: str) -> AccessResult:
    return AccessResult(endpoint=endpoint, category="connection_failure",
                        feed=feed, symbol=symbol,
                        provider_message=f"{type(exc).__name__}: {exc.reason}"[:300],
                        retrieved_at=utc_now())
