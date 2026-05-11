from src import coinbase


def test_fetch_daily_candle_range_uses_latest_daily_candle(monkeypatch):
    def fake_get_json(url):
        assert "granularity=86400" in url
        return [
            [100, "95", "110", "100", "108", "42"],
            [200, "90", "105", "98", "102", "24"],
        ]

    monkeypatch.setattr(coinbase, "get_json", fake_get_json)

    assert coinbase.fetch_daily_candle_range("BTC-USD") == {"low": 90.0, "high": 105.0, "range": 15.0}


def test_fetch_rvol_data_uses_historical_candles_when_stats_are_missing(monkeypatch):
    def fake_fetch_usd_products():
        return ["BTC-USD"]

    def fake_get_json(url):
        if "/candles?granularity=300" in url:
            return [[i, "0", "0", "0", "0", "10"] for i in range(24)]
        if url.endswith("/stats"):
            return {"volume": "0", "volume_30day": "0", "last": "100"}
        raise AssertionError(url)

    monkeypatch.setattr(coinbase, "fetch_usd_products", fake_fetch_usd_products)
    monkeypatch.setattr(coinbase, "get_json", fake_get_json)

    rows = coinbase.fetch_rvol_data()

    assert len(rows) == 1
    assert rows[0]["Symbol"] == "BTC-USD"
    assert rows[0]["RVol"] == 4.3
    assert rows[0]["Volume24h"] == 24000.0
    assert rows[0]["HourlyRVol"] == 12.0
    assert rows[0]["DailyRVol"] == 1.0
    assert rows[0]["HourChange"] == 1.0
    assert rows[0]["AvgDailyVolume"] == 240.0
    assert rows[0]["CurrentHourVolume"] == 120.0


def test_fetch_rvol_data_limits_products_and_skips_bad_rows(monkeypatch):
    monkeypatch.setattr(coinbase, "RVOL_PRODUCT_LIMIT", 2)
    monkeypatch.setattr(coinbase, "RVOL_WORKERS", 1)
    monkeypatch.setattr(coinbase, "fetch_usd_products", lambda: ["BTC-USD", "ETH-USD", "SOL-USD"])

    def fake_fetch_rvol_row(symbol):
        if symbol == "ETH-USD":
            return None
        return {"Symbol": symbol}

    monkeypatch.setattr(coinbase, "fetch_rvol_row", fake_fetch_rvol_row)

    assert coinbase.fetch_rvol_data() == [{"Symbol": "BTC-USD"}]


def test_env_int_uses_default_for_invalid_values(monkeypatch):
    monkeypatch.setenv("TAPEWORM_TEST_INT", "bad")
    assert coinbase.env_int("TAPEWORM_TEST_INT", 8) == 8

    monkeypatch.setenv("TAPEWORM_TEST_INT", "0")
    assert coinbase.env_int("TAPEWORM_TEST_INT", 8) == 1
