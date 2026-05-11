from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
from datetime import datetime
from itertools import accumulate

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label, Static
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
from .tmux import current_tmux_session_name, kill_tmux_session
from .models import (
    MarketStats,
    OrderBook,
    ScreenerStore,
    TpsTracker,
    Trade,
    TradeTracker,
    format_price,
    format_price_value,
    format_percent,
    format_quantity,
    format_volume,
)

logger = logging.getLogger(__name__)

DEFAULT_HUB_URL = "ws://127.0.0.1:8765"
SCREENER_FLASH_SECONDS = 0.75
SCREENER_HIGH_LOW_FLASH_SECONDS = 1.5
SCREENER_RVOL_ALERT = 3
SCREENER_MOVE_ALERT_PCT = 2
SCREENER_VOLUME_SPIKE_ALERT = 3
SCREENER_SORT_LABELS = {
    "volume_24h": "24h volume",
    "rvol": "RVol",
    "hourly_rvol": "hourly RVol",
    "change_1m": "1m % change",
    "change_5m": "5m % change",
    "change_15m": "15m % change",
    "change_1h": "1h % change",
    "tick_count": "tick count",
    "notional_velocity": "notional/sec",
    "spread": "spread %",
}


class ScreenerScreen(Screen):
    BINDINGS = [
        Binding("enter", "open_selected", "Open Book"),
        Binding("o", "open_btc", "BTC Book"),
        Binding("r", "refresh_rvol", "Refresh RVol"),
        Binding("v", "toggle_sort", "Vol/RVol"),
        Binding("w", "toggle_watch", "Pin"),
        Binding("1", "sort_volume", "Vol"),
        Binding("2", "sort_rvol", "RVol"),
        Binding("3", "sort_hourly_rvol", "Hourly"),
        Binding("4", "sort_change_1m", "1m%"),
        Binding("5", "sort_change_5m", "5m%"),
        Binding("6", "sort_change_15m", "15m%"),
        Binding("7", "sort_change_1h", "1h%"),
        Binding("8", "sort_tick_count", "Ticks"),
        Binding("9", "sort_notional_velocity", "$/s"),
        Binding("0", "sort_spread", "Spread"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label("Tapeworm Screener", id="screen-title")
        yield Static("Connecting to Coinbase feeds...", id="screener-status")
        table = DataTable(id="screener-table", cursor_type="row", zebra_stripes=True)
        table.add_column("Pin", key="pin", width=3)
        table.add_column("Symbol", key="symbol", width=10)
        table.add_column("Last", key="price", width=14)
        table.add_column("1m", key="change_1m", width=8)
        table.add_column("5m", key="change_5m", width=8)
        table.add_column("15m", key="change_15m", width=8)
        table.add_column("1h", key="change_1h", width=8)
        table.add_column("Sprd", key="spread", width=7)
        table.add_column("$/s", key="velocity", width=8)
        table.add_column("24h Vol", key="volume_24h", width=9)
        table.add_column("RVol", key="rvol", width=6)
        table.add_column("Hr", key="hourly", width=6)
        table.add_column("Ticks", key="ticks", width=5)
        table.add_column("Alert", key="alert", width=12)
        table.add_column("Age", key="age", width=5)
        table.add_column("Tick", key="direction", width=5)
        yield table
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(0.25, self.refresh_table)
        self.refresh_table()

    @property
    def table(self) -> DataTable:
        return self.query_one("#screener-table", DataTable)

    def refresh_table(self) -> None:
        app = self.app
        sort_by = app.screener_sort
        rows = app.screener.top(limit=20, sort_by=sort_by, pinned_symbols=app.screener_pins)
        source = SCREENER_SORT_LABELS.get(sort_by, sort_by)
        if not rows:
            rows = app.screener.live_prices(limit=20, sort_by=sort_by, pinned_symbols=app.screener_pins)
            source = f"live prices, waiting for {source}"
        table = self.table
        selected = None
        if table.cursor_row is not None and table.row_count:
            try:
                selected = table.get_row_at(table.cursor_row)[1]
            except Exception:
                selected = None

        table.clear()
        now = time.monotonic()
        for row in rows:
            direction = {"up": "UP", "down": "DOWN", "flat": "-"}[row.price_direction]
            is_flash = now - row.last_price_changed_at < SCREENER_FLASH_SECONDS
            style = self._tick_style(row.price_direction, is_flash)
            high_low_style = self._high_low_style(row.high_low_flash_direction, now - row.high_low_flash_at)
            alert = self._alert_text(row, now)
            alert_style = high_low_style or self._alert_style(alert)
            price_cell = Text(format_price(row.price), style=style) if style else format_price(row.price)
            direction_cell = Text(direction, style=style) if style else direction
            table.add_row(
                "*" if row.symbol in app.screener_pins else "",
                row.symbol,
                price_cell,
                self._percent_cell(row.percent_change(60)),
                self._percent_cell(row.percent_change(300)),
                self._percent_cell(row.percent_change(900)),
                self._percent_cell(row.percent_change(3600)),
                self._spread_text(row),
                self._velocity_text(row.notional_velocity),
                format_volume(row.volume_24h),
                f"{row.rvol:.2f}",
                f"{row.hourly_rvol:.2f}",
                str(row.tick_count),
                Text(alert, style=alert_style) if alert_style else alert,
                self._age_text(row.age(now)),
                direction_cell,
                key=row.symbol,
            )

        self.query_one("#screener-status", Static).update(
            f"Sort: {source} | pins: {','.join(sorted(app.screener_pins))} | prices: {len(app.latest_prices)} | "
            f"RVol rows: {app.rvol_count} | 1-9/0 sort | w pin | Enter open | r refresh | q shutdown"
        )

        if selected:
            for index, row in enumerate(rows):
                if row.symbol == selected:
                    table.move_cursor(row=index)
                    break

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.app.open_market(str(event.row_key.value))

    def action_open_selected(self) -> None:
        table = self.table
        if table.cursor_row is None or not table.row_count:
            return
        symbol = str(table.get_row_at(table.cursor_row)[1])
        self.app.open_market(symbol)

    def action_refresh_rvol(self) -> None:
        self.app.refresh_rvol_now()

    def action_open_btc(self) -> None:
        self.app.open_market("BTC-USD")

    def action_toggle_sort(self) -> None:
        self.app.toggle_screener_sort()
        self.refresh_table()

    def action_toggle_watch(self) -> None:
        table = self.table
        if table.cursor_row is None or not table.row_count:
            return
        self.app.toggle_screener_pin(str(table.get_row_at(table.cursor_row)[1]))
        self.refresh_table()

    def action_sort_volume(self) -> None:
        self._set_sort("volume_24h")

    def action_sort_rvol(self) -> None:
        self._set_sort("rvol")

    def action_sort_hourly_rvol(self) -> None:
        self._set_sort("hourly_rvol")

    def action_sort_change_1m(self) -> None:
        self._set_sort("change_1m")

    def action_sort_change_5m(self) -> None:
        self._set_sort("change_5m")

    def action_sort_change_15m(self) -> None:
        self._set_sort("change_15m")

    def action_sort_change_1h(self) -> None:
        self._set_sort("change_1h")

    def action_sort_tick_count(self) -> None:
        self._set_sort("tick_count")

    def action_sort_notional_velocity(self) -> None:
        self._set_sort("notional_velocity")

    def action_sort_spread(self) -> None:
        self._set_sort("spread")

    def _set_sort(self, sort_by: str) -> None:
        self.app.set_screener_sort(sort_by)
        self.refresh_table()

    def _tick_style(self, direction: str, is_flash: bool) -> str:
        if not is_flash:
            return ""
        if direction == "up":
            return "bold white on dark_green"
        if direction == "down":
            return "bold white on dark_red"
        return ""

    def _high_low_style(self, direction: str, elapsed: float) -> str:
        if elapsed >= SCREENER_HIGH_LOW_FLASH_SECONDS:
            return ""
        if direction == "high":
            return "bold white on dark_green"
        if direction == "low":
            return "bold white on dark_red"
        return ""

    def _percent_cell(self, value: float) -> Text:
        if value > 0:
            return Text(format_percent(value), style="green")
        if value < 0:
            return Text(format_percent(value), style="red")
        return Text(format_percent(value), style="dim")

    def _spread_text(self, row) -> str:
        if row.spread is None:
            return "--"
        return f"{row.spread_pct:.3f}%"

    def _velocity_text(self, value: float) -> str:
        if value <= 0:
            return "--"
        return format_volume(value).replace("$", "")

    def _age_text(self, seconds: float) -> str:
        if seconds < 1:
            return "<1s"
        if seconds < 60:
            return f"{seconds:.0f}s"
        return f"{seconds / 60:.0f}m"

    def _alert_text(self, row, now: float) -> str:
        if now - row.high_low_flash_at < SCREENER_HIGH_LOW_FLASH_SECONDS:
            return "NEW HIGH" if row.high_low_flash_direction == "high" else "NEW LOW"
        alerts = []
        if row.rvol >= SCREENER_RVOL_ALERT:
            alerts.append("RVOL")
        if abs(row.percent_change(300)) >= SCREENER_MOVE_ALERT_PCT:
            alerts.append("MOVE")
        if row.hourly_rvol >= SCREENER_VOLUME_SPIKE_ALERT:
            alerts.append("VOL")
        return ",".join(alerts[:2])

    def _alert_style(self, alert: str) -> str:
        if not alert:
            return ""
        if "MOVE" in alert:
            return "bold yellow"
        return "bold cyan"


class MarketScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.go_back", "Back"),
        Binding("b", "app.go_back", "Back"),
        Binding("c", "toggle_compact", "Compact"),
    ]

    symbol = reactive("BTC-USD")

    def __init__(self, symbol: str):
        super().__init__()
        self.symbol = symbol
        self.compact = False
        self._book_snapshot: dict[tuple[str, float], float] = {}
        self._book_flashes: dict[tuple[str, float], float] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label(f"{self.symbol} Market Depth", id="screen-title")
        with Horizontal(id="market-layout"):
            with Vertical(id="activity-column"):
                yield Static(id="price-panel")
                yield Static(id="tps-panel")
                yield Static(id="trades-panel")
            with Vertical(id="book-column"):
                yield Static(id="book-panel")
        yield Footer()

    def on_mount(self) -> None:
        self.app.track_market_symbol(self.symbol)
        self.set_interval(0.25, self.refresh_market)
        self.apply_compact_layout()
        self.refresh_market()

    def refresh_market(self) -> None:
        app = self.app
        price = app.latest_prices.get(self.symbol, 0)
        book = app.books[self.symbol]
        tps = app.tps[self.symbol]
        trades = app.trades[self.symbol]
        stats = app.market_stats[self.symbol]

        if not self.compact:
            self.query_one("#price-panel", Static).update(self.render_price(price, book))
            self.query_one("#tps-panel", Static).update(self.render_tps(tps))
            self.query_one("#trades-panel", Static).update(self.render_trades(trades))
        self.query_one("#book-panel", Static).update(self.render_book(book, stats))

    def action_toggle_compact(self) -> None:
        self.compact = not self.compact
        self.apply_compact_layout()
        self.refresh_market()

    def apply_compact_layout(self) -> None:
        self.query_one("#activity-column", Vertical).display = not self.compact
        self.query_one("#book-column", Vertical).styles.width = "100%" if self.compact else "57%"

    def render_price(self, price: float, book: OrderBook) -> str:
        summary = book.summary()
        price_line = format_price(price) if price else "Loading..."
        spread = format_price_value(summary.spread) if summary.spread is not None else "--"
        return f"[b]{self.symbol}[/b]\nLast: [bold cyan]{price_line}[/bold cyan]\nSpread: {spread}\nStatus: {book.status}"

    def render_tps(self, tps: TpsTracker) -> str:
        low = tps.lowest if tps.lowest is not None else 0
        return (
            "[b]Transactions Per Second[/b]\n"
            f"Current: [cyan]{tps.current}[/cyan]  Avg: {tps.average:.1f}\n"
            f"High: [green]{tps.highest}[/green]  Low: [red]{low}[/red]\n"
            f"{tps.sparkline(36)}"
        )

    def render_trades(self, trades: TradeTracker) -> str:
        lines = ["[b]Large Trades[/b]", "Recent"]
        if not trades.recent:
            lines.append(f"Waiting for trades > {format_volume(trades.min_notional)}...")
        for trade in trades.recent[:8]:
            color = "green" if trade.side == "buy" else "red"
            lines.append(
                f"{trade.time_label} [{color}]{trade.side.upper():<4}[/{color}] "
                f"{format_quantity(trade.size):>8} @ {format_price(trade.price):>14} "
                f"{format_price(trade.notional):>14}"
            )

        lines.append("")
        lines.append("Top")
        for index, trade in enumerate(trades.top[:5], 1):
            color = "green" if trade.side == "buy" else "red"
            lines.append(
                f"{index:>2}. [{color}]{trade.side.upper():<4}[/{color}] "
                f"{format_quantity(trade.size):>8} @ {format_price(trade.price):>14}"
            )
        return "\n".join(lines)

    def render_book(self, book: OrderBook, stats: MarketStats) -> str:
        depth = 20
        bids, asks = book.levels(depth)
        self._update_book_flashes(bids, asks)
        bid_totals = list(accumulate(size for _, size in bids))
        ask_totals = list(accumulate(size for _, size in asks))
        max_size = max((size for _, size in bids + asks), default=0)
        summary = book.summary(depth)
        spread = format_price_value(summary.spread) if summary.spread is not None else "--"
        spread_pct = (
            f"{summary.spread / summary.best_bid * 100:.4f}%"
            if summary.spread is not None and summary.best_bid
            else "--"
        )
        vwap = format_price(stats.vwap) if stats.vwap is not None else "--"

        prices = [price for price, _ in bids + asks]
        sizes = [size for _, size in bids + asks]
        totals = bid_totals + ask_totals
        price_width = max(12, len("BID PRICE"), *(len(format_price_value(price)) for price in prices))
        size_width = max(10, len("BID SIZE"), *(len(format_quantity(size)) for size in sizes))
        total_width = max(10, len("TOTAL"), *(len(format_quantity(total)) for total in totals))
        bar_width = 8 if self.compact else 5
        show_totals = self.compact
        left_width = bar_width + 1 + price_width + 1 + size_width
        right_width = price_width + 1 + size_width + 1 + bar_width
        if show_totals:
            left_width += 1 + total_width
            right_width += 1 + total_width
        row_width = left_width + 3 + right_width
        spread_text = f"Spread {spread}/{spread_pct}"
        spread_line = f"{'VWAP ' + vwap:>{left_width}} | {spread_text:<{right_width}}"
        spread_line = spread_line.ljust(row_width)

        rows = [
            f"[b]{'BID PRICE':>{bar_width + 1 + price_width}} {'BID SIZE':>{size_width}}"
            + (f" {'TOTAL':>{total_width}}" if show_totals else "")
            + " | "
            + f"{'ASK PRICE':>{price_width}} {'ASK SIZE':>{size_width}}"
            + (f" {'TOTAL':>{total_width}}" if show_totals else "")
            + f" {'':<{bar_width}}[/b]",
            "-" * row_width,
            spread_line,
            "-" * row_width,
        ]
        for index in range(max(len(bids), len(asks), 20)):
            bid = bids[index] if index < len(bids) else None
            ask = asks[index] if index < len(asks) else None
            bid_total = bid_totals[index] if index < len(bid_totals) else None
            ask_total = ask_totals[index] if index < len(ask_totals) else None
            if bid:
                bid_text = self._format_bid_level(
                    bid,
                    bid_total,
                    index,
                    max_size,
                    price_width,
                    size_width,
                    total_width,
                    bar_width,
                    show_totals,
                )
            else:
                bid_text = " " * left_width
            if ask:
                ask_text = self._format_ask_level(
                    ask,
                    ask_total,
                    index,
                    max_size,
                    price_width,
                    size_width,
                    total_width,
                    bar_width,
                    show_totals,
                )
            else:
                ask_text = " " * right_width
            rows.append(f"{bid_text} | {ask_text}")
        return "\n".join(rows)

    def _update_book_flashes(self, bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> None:
        now = time.monotonic()
        current = {("bid", price): size for price, size in bids}
        current.update({("ask", price): size for price, size in asks})
        for key, size in current.items():
            previous_size = self._book_snapshot.get(key)
            if previous_size != size and (previous_size is not None or self._book_snapshot):
                self._book_flashes[key] = now
        self._book_snapshot = current
        self._book_flashes = {
            key: changed_at for key, changed_at in self._book_flashes.items() if now - changed_at < 0.75
        }

    def _format_bid_level(
        self,
        level: tuple[float, float],
        total: float | None,
        index: int,
        max_size: float,
        price_width: int,
        size_width: int,
        total_width: int,
        bar_width: int,
        show_totals: bool,
    ) -> str:
        price, size = level
        style = self._level_style("bid", price, index)
        is_flash = self._is_level_flash("bid", price)
        bar = self._depth_bar(size, max_size, bar_width, align="right")
        price_text = format_price_value(price)
        size_text = format_quantity(size)
        total_text = f" {format_quantity(total):>{total_width}}" if show_totals and total is not None else ""
        text = f"{bar} {price_text:>{price_width}} {size_text:>{size_width}}{total_text}"
        if is_flash:
            bar_text = f"[bold bright_green on dark_green]{bar}[/]"
            row_text = f"{bar_text} {price_text:>{price_width}} {size_text:>{size_width}}{total_text}"
            return f"[white on dark_green]{row_text}[/]"
        return f"[{style}]{text}[/]"

    def _format_ask_level(
        self,
        level: tuple[float, float],
        total: float | None,
        index: int,
        max_size: float,
        price_width: int,
        size_width: int,
        total_width: int,
        bar_width: int,
        show_totals: bool,
    ) -> str:
        price, size = level
        style = self._level_style("ask", price, index)
        is_flash = self._is_level_flash("ask", price)
        bar = self._depth_bar(size, max_size, bar_width, align="left")
        price_text = format_price_value(price)
        size_text = format_quantity(size)
        total_text = f" {format_quantity(total):>{total_width}}" if show_totals and total is not None else ""
        text = f"{price_text:>{price_width}} {size_text:>{size_width}}{total_text} {bar}"
        if is_flash:
            bar_text = f"[bold bright_red on dark_red]{bar}[/]"
            row_text = f"{price_text:>{price_width}} {size_text:>{size_width}}{total_text} {bar_text}"
            return f"[white on dark_red]{row_text}[/]"
        return f"[{style}]{text}[/]"

    def _level_style(self, side: str, price: float, index: int) -> str:
        base_color = "green" if side == "bid" else "red"
        if index == 0:
            return f"bold {base_color}"
        if index >= 14:
            return f"dim {base_color}"
        if index >= 8:
            return f"{base_color} dim"
        return base_color

    def _is_level_flash(self, side: str, price: float) -> bool:
        return (side, price) in self._book_flashes

    @staticmethod
    def _depth_bar(size: float, max_size: float, width: int, align: str) -> str:
        if max_size <= 0:
            fill = 0
        else:
            fill = max(1, round(size / max_size * width))
        bar = "█" * fill
        if align == "right":
            return bar.rjust(width)
        return bar.ljust(width)


class L2Screen(MarketScreen):
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("c", "toggle_compact", "Compact"),
    ]

    def __init__(self, symbol: str):
        super().__init__(symbol)
        self.compact = True

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label(f"{self.symbol} Level 2", id="screen-title")
        yield Static(id="book-panel")
        yield Footer()

    def refresh_market(self) -> None:
        app = self.app
        book = app.books[self.symbol]
        stats = app.market_stats[self.symbol]
        self.query_one("#book-panel", Static).update(self.render_book(book, stats))

    def action_toggle_compact(self) -> None:
        self.compact = not self.compact
        self.refresh_market()

    def apply_compact_layout(self) -> None:
        return


class TimeSalesScreen(Screen):
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("s", "ignore_screener", show=False, priority=True),
    ]

    symbol = reactive("BTC-USD")

    def __init__(self, symbol: str):
        super().__init__()
        self.symbol = symbol

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label(f"{self.symbol} Time & Sales", id="screen-title")
        yield Static(id="time-sales-status")
        table = DataTable(id="time-sales-table", zebra_stripes=True)
        table.add_column("Price", key="price", width=16)
        table.add_column("Qty", key="qty", width=14)
        table.add_column("Time", key="time", width=10)
        yield table
        yield Footer()

    def on_mount(self) -> None:
        self.app.track_market_symbol(self.symbol)
        self.set_interval(0.1, self.refresh_time_sales)
        self.refresh_time_sales()

    @property
    def table(self) -> DataTable:
        return self.query_one("#time-sales-table", DataTable)

    def refresh_time_sales(self) -> None:
        app = self.app
        tps = app.tps[self.symbol]
        trades = app.trades[self.symbol]
        table = self.table
        table.clear()
        for trade in trades.recent:
            style = "bold green" if trade.side == "buy" else "bold red"
            table.add_row(
                Text(format_price_value(trade.price), style=style),
                Text(format_quantity(trade.size), style=style),
                Text(trade.time_label, style=style),
            )

        filter_label = app.time_sales_filter_label(self.symbol)
        self.query_one("#time-sales-status", Static).update(
            f"{self.symbol} | Prints: {len(trades.recent)} | TPS: {tps.current} | Filter: {filter_label}"
        )

    def action_ignore_screener(self) -> None:
        return


class TapewormApp(App):
    CSS = """
    Screen {
        background: #111318;
        color: #e6e6e6;
    }

    #screen-title {
        dock: top;
        height: 1;
        padding: 0 1;
        background: #20242d;
        color: white;
        text-style: bold;
    }

    #screener-table, #time-sales-table {
        height: 1fr;
        margin: 1;
    }

    #screener-status, #time-sales-status {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: #20242d;
        color: #d8d8d8;
    }

    #market-layout {
        height: 1fr;
        margin: 1;
    }

    #activity-column {
        width: 43%;
        min-width: 46;
    }

    #book-column {
        width: 57%;
    }

    #price-panel, #tps-panel, #trades-panel, #book-panel {
        border: solid #3a4050;
        padding: 1;
        margin: 0 1 1 0;
        background: #161a22;
    }

    #price-panel {
        height: 7;
    }

    #tps-panel {
        height: 7;
    }

    #trades-panel {
        height: 1fr;
    }

    #book-panel {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "shutdown_workspace", "Shutdown", priority=True),
        Binding("s", "show_screener", "Screener"),
    ]

    def __init__(
        self,
        mode: str = "all",
        symbol: str = "BTC-USD",
        source: str = "auto",
        hub_url: str = DEFAULT_HUB_URL,
        time_sales_min_notional: float = 0,
        time_sales_min_size: float | None = None,
    ):
        super().__init__()
        self.mode = mode
        self.symbol = normalize_asset(symbol)
        self.source = source
        self.hub_url = hub_url
        self.time_sales_min_notional = max(0, time_sales_min_notional)
        self.time_sales_min_size = max(0, time_sales_min_size) if time_sales_min_size is not None else None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.screener = ScreenerStore()
        self.screener_sort = "volume_24h"
        self.screener_pins: set[str] = {"BTC-USD", "ETH-USD", "SOL-USD"}
        self.latest_prices: dict[str, float] = {}
        self.books: dict[str, OrderBook] = {}
        self.trades: dict[str, TradeTracker] = {}
        self.tps: dict[str, TpsTracker] = {}
        self.market_stats: dict[str, MarketStats] = {}
        self.market_symbols: set[str] = {self.symbol}
        self.market_feed_symbols: set[str] = set()
        self.daily_range_symbols: set[str] = set()
        self.rvol_count = 0
        self._started = False
        self._direct_feeds = False

    def compose(self) -> ComposeResult:
        return
        yield

    def on_mount(self) -> None:
        load_env_file()
        if self.mode == "screener":
            self.push_screen(ScreenerScreen())
        elif self.mode == "l2":
            self.push_screen(L2Screen(self.symbol))
        elif self.mode == "ts":
            self.push_screen(TimeSalesScreen(self.symbol))
        else:
            self.push_screen(ScreenerScreen())
        self.start_background_feeds()
        self.set_interval(0.1, self.drain_events)

    def start_background_feeds(self) -> None:
        if self._started:
            return
        self._started = True
        if self.source in {"hub", "auto"} and self.mode != "all":
            threading.Thread(target=self._run_hub_client_or_fallback, daemon=True).start()
            return

        self.start_direct_feeds()

    def start_direct_feeds(self) -> None:
        self._direct_feeds = True
        if self.mode in {"all", "screener"}:
            threading.Thread(target=self._run_screener_ticker_feed, daemon=True).start()
            threading.Thread(target=self._run_rvol_feed, daemon=True).start()
        if self.mode in {"all", "l2", "ts"}:
            self.track_market_symbol(self.symbol)

    def _run_hub_client_or_fallback(self) -> None:
        try:
            asyncio.run(self._run_hub_client())
        except Exception as exc:
            logger.info("Hub unavailable at %s, falling back to direct feeds: %s", self.hub_url, exc)
            if self.source == "hub":
                self.events.put(("status", f"Hub unavailable: {exc}"))
                return
            self.start_direct_feeds()

    async def _run_hub_client(self) -> None:
        topics = self._hub_topics()
        async with websockets.connect(self.hub_url, max_size=None, ping_interval=None) as websocket:
            await websocket.send(json.dumps({"type": "subscribe", "topics": topics}))
            async for raw_message in websocket:
                message = json.loads(raw_message)
                event = message.get("event")
                payload = message.get("payload")
                if event == "ticker":
                    self.events.put(("ticker", payload))
                elif event == "market":
                    self.events.put(("market", payload))
                elif event == "rvol":
                    self.events.put(("rvol", payload))
                elif event == "daily_range":
                    self.events.put(("daily_range", payload))

    def _hub_topics(self) -> list[str]:
        if self.mode == "screener":
            return ["screener:*"]
        if self.mode == "l2":
            return [f"l2:{self.symbol}"]
        if self.mode == "ts":
            return [f"ts:{self.symbol}"]
        return ["screener:*", f"l2:{self.symbol}", f"ts:{self.symbol}"]

    def _run_screener_ticker_feed(self) -> None:
        products_cache: list[str] = []

        def products() -> list[str]:
            nonlocal products_cache
            if not products_cache:
                products_cache = fetch_usd_products()
            return products_cache

        def channels(_: list[str]) -> list:
            return ["ticker"]

        asyncio.run(websocket_loop(products, channels, lambda message: self.events.put(("ticker", message))))

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

        asyncio.run(websocket_loop(products, channels, lambda message: self.events.put(("market", message))))

    def _run_rvol_feed(self) -> None:
        while True:
            self.refresh_rvol_now()
            time.sleep(300)

    def refresh_rvol_now(self) -> None:
        def worker() -> None:
            self.events.put(("rvol", fetch_rvol_data()))

        threading.Thread(target=worker, daemon=True).start()

    def drain_events(self) -> None:
        for _ in range(500):
            try:
                event_type, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event_type == "ticker":
                self.handle_ticker(payload)
            elif event_type == "market":
                self.handle_market(payload)
            elif event_type == "rvol":
                self.rvol_count = len(payload) if isinstance(payload, list) else 0
                self.screener.update_rvol(payload)
            elif event_type == "daily_range":
                symbol, candle = payload
                if isinstance(candle, dict):
                    self.market_stats.setdefault(str(symbol), MarketStats()).apply_daily_candle(candle)

    def handle_ticker(self, message: object) -> None:
        if not isinstance(message, dict) or message.get("type") != "ticker":
            return
        try:
            symbol = str(message["product_id"])
            price = float(message["price"])
        except (KeyError, TypeError, ValueError):
            return
        try:
            volume_24h = float(message["volume_24h"]) * price if message.get("volume_24h") is not None else None
        except (TypeError, ValueError):
            volume_24h = None
        best_bid = optional_float(message.get("best_bid"))
        best_ask = optional_float(message.get("best_ask"))
        self.latest_prices[symbol] = price
        self.screener.update_price(symbol, price, volume_24h, best_bid, best_ask)

    def handle_market(self, message: object) -> None:
        if not isinstance(message, dict):
            return
        msg_type = message.get("type")
        symbol = str(message.get("product_id", "BTC-USD"))
        self.ensure_market_state(symbol)

        if msg_type == "match":
            self.tps[symbol].add_transaction()
            try:
                price = float(message["price"])
                trade = Trade(
                    symbol=symbol,
                    size=float(message["size"]),
                    price=price,
                    side=self.trade_side(symbol, price, message),
                    time=parse_trade_time(message.get("time")),
                )
            except (KeyError, TypeError, ValueError):
                return
            self.trades[symbol].add(trade)
            self.market_stats[symbol].add_trade(trade.price, trade.size)
            self.latest_prices[symbol] = trade.price
            self.screener.update_price(symbol, trade.price)
        elif msg_type == "ticker":
            self.handle_ticker(message)
            self.books[symbol].apply_ticker(message)
        elif msg_type in {"snapshot", "l2update"}:
            self.books[symbol].apply(message)
        elif msg_type == "error":
            reason = str(message.get("reason") or message.get("message") or "Coinbase feed error")
            self.books[symbol].status = reason
            logger.error("Coinbase WebSocket error: %s", reason)

    def track_market_symbol(self, symbol: str) -> None:
        self.market_symbols.add(symbol)
        self.ensure_market_state(symbol)
        if self.source in {"hub", "auto"} and self.mode != "all" and not self._direct_feeds:
            return
        if symbol not in self.market_feed_symbols:
            self.market_feed_symbols.add(symbol)
            threading.Thread(target=self._run_market_feed, args=(symbol,), daemon=True).start()
        if symbol not in self.daily_range_symbols:
            self.daily_range_symbols.add(symbol)
            threading.Thread(target=self._run_daily_range_feed, args=(symbol,), daemon=True).start()

    def _run_daily_range_feed(self, symbol: str) -> None:
        while True:
            self.events.put(("daily_range", (symbol, fetch_daily_candle_range(symbol))))
            time.sleep(60)

    def ensure_market_state(self, symbol: str) -> None:
        self.books.setdefault(symbol, OrderBook(symbol))
        self.trades.setdefault(symbol, self.new_trade_tracker())
        self.tps.setdefault(symbol, TpsTracker())
        self.market_stats.setdefault(symbol, MarketStats())

    def new_trade_tracker(self) -> TradeTracker:
        if self.mode == "ts":
            return TradeTracker(
                max_recent=200,
                max_top=0,
                min_notional=self.time_sales_min_notional,
                min_size=self.time_sales_min_size,
            )
        return TradeTracker()

    def time_sales_filter_label(self, symbol: str) -> str:
        parts = []
        if self.time_sales_min_notional > 0:
            parts.append(f">= {format_price(self.time_sales_min_notional)}")
        if self.time_sales_min_size is not None:
            base = symbol.split("-", 1)[0]
            parts.append(f">= {format_quantity(self.time_sales_min_size)} {base}")
        return ", ".join(parts) if parts else "All prints"

    def trade_side(self, symbol: str, price: float, message: dict) -> str:
        side = str(message.get("side", "")).lower()
        if side in {"buy", "sell"}:
            return side

        summary = self.books[symbol].summary()
        if summary.best_ask is not None and price >= summary.best_ask:
            return "buy"
        if summary.best_bid is not None and price <= summary.best_bid:
            return "sell"

        last_price = self.latest_prices.get(symbol)
        if last_price is not None:
            return "buy" if price >= last_price else "sell"
        return "buy"

    def open_market(self, symbol: str) -> None:
        if self.mode == "screener" and self.source in {"hub", "auto"} and not self._direct_feeds:
            self._direct_feeds = True
        self.track_market_symbol(symbol)
        self.push_screen(MarketScreen(symbol))

    def action_show_screener(self) -> None:
        if isinstance(self.screen, ScreenerScreen):
            return
        if len(self._screen_stack) > 1:
            self.pop_screen()
        else:
            self.push_screen(ScreenerScreen())

    def toggle_screener_sort(self) -> None:
        self.screener_sort = "rvol" if self.screener_sort == "volume_24h" else "volume_24h"

    def set_screener_sort(self, sort_by: str) -> None:
        if sort_by in SCREENER_SORT_LABELS:
            self.screener_sort = sort_by

    def toggle_screener_pin(self, symbol: str) -> None:
        if symbol in self.screener_pins:
            self.screener_pins.remove(symbol)
        else:
            self.screener_pins.add(symbol)

    def action_go_back(self) -> None:
        if len(self._screen_stack) > 1:
            self.pop_screen()

    def action_shutdown_workspace(self) -> None:
        session_name = current_tmux_session_name()
        if session_name:
            kill_tmux_session(session_name)
            return
        self.exit()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler("tapeworm.log")],
    )
    TapewormApp().run()


def parse_trade_time(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        return datetime.now()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now()
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone().replace(tzinfo=None)


def optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
