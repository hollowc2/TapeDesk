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

The orchestrator asks for assets and tools, starts a local market-data hub in a separate tmux window, and opens the tools layout in the visible window.
Press `q` in any tool window to shut down the entire tmux workspace, including the hub and every open tool.

Run individual tools:

```bash
venv/bin/python -m src tool screener
venv/bin/python -m src tool l2 --asset BTC
venv/bin/python -m src tool ts --asset ETH
venv/bin/python -m src tool ts --asset XRP --min-notional 25 --min-qty 100
```

Run the standalone Time & Sales tape:

```bash
venv/bin/python -m src tool ts --asset BTC
```

The tape shows DAS-style `Price`, `Qty`, and `Time` columns. New prints appear at the top. Buy prints are green and sell prints are red.

Filter the tape by USD notional value, base asset quantity, or both:

```bash
venv/bin/python -m src tool ts --asset BTC --min-notional 10000
venv/bin/python -m src tool ts --asset XRP --min-qty 1000
venv/bin/python -m src tool ts --asset XRP --min-notional 25 --min-qty 100
```

Use an existing hub instead of opening a direct Coinbase feed:

```bash
venv/bin/python -m src hub
venv/bin/python -m src tool ts --asset BTC --source hub
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
Time-and-sales filters are optional. `--min-notional` filters by USD trade value, which stays comparable across high-price and low-price assets, and `--min-qty` filters by the selected asset's base quantity.

## Development Checks

Compile-check the package:

```bash
venv/bin/python -m compileall src tests
```

Run tests once `pytest` is installed:

```bash
venv/bin/python -m pytest -q
```
