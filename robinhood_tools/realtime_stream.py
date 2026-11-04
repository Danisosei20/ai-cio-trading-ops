#!/usr/bin/env python3
"""Real-time market streaming via Alpaca WebSockets.

Connects to Alpaca's data websocket for real-time quotes, trades, bars, and
order updates. Provides async callback interface for the intraday scanner to
react to confirmed setups without polling.

Requires: websockets, aiohttp

Usage:
  from robinhood_tools.realtime_stream import RealtimeStream
  stream = RealtimeStream(api_key, secret_key)
  stream.subscribe_quotes(["NVDA", "AAPL"])
  stream.subscribe_trades(["NVDA"])
  stream.subscribe_bars(["NVDA", "QQQ"])
  stream.subscribe_orders()
  stream.run()  # blocks, calls callbacks on messages
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
from typing import Callable, Optional

import websockets

logger = logging.getLogger(__name__)


class RealtimeStream:
    """WebSocket client for Alpaca real-time market data."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        feed: str = "iex",  # "iex" for free tier, "sip" for paid
    ) -> None:
        self.api_key = api_key
        self.secret_key = secret_key
        self.feed = feed
        self.ws_url = "wss://stream.data.alpaca.markets/v2/iex" if feed == "iex" else "wss://stream.data.alpaca.markets/v2/sip"

        self._websocket: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
        self._quote_callbacks: list[Callable[[dict], None]] = []
        self._trade_callbacks: list[Callable[[dict], None]] = []
        self._bar_callbacks: list[Callable[[dict], None]] = []
        self._trade_update_callbacks: list[Callable[[dict], None]] = []
        self._subscribed_symbols: set[str] = set()
        self._running = False

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
        if auth_data.get([0], {}).get("code") != 200 if isinstance(auth_data, list) else auth_data.get("code") != 200:
            raise RuntimeError(f"Authentication failed: {auth_data}")

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

    async def _run_loop(self) -> None:
        """Main message processing loop."""
        try:
            await self._connect()
            logger.info("Connected to Alpaca data stream")

            async for message in self._websocket:
                data = json.loads(message)
                await self._handle_message(data)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket connection closed")
            self._connected = False
        except Exception as exc:
            logger.error(f"WebSocket error: {exc}")
            self._connected = False
        finally:
            self._running = False

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
        return self._connected and self._websocket is not None and self._websocket.open

    def stop(self) -> None:
        """Stop the stream."""
        self._running = False
        if self._websocket:
            asyncio.create_task(self._websocket.close())


if __name__ == "__main__":
    # Example usage (requires API keys)
    # stream = RealtimeStream("YOUR_API_KEY", "YOUR_SECRET_KEY")
    # stream.subscribe_quotes(["NVDA", "AAPL"])
    # stream.on_quote(lambda msg: print(f"Quote: {msg}"))
    # stream.run()
    print("RealtimeStream module ready")
