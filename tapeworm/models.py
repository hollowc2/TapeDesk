from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable


def price_decimals(price: float) -> int:
    price = abs(price)
    if price < 0.0001:
        return 8
    if price < 0.01:
        return 6
    if price < 1:
        return 4
    if price >= 100_000:
        return 0
    return 2


def format_price_value(price: float) -> str:
    return f"{price:,.{price_decimals(price)}f}"


def format_price(price: float) -> str:
    return f"${format_price_value(price)}"


def format_volume(volume: float) -> str:
    if volume >= 1_000_000_000:
        return f"${volume / 1_000_000_000:.2f}B"
    if volume >= 1_000_000:
        return f"${volume / 1_000_000:.2f}M"
    if volume >= 1_000:
        return f"${volume / 1_000:.2f}K"
    return f"${volume:,.0f}"


def format_quantity(quantity: float) -> str:
    quantity_abs = abs(quantity)
    sign = "-" if quantity < 0 else ""
    if quantity_abs >= 1_000_000_000:
        return f"{sign}{quantity_abs / 1_000_000_000:.2f}B"
    if quantity_abs >= 1_000_000:
        return f"{sign}{quantity_abs / 1_000_000:.2f}M"
    if quantity_abs >= 1_000:
        return f"{sign}{quantity_abs / 1_000:.2f}K"
    if quantity_abs >= 1:
        return f"{quantity:.3f}"
    return f"{quantity:.6f}".rstrip("0").rstrip(".")


@dataclass
class Trade:
    symbol: str
    size: float
    price: float
    side: str
    time: datetime = field(default_factory=datetime.now)

    @property
    def time_label(self) -> str:
        return self.time.strftime("%H:%M:%S")

    @property
    def notional(self) -> float:
        return self.size * self.price


class TradeTracker:
    def __init__(
        self,
        max_recent: int = 12,
        max_top: int = 8,
        min_notional: float = 5_000,
        min_size: float | None = None,
    ):
        self.max_recent = max_recent
        self.max_top = max_top
        self.min_notional = min_notional
        self.min_size = min_size
        self.recent: list[Trade] = []
        self.top: list[Trade] = []

    def add(self, trade: Trade) -> bool:
        if self.min_size is not None and trade.size <= self.min_size:
            return False
        if trade.notional < self.min_notional:
            return False

        self.recent.insert(0, trade)
        self.recent = self.recent[: self.max_recent]
        self.top.append(trade)
        self.top.sort(key=lambda item: item.notional, reverse=True)
        self.top = self.top[: self.max_top]
        return True


class TpsTracker:
    def __init__(self, window_seconds: int = 30):
        self.window = timedelta(seconds=window_seconds)
        self.transactions: list[datetime] = []
        self.history: list[tuple[datetime, int]] = []
        self.highest = 0
        self.lowest: int | None = None
        self.total = 0
        self.count = 0
        self.last_sample = datetime.min

    def add_transaction(self, at: datetime | None = None) -> bool:
        now = at or datetime.now()
        self.transactions.append(now)
        cutoff = now - self.window
        self.transactions = [item for item in self.transactions if item > cutoff]

        if (now - self.last_sample).total_seconds() < 1:
            return False

        current = len(self.transactions)
        self.highest = max(self.highest, current)
        self.lowest = current if self.lowest is None else min(self.lowest, current)
        self.total += current
        self.count += 1
        self.history.append((now, current))
        self.history = [(at, tps) for at, tps in self.history if at > cutoff]
        self.last_sample = now
        return True

    @property
    def current(self) -> int:
        return self.history[-1][1] if self.history else 0

    @property
    def average(self) -> float:
        return self.total / self.count if self.count else 0

    def sparkline(self, width: int = 30) -> str:
        values = [value for _, value in self.history[-width:]]
        if not values:
            return "Collecting data..."
        blocks = "▁▂▃▄▅▆▇█"
        high = max(values)
        low = min(values)
        spread = high - low or 1
        return "".join(blocks[round((value - low) / spread * (len(blocks) - 1))] for value in values)


@dataclass
class BookSummary:
    best_bid: float | None = None
    best_ask: float | None = None
    spread: float | None = None
    bid_depth: float = 0
    ask_depth: float = 0
    imbalance: float = 0


@dataclass
class MarketStats:
    daily_high: float | None = None
    daily_low: float | None = None
    vwap_notional: float = 0
    vwap_volume: float = 0

    @property
    def vwap(self) -> float | None:
        if self.vwap_volume <= 0:
            return None
        return self.vwap_notional / self.vwap_volume

    @property
    def daily_range(self) -> float | None:
        if self.daily_high is None or self.daily_low is None:
            return None
        return self.daily_high - self.daily_low

    def apply_daily_candle(self, candle: dict) -> None:
        self.daily_high = self._optional_float(candle.get("high"), self.daily_high)
        self.daily_low = self._optional_float(candle.get("low"), self.daily_low)

    def add_trade(self, price: float, size: float) -> None:
        if price <= 0 or size <= 0:
            return
        self.vwap_notional += price * size
        self.vwap_volume += size

    @staticmethod
    def _optional_float(value: object, fallback: float | None) -> float | None:
        if value is None:
            return fallback
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback


class OrderBook:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.has_level2 = False
        self.status = "Waiting for level 2 book..."

    def apply(self, message: dict) -> bool:
        msg_type = message.get("type")
        if msg_type == "snapshot":
            self.bids = self._parse_levels(message.get("bids", []))
            self.asks = self._parse_levels(message.get("asks", []))
            self.has_level2 = True
            self.status = "Live Level 2 depth"
            return True

        if msg_type == "l2update":
            for side, price_raw, size_raw in message.get("changes", []):
                price = float(price_raw)
                size = float(size_raw)
                levels = self.bids if side == "buy" else self.asks
                if size == 0:
                    levels.pop(price, None)
                else:
                    levels[price] = size
            self.has_level2 = True
            self.status = "Live Level 2 depth"
            return True

        return False

    def apply_ticker(self, message: dict) -> bool:
        if self.has_level2:
            return False
        try:
            bid = float(message["best_bid"])
            ask = float(message["best_ask"])
            bid_size = float(message.get("best_bid_size", 0))
            ask_size = float(message.get("best_ask_size", 0))
        except (KeyError, TypeError, ValueError):
            return False
        self.bids = {bid: bid_size}
        self.asks = {ask: ask_size}
        self.status = "Top of book only"
        return True

    def levels(self, depth: int = 18) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        bids = sorted(self.bids.items(), key=lambda item: item[0], reverse=True)[:depth]
        asks = sorted(self.asks.items(), key=lambda item: item[0])[:depth]
        return bids, asks

    def summary(self, depth: int = 10) -> BookSummary:
        bids, asks = self.levels(depth)
        best_bid = bids[0][0] if bids else None
        best_ask = asks[0][0] if asks else None
        bid_depth = sum(size for _, size in bids)
        ask_depth = sum(size for _, size in asks)
        total_depth = bid_depth + ask_depth
        return BookSummary(
            best_bid=best_bid,
            best_ask=best_ask,
            spread=(best_ask - best_bid) if best_bid is not None and best_ask is not None else None,
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            imbalance=((bid_depth - ask_depth) / total_depth * 100) if total_depth else 0,
        )

    @staticmethod
    def _parse_levels(levels: Iterable[Iterable[str]]) -> dict[float, float]:
        parsed: dict[float, float] = {}
        for price_raw, size_raw in levels:
            price = float(price_raw)
            size = float(size_raw)
            if size > 0:
                parsed[price] = size
        return parsed


@dataclass
class ScreenerRow:
    symbol: str
    price: float = 0
    volume_24h: float = 0
    rvol: float = 0
    hourly_rvol: float = 0
    daily_rvol: float = 0
    hour_change: float = 0
    previous_price: float | None = None

    @property
    def price_direction(self) -> str:
        if self.previous_price is None or self.price == self.previous_price:
            return "flat"
        return "up" if self.price > self.previous_price else "down"


class ScreenerStore:
    def __init__(self):
        self.rows: dict[str, ScreenerRow] = {}

    def update_price(self, symbol: str, price: float, volume_24h: float | None = None) -> None:
        row = self.rows.setdefault(symbol, ScreenerRow(symbol=symbol))
        if row.price:
            row.previous_price = row.price
        row.price = price
        if volume_24h is not None:
            row.volume_24h = volume_24h

    def update_rvol(self, metrics: Iterable[dict]) -> None:
        for item in metrics:
            symbol = item["Symbol"]
            row = self.rows.setdefault(symbol, ScreenerRow(symbol=symbol))
            if "Volume24h" in item:
                row.volume_24h = float(item.get("Volume24h", 0))
            row.rvol = float(item.get("RVol", 0))
            row.hourly_rvol = float(item.get("HourlyRVol", 0))
            row.daily_rvol = float(item.get("DailyRVol", 0))
            row.hour_change = float(item.get("HourChange", 0))

    def top(self, limit: int = 20, sort_by: str = "volume_24h", min_rvol: float = 1) -> list[ScreenerRow]:
        if sort_by == "rvol":
            rows = [row for row in self.rows.values() if row.price > 0 and row.rvol >= min_rvol]
            rows.sort(key=lambda row: (row.rvol, row.volume_24h, row.symbol), reverse=True)
            return rows[:limit]

        rows = [row for row in self.rows.values() if row.price > 0 and row.volume_24h > 0]
        rows.sort(key=lambda row: (row.volume_24h, row.rvol, row.symbol), reverse=True)
        return rows[:limit]

    def live_prices(self, limit: int = 20, sort_by: str = "volume_24h") -> list[ScreenerRow]:
        rows = [row for row in self.rows.values() if row.price > 0]
        if sort_by == "rvol":
            rows.sort(key=lambda row: (row.rvol, row.volume_24h, row.symbol), reverse=True)
        else:
            rows.sort(key=lambda row: (row.volume_24h, row.rvol, row.symbol), reverse=True)
        return rows[:limit]
