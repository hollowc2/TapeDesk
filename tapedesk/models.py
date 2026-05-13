from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import time
from typing import Iterable, Sequence

logger = logging.getLogger(__name__)


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


def format_percent(value: float) -> str:
    return f"{value:+.2f}%"


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
    return f"{quantity:.8f}".rstrip("0").rstrip(".")


def format_book_quantity(quantity: float) -> str:
    quantity_abs = abs(quantity)
    if quantity_abs == 0:
        return "0"
    if quantity_abs >= 1_000:
        return format_quantity(quantity)
    if quantity_abs >= 100:
        return f"{quantity:,.0f}"
    if quantity_abs >= 10:
        return f"{quantity:,.1f}".rstrip("0").rstrip(".")
    if quantity_abs >= 1:
        return f"{quantity:,.2f}".rstrip("0").rstrip(".")
    if quantity_abs >= 0.1:
        return f"{quantity:.4f}".rstrip("0").rstrip(".")
    if quantity_abs >= 0.01:
        return f"{quantity:.5f}".rstrip("0").rstrip(".")
    if quantity_abs >= 0.0001:
        return f"{quantity:.6f}".rstrip("0").rstrip(".")
    return "<0.0001" if quantity > 0 else ">-0.0001"


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
        if self.min_size is not None and trade.size < self.min_size:
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
            applied = False
            for change in message.get("changes", []):
                parsed = self._parse_change(change)
                if parsed is None:
                    continue
                side, price, size = parsed
                levels = self.bids if side == "buy" else self.asks
                if size == 0:
                    levels.pop(price, None)
                else:
                    levels[price] = size
                applied = True
            if applied:
                self.has_level2 = True
                self.status = "Live Level 2 depth"
            return applied

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
        return self.summary_from_levels(bids, asks)

    @staticmethod
    def summary_from_levels(
        bids: Sequence[tuple[float, float]],
        asks: Sequence[tuple[float, float]],
    ) -> BookSummary:
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
        for level in levels:
            try:
                price_raw, size_raw = level
                price = float(price_raw)
                size = float(size_raw)
            except (TypeError, ValueError):
                logger.debug("Skipping bad level 2 level: %r", level)
                continue
            if size > 0:
                parsed[price] = size
        return parsed

    @staticmethod
    def _parse_change(change: object) -> tuple[str, float, float] | None:
        try:
            side, price_raw, size_raw = change
            price = float(price_raw)
            size = float(size_raw)
        except (TypeError, ValueError):
            logger.debug("Skipping bad level 2 change: %r", change)
            return None
        if side not in {"buy", "sell"}:
            logger.debug("Skipping level 2 change with unknown side: %r", change)
            return None
        if price <= 0 or size < 0:
            logger.debug("Skipping level 2 change with invalid price/size: %r", change)
            return None
        return str(side), price, size


@dataclass
class ScreenerRow:
    symbol: str
    price: float = 0
    volume_24h: float = 0
    rvol: float = 0
    hourly_rvol: float = 0
    daily_rvol: float = 0
    hour_change: float = 0
    rvol_snapshot_at: float = 0
    rvol_avg_daily_volume: float = 0
    rvol_bootstrap_hour_volume: float = 0
    rvol_bootstrap_previous_hour_volume: float = 0
    rvol_bootstrap_day_volume: float = 0
    rvol_status: str = "pending"
    rvol_reason: str = ""
    rvol_status_at: float = 0
    previous_price: float | None = None
    last_price_changed_at: float = 0
    last_updated_at: float = 0
    session_high: float = 0
    session_low: float = 0
    high_low_flash_at: float = 0
    high_low_flash_direction: str = ""
    best_bid: float | None = None
    best_ask: float | None = None
    price_history: deque[tuple[float, float]] = field(default_factory=deque)
    rvol_volume_samples: deque[tuple[float, float]] = field(default_factory=deque)
    rvol_volume_deltas: deque[tuple[float, float]] = field(default_factory=deque)

    @property
    def price_direction(self) -> str:
        if self.previous_price is None or self.price == self.previous_price:
            return "flat"
        return "up" if self.price > self.previous_price else "down"

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return max(0, self.best_ask - self.best_bid)

    @property
    def spread_pct(self) -> float:
        if self.spread is None or not self.best_bid:
            return 0
        return self.spread / self.best_bid * 100

    def age(self, now: float | None = None) -> float:
        if not self.last_updated_at:
            return 0
        now = time.monotonic() if now is None else now
        return max(0, now - self.last_updated_at)

    def percent_change(self, seconds: int) -> float:
        if self.price <= 0 or len(self.price_history) < 2:
            return 0
        target = self.last_updated_at - seconds
        baseline = self.price_history[0][1]
        for changed_at, price in self.price_history:
            if changed_at <= target:
                baseline = price
            else:
                break
        if baseline <= 0:
            return 0
        return (self.price - baseline) / baseline * 100

    def update_price(
        self,
        price: float,
        volume_24h: float | None = None,
        now: float | None = None,
        rvol_volume_24h: float | None = None,
    ) -> None:
        now = time.monotonic() if now is None else now
        self.last_updated_at = now

        if self.price and price != self.price:
            self.previous_price = self.price
            self.last_price_changed_at = now
            self.price_history.append((now, price))
            if self.session_high and price > self.session_high:
                self.high_low_flash_at = now
                self.high_low_flash_direction = "high"
            elif self.session_low and price < self.session_low:
                self.high_low_flash_at = now
                self.high_low_flash_direction = "low"
        elif self.price:
            self.previous_price = self.price
        else:
            self.price_history.append((now, price))

        self.price = price
        self.session_high = max(self.session_high or price, price)
        self.session_low = min(self.session_low or price, price)

        if volume_24h is not None:
            self._update_volume(volume_24h, now, rvol_volume_24h)

        self._prune(now)

    def update_quote(self, best_bid: float | None = None, best_ask: float | None = None) -> None:
        if best_bid is not None and best_bid > 0:
            self.best_bid = best_bid
        if best_ask is not None and best_ask > 0:
            self.best_ask = best_ask

    def _update_volume(self, volume_24h: float, now: float, rvol_volume_24h: float | None = None) -> None:
        previous_rvol = self.rvol_volume_samples[-1] if self.rvol_volume_samples else None
        self.volume_24h = volume_24h
        rvol_volume_24h = volume_24h if rvol_volume_24h is None else rvol_volume_24h
        self.rvol_volume_samples.append((now, rvol_volume_24h))
        if previous_rvol is not None:
            previous_rvol_at, previous_rvol_volume = previous_rvol
            elapsed = now - previous_rvol_at
            rvol_volume_delta = rvol_volume_24h - previous_rvol_volume
            if elapsed > 0 and rvol_volume_delta > 0:
                self.rvol_volume_deltas.append((now, rvol_volume_delta))

    def current_hour_volume(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        if not self.rvol_snapshot_at or not self.rvol_avg_daily_volume:
            return 0
        if now - self.rvol_snapshot_at < 3600:
            return self.rvol_bootstrap_hour_volume + self._live_volume_since(self.rvol_snapshot_at, now)
        return self._live_volume_window(now - 3600, now)

    def previous_hour_volume(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        if not self.rvol_snapshot_at or not self.rvol_avg_daily_volume:
            return 0
        if now - self.rvol_snapshot_at < 7200:
            return self.rvol_bootstrap_previous_hour_volume
        return self._live_volume_window(now - 7200, now - 3600)

    def current_day_volume(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        if not self.rvol_snapshot_at or not self.rvol_avg_daily_volume:
            return 0
        if now - self.rvol_snapshot_at < 86400:
            return self.rvol_bootstrap_day_volume + self._live_volume_since(self.rvol_snapshot_at, now)
        return self._live_volume_window(now - 86400, now)

    def live_rvol_metrics(self, now: float | None = None) -> tuple[float, float, float]:
        now = time.monotonic() if now is None else now
        if self.rvol_status != "ok" or not self.rvol_snapshot_at or not self.rvol_avg_daily_volume:
            return self.rvol, self.hourly_rvol, self.daily_rvol

        avg_daily_volume = self.rvol_avg_daily_volume
        daily_volume = self.current_day_volume(now)
        hourly_volume = self.current_hour_volume(now)
        previous_hour_volume = self.previous_hour_volume(now)
        daily_rvol = daily_volume / avg_daily_volume if avg_daily_volume > 0 else 0
        hourly_rvol = hourly_volume / (avg_daily_volume / 24) if avg_daily_volume > 0 else 0
        hour_change = hourly_volume / previous_hour_volume if previous_hour_volume > 0 else 1
        weighted_rvol = daily_rvol * 0.5 + hourly_rvol * 0.3 + hour_change * 0.2
        return weighted_rvol, hourly_rvol, daily_rvol

    def _live_volume_since(self, start: float, now: float) -> float:
        return self._live_volume_window(start, now)

    def _live_volume_window(self, start: float, end: float) -> float:
        total = 0.0
        for sample_at, delta in self.rvol_volume_deltas:
            if sample_at < start:
                continue
            if sample_at > end:
                continue
            total += delta
        return total

    def _prune(self, now: float) -> None:
        price_cutoff = now - 3600
        while len(self.price_history) > 1 and self.price_history[1][0] < price_cutoff:
            self.price_history.popleft()
        volume_cutoff = now - 60
        while len(self.rvol_volume_samples) > 2 and self.rvol_volume_samples[0][0] < volume_cutoff:
            self.rvol_volume_samples.popleft()
        rvol_cutoff = now - 90000
        while self.rvol_volume_deltas and self.rvol_volume_deltas[0][0] < rvol_cutoff:
            self.rvol_volume_deltas.popleft()


class ScreenerStore:
    def __init__(self):
        self.rows: dict[str, ScreenerRow] = {}

    def update_price(
        self,
        symbol: str,
        price: float,
        volume_24h: float | None = None,
        best_bid: float | None = None,
        best_ask: float | None = None,
        now: float | None = None,
        rvol_volume_24h: float | None = None,
    ) -> None:
        row = self.rows.setdefault(symbol, ScreenerRow(symbol=symbol))
        row.update_price(price, volume_24h, now, rvol_volume_24h)
        row.update_quote(best_bid, best_ask)

    def update_rvol(self, metrics: Iterable[dict]) -> None:
        now = time.monotonic()
        for item in metrics:
            symbol = item["Symbol"]
            row = self.rows.setdefault(symbol, ScreenerRow(symbol=symbol))
            status = str(item.get("RVolStatus") or ("ok" if "RVol" in item else "unavailable"))
            row.rvol_status = status
            row.rvol_reason = str(item.get("RVolReason") or "")
            row.rvol_status_at = now
            if status != "ok":
                continue
            if "Volume24h" in item:
                row.volume_24h = float(item.get("Volume24h", 0))
            row.rvol = float(item.get("RVol", 0))
            row.hourly_rvol = float(item.get("HourlyRVol", 0))
            row.daily_rvol = float(item.get("DailyRVol", 0))
            row.hour_change = float(item.get("HourChange", 0))
            row.rvol_snapshot_at = now
            row.rvol_avg_daily_volume = float(item.get("AvgDailyVolume", 0))
            row.rvol_bootstrap_hour_volume = float(item.get("CurrentHourVolume", 0))
            row.rvol_bootstrap_previous_hour_volume = float(item.get("PreviousHourVolume", 0))
            row.rvol_bootstrap_day_volume = float(item.get("CurrentDayVolume", 0))

    def top(
        self,
        limit: int = 20,
        sort_by: str = "volume_24h",
        min_rvol: float = 1,
        pinned_symbols: Iterable[str] = (),
    ) -> list[ScreenerRow]:
        pinned = set(pinned_symbols)
        all_rows = [row for row in self.rows.values() if row.price > 0]
        rows = all_rows
        if sort_by == "rvol":
            rows = [row for row in rows if row.rvol >= min_rvol]
        elif sort_by == "volume_24h":
            rows = [row for row in rows if row.volume_24h > 0]
        rows.extend(row for row in all_rows if row.symbol in pinned and row not in rows)
        return self._ranked(rows, limit, sort_by, pinned_symbols)

    def live_prices(
        self,
        limit: int = 20,
        sort_by: str = "volume_24h",
        pinned_symbols: Iterable[str] = (),
    ) -> list[ScreenerRow]:
        rows = [row for row in self.rows.values() if row.price > 0]
        return self._ranked(rows, limit, sort_by, pinned_symbols)

    def _ranked(
        self,
        rows: list[ScreenerRow],
        limit: int,
        sort_by: str,
        pinned_symbols: Iterable[str],
    ) -> list[ScreenerRow]:
        pinned = set(pinned_symbols)
        rows.sort(key=lambda row: self._sort_key(row, sort_by), reverse=True)
        pinned_rows = sorted([row for row in rows if row.symbol in pinned], key=lambda row: row.symbol)
        selected = pinned_rows[:]
        for row in rows:
            if row.symbol not in pinned and len(selected) < limit:
                selected.append(row)
        return selected[:limit]

    @staticmethod
    def _sort_key(row: ScreenerRow, sort_by: str) -> tuple[float, float, str]:
        if sort_by == "rvol":
            return (row.rvol if row.rvol_status == "ok" else 0, row.volume_24h, row.symbol)
        if sort_by == "hourly_rvol":
            return (
                row.hourly_rvol if row.rvol_status == "ok" else 0,
                row.rvol if row.rvol_status == "ok" else 0,
                row.symbol,
            )
        if sort_by == "change_1m":
            return (abs(row.percent_change(60)), row.volume_24h, row.symbol)
        if sort_by == "change_5m":
            return (abs(row.percent_change(300)), row.volume_24h, row.symbol)
        if sort_by == "change_15m":
            return (abs(row.percent_change(900)), row.volume_24h, row.symbol)
        if sort_by == "change_1h":
            return (abs(row.percent_change(3600)), row.volume_24h, row.symbol)
        if sort_by == "spread":
            return (row.spread_pct, row.volume_24h, row.symbol)
        return (row.volume_24h, row.rvol if row.rvol_status == "ok" else 0, row.symbol)
