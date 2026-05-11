from datetime import datetime, timedelta

from src.app import TapewormApp
import src.models as models
from src.models import (
    MarketStats,
    OrderBook,
    ScreenerStore,
    TpsTracker,
    Trade,
    TradeTracker,
    format_price,
    format_price_value,
    format_quantity,
    format_volume,
)


def test_format_price_uses_precision_by_magnitude():
    assert format_price(0.000012346) == "$0.00001235"
    assert format_price(0.0008) == "$0.000800"
    assert format_price(0.0042) == "$0.004200"
    assert format_price(0.42) == "$0.4200"
    assert format_price(42000) == "$42,000.00"
    assert format_price(800000) == "$800,000"
    assert format_price_value(0.0008) == "0.000800"
    assert format_price_value(800000) == "800,000"


def test_format_volume_compacts_large_usd_values():
    assert format_volume(999) == "$999"
    assert format_volume(42_000) == "$42.00K"
    assert format_volume(42_000_000) == "$42.00M"
    assert format_volume(4_200_000_000) == "$4.20B"


def test_format_quantity_compacts_asset_sizes():
    assert format_quantity(0.25) == "0.25"
    assert format_quantity(42) == "42.000"
    assert format_quantity(6_496) == "6.50K"
    assert format_quantity(2_252_775.7) == "2.25M"


def test_trade_tracker_filters_recent_and_top_large_trades():
    tracker = TradeTracker(max_recent=2, max_top=2, min_notional=10)

    assert tracker.add(Trade("BTC-USD", 0.05, 100, "buy")) is False
    assert tracker.add(Trade("BTC-USD", 0.2, 100, "buy")) is True
    assert tracker.add(Trade("BTC-USD", 0.4, 100, "sell")) is True
    assert tracker.add(Trade("BTC-USD", 0.3, 100, "buy")) is True

    assert [trade.size for trade in tracker.recent] == [0.3, 0.4]
    assert [trade.size for trade in tracker.top] == [0.4, 0.3]


def test_trade_tracker_filters_by_notional_not_asset_size():
    tracker = TradeTracker(min_notional=100)

    assert tracker.add(Trade("DOGE-USD", 100_000, 0.0008, "buy")) is False
    assert tracker.add(Trade("BTC-USD", 0.002, 80_000, "buy")) is True


def test_trade_tracker_uses_min_size_as_inclusive_threshold():
    tracker = TradeTracker(min_notional=0, min_size=100)

    assert tracker.add(Trade("XRP-USD", 99.9, 0.50, "buy")) is False
    assert tracker.add(Trade("XRP-USD", 100, 0.50, "buy")) is True


def test_tps_tracker_samples_once_per_second_and_calculates_average():
    tracker = TpsTracker(window_seconds=30)
    start = datetime(2026, 1, 1, 12, 0, 0)

    assert tracker.add_transaction(start) is True
    assert tracker.add_transaction(start + timedelta(milliseconds=500)) is False
    assert tracker.add_transaction(start + timedelta(seconds=1)) is True

    assert tracker.current == 3
    assert tracker.highest == 3
    assert tracker.lowest == 1
    assert tracker.average == 2


def test_order_book_applies_snapshot_updates_and_ticker_fallback():
    book = OrderBook("BTC-USD")

    assert book.apply_ticker({"best_bid": "100", "best_ask": "101", "best_bid_size": "2", "best_ask_size": "3"})
    assert book.summary().spread == 1

    assert book.apply({"type": "snapshot", "bids": [["99", "1.5"]], "asks": [["102", "2.5"]]})
    assert book.has_level2 is True
    assert book.summary().best_bid == 99

    assert book.apply({"type": "l2update", "changes": [["buy", "100", "4"], ["sell", "102", "0"]]})
    bids, asks = book.levels()
    assert bids[0] == (100, 4)
    assert asks == []


def test_market_stats_tracks_vwap_and_daily_range():
    stats = MarketStats()

    stats.apply_daily_candle({"high": "110", "low": "95"})
    stats.add_trade(price=100, size=2)
    stats.add_trade(price=106, size=1)
    stats.add_trade(price=0, size=5)

    assert stats.daily_range == 15
    assert stats.vwap == 102


def test_screener_store_tracks_price_direction_and_sorts_by_selected_metric():
    store = ScreenerStore()
    store.update_rvol(
        [
            {"Symbol": "BTC-USD", "Volume24h": 1000, "RVol": 2, "HourlyRVol": 3, "DailyRVol": 4, "HourChange": 5},
            {"Symbol": "ETH-USD", "Volume24h": 500, "RVol": 4, "HourlyRVol": 1, "DailyRVol": 1, "HourChange": 1},
            {"Symbol": "DOGE-USD", "Volume24h": 100, "RVol": 0.5},
        ]
    )
    store.update_price("BTC-USD", 100)
    store.update_price("BTC-USD", 101)
    store.update_price("ETH-USD", 50)
    store.update_price("DOGE-USD", 1)

    rows = store.top(limit=2)
    assert [row.symbol for row in rows] == ["BTC-USD", "ETH-USD"]
    assert rows[0].price_direction == "up"
    assert rows[0].last_price_changed_at > 0
    assert [row.symbol for row in store.live_prices(limit=3)] == ["BTC-USD", "ETH-USD", "DOGE-USD"]

    rvol_rows = store.top(limit=2, sort_by="rvol")
    assert [row.symbol for row in rvol_rows] == ["ETH-USD", "BTC-USD"]
    assert [row.symbol for row in store.live_prices(limit=3, sort_by="rvol")] == ["ETH-USD", "BTC-USD", "DOGE-USD"]


def test_screener_store_marks_rvol_snapshots_and_app_shows_placeholder_until_one_arrives():
    store = ScreenerStore()
    store.update_price("BTC-USD", 100)
    row = store.rows["BTC-USD"]
    app = TapewormApp(mode="screener")

    assert app._rvol_text(row.rvol, row) == "--"

    store.update_rvol([{"Symbol": "BTC-USD", "RVol": 2, "HourlyRVol": 3, "DailyRVol": 4, "HourChange": 5}])
    row = store.rows["BTC-USD"]

    assert row.rvol_snapshot_at > 0
    assert app._rvol_text(row.rvol, row) == "2.00"
    assert app._rvol_text(row.hourly_rvol, row) == "3.00"


def test_screener_row_rolls_rvol_forward_from_live_volume_deltas(monkeypatch):
    monkeypatch.setattr(models.time, "monotonic", lambda: 1000.0)
    store = ScreenerStore()
    store.update_rvol(
        [
            {
                "Symbol": "BTC-USD",
                "RVol": 1.2,
                "HourlyRVol": 1.0,
                "DailyRVol": 1.0,
                "HourChange": 2.0,
                "AvgDailyVolume": 2400,
                "CurrentHourVolume": 100,
                "PreviousHourVolume": 50,
                "CurrentDayVolume": 2400,
            }
        ]
    )

    store.update_price("BTC-USD", 100, volume_24h=2400, now=1000.0)
    store.update_price("BTC-USD", 101, volume_24h=2420, now=1060.0)

    row = store.rows["BTC-USD"]
    rvol, hourly_rvol, daily_rvol = row.live_rvol_metrics(1060.0)

    assert row.current_hour_volume(1060.0) == 120
    assert row.current_day_volume(1060.0) == 2420
    assert round(rvol, 3) == 1.344
    assert round(hourly_rvol, 2) == 1.20
    assert round(daily_rvol, 3) == 1.008


def test_screener_row_tracks_momentum_spread_velocity_and_high_low():
    store = ScreenerStore()
    store.update_price("BTC-USD", 100, volume_24h=1_000, best_bid=99, best_ask=101, now=0)
    store.update_price("BTC-USD", 106, volume_24h=1_300, now=60)

    row = store.rows["BTC-USD"]
    assert row.percent_change(60) == 6
    assert row.tick_count == 1
    assert round(row.spread_pct, 2) == 2.02
    assert row.notional_velocity == 5
    assert row.high_low_flash_direction == "high"

    store.update_price("BTC-USD", 94, now=70)

    assert row.percent_change(60) == -6
    assert row.tick_count == 2
    assert row.age(75) == 5
    assert row.high_low_flash_direction == "low"


def test_screener_store_keeps_pinned_symbols_visible_before_sorted_rows():
    store = ScreenerStore()
    store.update_rvol(
        [
            {"Symbol": "BTC-USD", "Volume24h": 1000, "RVol": 2},
            {"Symbol": "ETH-USD", "Volume24h": 500, "RVol": 4},
            {"Symbol": "DOGE-USD", "Volume24h": 10, "RVol": 0.5},
        ]
    )
    store.update_price("BTC-USD", 100)
    store.update_price("ETH-USD", 50)
    store.update_price("DOGE-USD", 1)

    rows = store.top(limit=2, pinned_symbols={"DOGE-USD"})

    assert [row.symbol for row in rows] == ["DOGE-USD", "BTC-USD"]
    assert [row.symbol for row in store.top(limit=2, sort_by="rvol", pinned_symbols={"DOGE-USD"})] == [
        "DOGE-USD",
        "ETH-USD",
    ]
