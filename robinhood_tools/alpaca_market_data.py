from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .errors import AuthorizationRequired, ConnectorUnavailable, PolicyViolation


ALPACA_DATA_BASE_URL = "https://data.alpaca.markets"


class AlpacaMarketDataHttpClient:
    """Read-only Alpaca stock/news data client with an immutable official base URL."""

    def __init__(self, key_id: str, secret_key: str, *, base_url: str = ALPACA_DATA_BASE_URL):
        if not key_id.strip() or not secret_key.strip():
            raise AuthorizationRequired("Alpaca market-data credentials are required.")
        if base_url.rstrip("/") != ALPACA_DATA_BASE_URL:
            raise PolicyViolation("Alpaca market data must use https://data.alpaca.markets exactly.")
        self._key_id = key_id
        self._secret_key = secret_key

    @classmethod
    def from_values(cls, values: dict[str, str]) -> AlpacaMarketDataHttpClient:
        return cls(
            values.get("ALPACA_API_KEY", ""),
            values.get("ALPACA_SECRET_KEY", ""),
            base_url=values.get("ALPACA_DATA_BASE_URL", ALPACA_DATA_BASE_URL),
        )

    def stock_snapshot(self, symbol: str, *, feed: str = "iex") -> dict[str, Any]:
        normalized = _symbol(symbol)
        payload = self._get(f"/v2/stocks/{normalized}/snapshot", {"feed": _feed(feed)})
        if not isinstance(payload, dict):
            raise ConnectorUnavailable("Alpaca stock snapshot response was invalid.")
        return payload

    def stock_bars(
        self, symbol: str, *, timeframe: str, start: str, end: str | None = None,
        limit: int = 1000, feed: str = "iex",
    ) -> list[dict[str, Any]]:
        if not timeframe.strip() or not start.strip() or not 1 <= limit <= 10_000:
            raise PolicyViolation("Stock bars require timeframe, start, and a limit from 1 to 10000.")
        params: dict[str, str | int] = {
            "timeframe": timeframe, "start": start, "limit": limit,
            "feed": _feed(feed), "adjustment": "all", "sort": "asc",
        }
        if end:
            params["end"] = end
        payload = self._get(f"/v2/stocks/{_symbol(symbol)}/bars", params)
        bars = payload.get("bars") if isinstance(payload, dict) else None
        if not isinstance(bars, list):
            raise ConnectorUnavailable("Alpaca stock bars response was invalid.")
        return bars

    def news(
        self, symbol: str, *, start: str, end: str | None = None, limit: int = 50,
    ) -> list[dict[str, Any]]:
        if not start.strip() or not 1 <= limit <= 50:
            raise PolicyViolation("News requires start and a limit from 1 to 50.")
        params: dict[str, str | int] = {
            "symbols": _symbol(symbol), "start": start, "limit": limit, "sort": "desc",
        }
        if end:
            params["end"] = end
        payload = self._get("/v1beta1/news", params)
        news = payload.get("news") if isinstance(payload, dict) else None
        if not isinstance(news, list):
            raise ConnectorUnavailable("Alpaca news response was invalid.")
        return news

    def _get(self, path: str, params: dict[str, str | int]) -> Any:
        if not path.startswith(("/v2/stocks/", "/v1beta1/news")):
            raise PolicyViolation("Unsupported Alpaca read-only market-data path.")
        url = f"{ALPACA_DATA_BASE_URL}{path}?{urlencode(params)}"
        request = Request(
            url,
            method="GET",
            headers={
                "APCA-API-KEY-ID": self._key_id,
                "APCA-API-SECRET-KEY": self._secret_key,
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                body = response.read()
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise AuthorizationRequired("Alpaca rejected market-data access for these credentials.") from exc
            raise ConnectorUnavailable(f"Alpaca market data returned HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ConnectorUnavailable("Alpaca market data could not be reached.") from exc
        return json.loads(body) if body else {}


def _symbol(value: str) -> str:
    symbol = value.strip().upper()
    if not symbol or not all(character.isalnum() or character in {".", "-"} for character in symbol):
        raise PolicyViolation("A valid stock symbol is required.")
    return symbol


def _feed(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"iex", "sip", "delayed_sip"}:
        raise PolicyViolation("Stock feed must be iex, sip, or delayed_sip.")
    return normalized
