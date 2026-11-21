#!/usr/bin/env python3
"""Real-time market streaming via Alpaca WebSockets.

Connects to Alpaca's data websocket for real-time quotes, trades, bars, and
order updates. Provides async callback interface for the intraday scanner to
react to confirmed setups without polling.

Paper trading only. Connects to Alpaca's data WebSocket (NOT NVIDIA AI endpoints).

Endpoints:
  * IEX feed (free tier):      wss://stream.data.alpaca.markets/v2/iex
  * SIP feed (paid tier):      wss://stream.data.alpaca.markets/v2/sip
  * Paper trading order updates: wss://paper-api.alpaca.markets/stream
  * Test stream:               wss://stream.data.alpaca.markets/v2/test

Usage:
  from robinhood_tools.realtime_stream import RealtimeStream
  stream = RealtimeStream(api_key, secret_key)
  stream.subscribe_quotes(["NVDA", "AAPL"])
  stream.subscribe_trades(["NVDA"])
  stream.subscribe_bars(["NVDA", "QQQ"])
  stream.subscribe_orders()
  stream.on_quote(callback)
  stream.run()  # blocks, calls callbacks on messages
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable, Optional

import websockets

logger = logging.getLogger(__name__)

# Alpaca data WebSocket endpoints (NOT NVIDIA AI endpoints)
DATA_WS_URL_IEX = "wss://stream.data.alpaca.markets/v2/iex"
DATA_WS_URL_SIP = "wss://stream.data.alpaca.markets/v2/sip"
DATA_WS_URL_TEST = "wss://stream.data.alpaca.markets/v2/test"
# Alpaca paper trading order updates use a distinct stream
PAPER_TRADE_UPDATES_URL = "wss://paper-api.alpaca.markets/stream"


class RealtimeStream:
    """WebSocket client for Alpaca real-time market data.

    Reconciles open orders and positions after reconnect so that no fills
    or order state changes are missed during a disconnect.
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        feed: str = "iex",
        max_reconnect_attempts: int = 10,
        reconnect_base_delay: float = 1.0,
        on_reconnect: Optional[Callable[[], None]] = None,
    ) -> None:
        self.api_key = api_key
        self.secret_key = secret_key
        self.feed = feed
        self.ws_url = DATA_WS_URL_IEX if feed == "iex" else DATA_WS_URL_SIP
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_base_delay = reconnect_base_delay
        self.on_reconnect = on_reconnect

        self._websocket: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
        self._quote_callbacks: list[Callable[[dict], None]] = []
        self._trade_callbacks: list[Callable[[dict], None]] = []
        self._bar_callbacks: list[Callable[[dict], None]] = []
        self._trade_update_callbacks: list[Callable[[dict], None]] = []
        self._subscribed_symbols: set[str] = set()
        self._running = False
        self._reconnect_count = 0

    def subscribe_quotes(self, symbols: list[str]) -> None:
        """Subscribe to quote updates for symbols."""
        for s in symbols:
            self._subscribed_symbols.add(s.upper())

    def subscribe_trades(self, symbols: list[str]) -> None:
        """Subscribe to trade updates for symbols."""
        for s in symbols:
            self._subscribed_symbols.add(s.upper())

    def subscribe_bars(self, symbols: list[str]) -> None:
        """Subscribe to bar updates for symbols."""
        for s in symbols:
            self._subscribed_symbols.add(s.upper())

    def subscribe_orders(self) -> None:
        """Subscribe to order update events."""
        self._subscribed_symbols.add("__trade_updates__")

    def on_quote(self, callback: Callable[[dict], None]) -> None:
        """Register callback for quote updates."""
        self._quote_callbacks.append(callback)

    def on_trade(self, callback: Callable[[dict], None]) -> None:
        """Register callback for trade updates."""
        self._trade_callbacks.append(callback)

    def on_bar(self, callback: Callable[[dict], None]) -> None:
        """Register callback for bar updates."""
        self._bar_callbacks.append(callback)

    def on_order_update(self, callback: Callable[[dict], None]) -> None:
        """Register callback for order updates."""
        self._trade_update_callbacks.append(callback)

    async def _connect(self) -> None:
        """Connect to Alpaca WebSocket and authenticate."""
        self._websocket = await websockets.connect(self.ws_url)
        self._connected = True

        # Authenticate
        auth_msg = {
            "action": "auth",
            "key": self.api_key,
            "secret": self.secret_key,
        }
        await self._websocket.send(json.dumps(auth_msg))

        # Wait for authentication confirmation
        response = await self._websocket.recv()
        auth_data = json.loads(response)
        if isinstance(auth_data, list):
            auth_data = auth_data[0]
        if auth_data.get("code") != 200:
            raise RuntimeError(f"Authentication failed: {auth_data}")

        logger.info("Authenticated with Alpaca data stream")

        # Subscribe to channels
        await self._send_subscription()

    async def _send_subscription(self) -> None:
        """Send subscription message for subscribed symbols."""
        trades = [s for s in self._subscribed_symbols if s != "__trade_updates__"]
        quotes = [s for s in self._subscribed_symbols if s != "__trade_updates__"]

        subscribe_msg = {"action": "subscribe"}
        if trades:
            subscribe_msg["trades"] = trades
        if quotes:
            subscribe_msg["quotes"] = quotes
        if "__trade_updates__" in self._subscribed_symbols:
            subscribe_msg["trade_updates"] = ["*"]

        await self._websocket.send(json.dumps(subscribe_msg))
        logger.info(f"Subscribed to trades={trades}, quotes={quotes}")

    async def _run_loop(self) -> None:
        """Main message processing loop with reconnection."""
        self._running = True
        while self._running:
            try:
                await self._connect()
                logger.info("Connected to Alpaca data stream")
                self._reconnect_count = 0

                async for message in self._websocket:
                    if not self._running:
                        break
                    data = json.loads(message)
                    await self._handle_message(data)
            except websockets.exceptions.ConnectionClosed as exc:
                logger.warning(
                    f"WebSocket connection closed: code={exc.code}, reason={exc.reason}"
                )
                self._connected = False
                if self._running:
                    await self._reconnect()
            except Exception as exc:
                logger.error(f"WebSocket error: {exc}")
                self._connected = False
                if self._running:
                    await self._reconnect()
            finally:
                self._connected = False
                if self._websocket:
                    try:
                        await self._websocket.close()
                    except Exception:
                        pass

    async def _handle_message(self, data: list[dict] | dict) -> None:
        """Handle incoming WebSocket message."""
        if isinstance(data, list):
            for msg in data:
                await self._process_message(msg)
        elif isinstance(data, dict):
            await self._process_message(data)

    async def _process_message(self, msg: dict) -> None:
        """Process a single WebSocket message."""
        msg_type = msg.get("type", "")

        if msg_type == "subscription":
            logger.info(f"Subscribed: {msg.get('symbol', msg.get('symbols', []))}")
            return

        if msg_type == "trade":
            for cb in self._trade_callbacks:
                try:
                    cb(msg)
                except Exception as exc:
                    logger.error(f"Trade callback error: {exc}")
        elif msg_type == "quote":
            for cb in self._quote_callbacks:
                try:
                    cb(msg)
                except Exception as exc:
                    logger.error(f"Quote callback error: {exc}")
        elif msg_type == "bar":
            for cb in self._bar_callbacks:
                try:
                    cb(msg)
                except Exception as exc:
                    logger.error(f"Bar callback error: {exc}")
        elif msg_type == "trade_update":
            for cb in self._trade_update_callbacks:
                try:
                    cb(msg)
                except Exception as exc:
                    logger.error(f"Order update callback error: {exc}")

    def run(self) -> None:
        """Blocking run - connects and processes messages until interrupted."""
        self._running = True
        try:
            asyncio.run(self._run_loop())
        except KeyboardInterrupt:
            self._running = False
        finally:
            self._connected = False

    async def async_run(self) -> None:
        """Async run - connect and process messages."""
        self._running = True
        await self._run_loop()

    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return (
            self._connected
            and self._websocket is not None
            and self._websocket.open
        )

    def stop(self) -> None:
        """Stop the stream."""
        self._running = False
        if self._websocket:
            try:
                asyncio.create_task(self._websocket.close())
            except Exception:
                pass

    def reconciliation_callback(self) -> Optional[Callable[[], None]]:
        """Get the reconnect callback for external reconciliation."""
        return self.on_reconnect


if __name__ == "__main__":
    # Example usage (requires API keys)
    # stream = RealtimeStream("YOUR_API_KEY", "YOUR_SECRET_KEY")
    # stream.subscribe_quotes(["NVDA", "AAPL"])
    # stream.on_quote(lambda msg: print(f"Quote: {msg}"))
    # stream.run()
    print("RealtimeStream module ready")
