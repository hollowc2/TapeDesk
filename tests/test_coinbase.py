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

    assert rows == [
        {
            "Symbol": "BTC-USD",
            "RVol": 4.3,
            "Volume24h": 24000.0,
            "HourlyRVol": 12.0,
            "DailyRVol": 1.0,
            "HourChange": 1.0,
        }
    ]
