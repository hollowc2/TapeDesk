import asyncio

from tapedesk.hub import MarketDataHub


class FakeWebSocket:
    def __init__(self, fail: bool = False):
        self.sent: list[str] = []
        self.fail = fail

    async def send(self, message: str) -> None:
        if self.fail:
            raise RuntimeError("send failed")
        self.sent.append(message)


def test_ensure_topics_starts_requested_feeds(monkeypatch):
    hub = MarketDataHub()
    screener_started = []
    markets_started = []

    monkeypatch.setattr(hub, "ensure_screener", lambda: screener_started.append(True))
    monkeypatch.setattr(hub, "ensure_market", lambda symbol: markets_started.append(symbol))

    hub.ensure_topics({"screener:*", "l2:btc", "ts:ETH-USD", "bad-topic", "l2:*"})

    assert screener_started == [True]
    assert sorted(markets_started) == ["BTC-USD", "ETH-USD"]


def test_publish_market_routes_messages_by_topic(monkeypatch):
    hub = MarketDataHub()
    published = []
    monkeypatch.setattr(hub, "publish", lambda topic, event, payload: published.append((topic, event, payload)))

    match = {"type": "match"}
    snapshot = {"type": "snapshot"}
    ticker = {"type": "ticker"}

    hub.publish_market("BTC-USD", match)
    hub.publish_market("BTC-USD", snapshot)
    hub.publish_market("BTC-USD", ticker)

    assert ("ts:BTC-USD", "market", match) in published
    assert ("l2:BTC-USD", "market", snapshot) in published
    assert ("l2:BTC-USD", "market", ticker) in published
    assert ("ts:BTC-USD", "market", ticker) in published


def test_broadcast_sends_to_matching_clients_and_drops_dead_clients():
    hub = MarketDataHub()
    l2_client = FakeWebSocket()
    screener_client = FakeWebSocket()
    dead_client = FakeWebSocket(fail=True)
    unrelated_client = FakeWebSocket()
    hub.clients = {
        l2_client: {"l2:BTC-USD"},
        screener_client: {"screener:*"},
        dead_client: {"l2:BTC-USD"},
        unrelated_client: {"ts:BTC-USD"},
    }

    asyncio.run(hub.broadcast("l2:BTC-USD", {"event": "market"}))

    assert l2_client.sent
    assert not screener_client.sent
    assert not unrelated_client.sent
    assert dead_client not in hub.clients
