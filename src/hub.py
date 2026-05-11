from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import defaultdict
from typing import Any

import websockets

from .coinbase import (
    coinbase_auth_fields,
    fetch_daily_candle_range,
    fetch_rvol_data,
    fetch_usd_products,
    load_env_file,
    websocket_loop,
)
from .shared import normalize_asset

logger = logging.getLogger(__name__)


class MarketDataHub:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: dict[Any, set[str]] = {}
        self.market_symbols: set[str] = set()
        self.daily_range_symbols: set[str] = set()
        self.started_market_feeds: set[str] = set()
        self.screener_started = False
        self.lock = threading.Lock()

    async def serve_forever(self) -> None:
        load_env_file()
        self.loop = asyncio.get_running_loop()
        async with websockets.serve(self.handle_client, self.host, self.port):
            logger.info("tapeworm hub listening on ws://%s:%s", self.host, self.port)
            await asyncio.Future()

    async def handle_client(self, websocket, _path: str | None = None) -> None:
        self.clients[websocket] = set()
        try:
            async for raw_message in websocket:
                message = json.loads(raw_message)
                if message.get("type") != "subscribe":
                    continue
                topics = {str(topic) for topic in message.get("topics", [])}
                self.clients[websocket] = topics
                self.ensure_topics(topics)
        except Exception as exc:
            logger.debug("Hub client disconnected: %s", exc)
        finally:
            self.clients.pop(websocket, None)

    def ensure_topics(self, topics: set[str]) -> None:
        if "screener:*" in topics:
            self.ensure_screener()

        for topic in topics:
            if ":" not in topic:
                continue
            prefix, symbol = topic.split(":", 1)
            if prefix in {"l2", "ts"} and symbol and symbol != "*":
                self.ensure_market(normalize_asset(symbol))

    def ensure_screener(self) -> None:
        with self.lock:
            if self.screener_started:
                return
            self.screener_started = True
        threading.Thread(target=self._run_screener_ticker_feed, daemon=True).start()
        threading.Thread(target=self._run_rvol_feed, daemon=True).start()

    def ensure_market(self, symbol: str) -> None:
        with self.lock:
            if symbol in self.started_market_feeds:
                return
            self.started_market_feeds.add(symbol)
        threading.Thread(target=self._run_market_feed, args=(symbol,), daemon=True).start()
        threading.Thread(target=self._run_daily_range_feed, args=(symbol,), daemon=True).start()

    def _run_screener_ticker_feed(self) -> None:
        products_cache: list[str] = []

        def products() -> list[str]:
            nonlocal products_cache
            if not products_cache:
                products_cache = fetch_usd_products()
            return products_cache

        def channels(_: list[str]) -> list:
            return ["ticker"]

        asyncio.run(websocket_loop(products, channels, lambda message: self.publish("screener:*", "ticker", message)))

    def _run_market_feed(self, symbol: str) -> None:
        def products() -> list[str]:
            return [symbol]

        def channels(product_ids: list[str]) -> list:
            level2_channel = "level2" if coinbase_auth_fields() else "level2_batch"
            return [
                {"name": "matches", "product_ids": product_ids},
                {"name": "ticker", "product_ids": product_ids},
                {"name": level2_channel, "product_ids": product_ids},
            ]

        asyncio.run(websocket_loop(products, channels, lambda message: self.publish_market(symbol, message)))

    def _run_rvol_feed(self) -> None:
        while True:
            self.publish("screener:*", "rvol", fetch_rvol_data())
            time.sleep(300)

    def _run_daily_range_feed(self, symbol: str) -> None:
        while True:
            candle = fetch_daily_candle_range(symbol)
            self.publish(f"l2:{symbol}", "daily_range", [symbol, candle])
            self.publish(f"ts:{symbol}", "daily_range", [symbol, candle])
            time.sleep(60)

    def publish_market(self, symbol: str, message: dict) -> None:
        msg_type = message.get("type")
        if msg_type in {"snapshot", "l2update", "ticker", "error"}:
            self.publish(f"l2:{symbol}", "market", message)
        if msg_type in {"match", "ticker", "error"}:
            self.publish(f"ts:{symbol}", "market", message)

    def publish(self, topic: str, event: str, payload: object) -> None:
        if self.loop is None:
            return
        message = {"topic": topic, "event": event, "payload": payload}
        asyncio.run_coroutine_threadsafe(self.broadcast(topic, message), self.loop)

    async def broadcast(self, topic: str, message: dict) -> None:
        encoded = json.dumps(message)
        dead = []
        for websocket, topics in list(self.clients.items()):
            if topic in topics or "screener:*" in topics and topic == "screener:*":
                try:
                    await websocket.send(encoded)
                except Exception:
                    dead.append(websocket)
        for websocket in dead:
            self.clients.pop(websocket, None)


def run_hub(host: str = "127.0.0.1", port: int = 8765) -> None:
    asyncio.run(MarketDataHub(host=host, port=port).serve_forever())
