from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Callable
from urllib.error import URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import websockets

logger = logging.getLogger(__name__)

EXCHANGE_API = "https://api.exchange.coinbase.com"
WS_URI = "wss://ws-feed.exchange.coinbase.com"


def load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def coinbase_auth_fields() -> dict[str, str]:
    api_key = os.getenv("COINBASE_API_KEY")
    api_secret = os.getenv("COINBASE_API_SECRET")
    passphrase = os.getenv("COINBASE_PASSPHRASE")
    if not all([api_key, api_secret, passphrase]):
        return {}

    timestamp = str(time.time())
    message = f"{timestamp}GET/users/self/verify"
    try:
        hmac_key = base64.b64decode(api_secret)
        signature = hmac.new(hmac_key, message.encode("utf-8"), hashlib.sha256).digest()
    except Exception as exc:
        logger.error("Error signing Coinbase WebSocket auth: %s", exc)
        return {}

    return {
        "signature": base64.b64encode(signature).decode().rstrip("\n"),
        "key": api_key or "",
        "passphrase": passphrase or "",
        "timestamp": timestamp,
    }


def get_json(url: str, timeout: int = 20):
    request = Request(url, headers={"User-Agent": "tapeworm/0.1"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_usd_products() -> list[str]:
    try:
        products = get_json(f"{EXCHANGE_API}/products")
    except (OSError, URLError, json.JSONDecodeError) as exc:
        logger.error("Error fetching products: %s", exc)
        return []
    return [
        item["id"]
        for item in products
        if item.get("quote_currency") == "USD"
        and item.get("status") == "online"
        and not item.get("trading_disabled")
    ]


def fetch_daily_candle_range(symbol: str) -> dict[str, float] | None:
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    query = urlencode(
        {
            "granularity": 86400,
            "start": day_start.isoformat(),
            "end": now.isoformat(),
        }
    )
    url = f"{EXCHANGE_API}/products/{quote(symbol, safe='')}/candles?{query}"
    try:
        candles = get_json(url)
    except (OSError, URLError, json.JSONDecodeError) as exc:
        logger.debug("Skipping daily candle for %s: %s", symbol, exc)
        return None

    try:
        candle = max(candles, key=lambda item: int(item[0]))
        low = float(candle[1])
        high = float(candle[2])
    except (TypeError, ValueError, IndexError) as exc:
        logger.debug("Bad daily candle payload for %s: %s", symbol, exc)
        return None

    return {"low": low, "high": high, "range": high - low}


def fetch_rvol_data() -> list[dict]:
    products = fetch_usd_products()
    crypto_data: list[dict] = []
    for symbol in products:
        try:
            candles = get_json(f"{EXCHANGE_API}/products/{symbol}/candles?granularity=300")
            stats = get_json(f"{EXCHANGE_API}/products/{symbol}/stats")
        except (OSError, URLError, json.JSONDecodeError) as exc:
            logger.debug("Skipping RVol for %s: %s", symbol, exc)
            continue

        try:
            current_hour_volume = sum(float(candle[5]) for candle in candles[:12])
            prev_hour_volume = sum(float(candle[5]) for candle in candles[12:24])
            volume_24h = float(stats.get("volume", 0))
            volume_30d = float(stats.get("volume_30day", 0))
            last_price = float(stats.get("last", 0))
            if volume_24h <= 0 or volume_30d <= 0:
                continue

            avg_daily_volume = volume_30d / 30
            daily_rvol = volume_24h / avg_daily_volume
            hourly_rvol = current_hour_volume / (avg_daily_volume / 24)
            hour_change = current_hour_volume / prev_hour_volume if prev_hour_volume > 0 else 1
            weighted_rvol = daily_rvol * 0.5 + hourly_rvol * 0.3 + hour_change * 0.2
        except (ValueError, TypeError, IndexError) as exc:
            logger.debug("Bad RVol payload for %s: %s", symbol, exc)
            continue

        if weighted_rvol > 0:
            crypto_data.append(
                {
                    "Symbol": symbol,
                    "RVol": round(weighted_rvol, 2),
                    "Volume24h": round(volume_24h * last_price, 2),
                    "HourlyRVol": round(hourly_rvol, 2),
                    "DailyRVol": round(daily_rvol, 2),
                    "HourChange": round(hour_change, 2),
                }
            )
    return crypto_data


async def websocket_loop(
    product_ids: Callable[[], list[str]],
    channels: Callable[[list[str]], list],
    on_message: Callable[[dict], None],
) -> None:
    while True:
        products = product_ids()
        if not products:
            await asyncio.sleep(5)
            continue

        subscribe_message = {
            "type": "subscribe",
            "product_ids": products,
            "channels": channels(products),
        }
        subscribe_message.update(coinbase_auth_fields())

        try:
            async with websockets.connect(WS_URI, max_size=None, ping_interval=None) as websocket:
                await websocket.send(json.dumps(subscribe_message))
                async for raw_message in websocket:
                    on_message(json.loads(raw_message))
        except Exception as exc:
            logger.error("Coinbase WebSocket error: %s", exc)
            await asyncio.sleep(2)
