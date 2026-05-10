# Tapeworm

Tapeworm is a terminal crypto market monitor built with Textual. It can run as one combined app or as separate tools for a screener, level 2 order book, and time-and-sales tape.

## Setup

Install dependencies in your virtual environment:

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt
```

Optional Coinbase credentials can live in `.env`:

```bash
COINBASE_API_KEY=...
COINBASE_API_SECRET=...
COINBASE_PASSPHRASE=...
```

## Run

Run the combined app:

```bash
venv/bin/python -m src
```

Start the interactive orchestrator:

```bash
venv/bin/python -m src orchestrator
```

The orchestrator asks for assets and tools, starts a local market-data hub, and opens a tmux layout.

Run individual tools:

```bash
venv/bin/python -m src tool screener
venv/bin/python -m src tool l2 --asset BTC
venv/bin/python -m src tool ts --asset ETH
```

Launch a tmux workspace directly:

```bash
venv/bin/python -m src tmux launch --assets BTC,ETH --tools screener,l2,ts
```

Run only the local websocket hub:

```bash
venv/bin/python -m src hub
```

Tool commands default to `--source auto`: they try the local hub first, then fall back to direct Coinbase feeds if no hub is available.

## Tools

- `screener`: live USD market screener with price, volume, and RVol data.
- `l2`: level 2 order book for one asset.
- `ts`: time-and-sales tape for one asset.

Assets can be passed as full Coinbase product IDs such as `BTC-USD` or as shorthand tickers such as `BTC`.

## Development Checks

Compile-check the package:

```bash
venv/bin/python -m compileall src tests
```

Run tests once `pytest` is installed:

```bash
venv/bin/python -m pytest -q
```
