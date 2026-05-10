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
