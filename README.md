# tapedesk

tapedesk is a terminal crypto market monitor built with Textual. It can run as one combined app or as separate tools for a screener, level 2 order book, and time-and-sales tape. It is the replacement for BTCBeeper.

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
venv/bin/python -m tapedesk
```

Start the interactive orchestrator:

```bash
venv/bin/python -m tapedesk orchestrator
```

The orchestrator asks for assets and tools, starts a local market-data hub in a separate tmux window, and opens the tools layout in the visible window. Press `q` in any tool window to shut down the entire tmux workspace, including the hub and every open tool.

Run modules in isolation with direct Coinbase feeds:

```bash
venv/bin/python -m tapedesk tool screener
venv/bin/python -m tapedesk tool l2 --asset BTC
venv/bin/python -m tapedesk tool ts --asset BTC
venv/bin/python -m tapedesk hub
```

Run the standalone Time & Sales tape:

```bash
venv/bin/python -m tapedesk tool ts --asset BTC
```

The tape shows DAS-style `Price`, `Qty`, and `Time` columns. New prints appear at the top. Buy prints are green and sell prints are red.

Filter the tape by USD notional value, base asset quantity, or both:

```bash
venv/bin/python -m tapedesk tool ts --asset BTC --min-notional 10000
venv/bin/python -m tapedesk tool ts --asset XRP --min-qty 1000
venv/bin/python -m tapedesk tool ts --asset XRP --min-notional 25 --min-qty 100
```

Use an existing hub instead of opening a direct Coinbase feed:

```bash
venv/bin/python -m tapedesk hub
venv/bin/python -m tapedesk tool screener --source hub --hub-url ws://127.0.0.1:8765
venv/bin/python -m tapedesk tool l2 --asset BTC --source hub --hub-url ws://127.0.0.1:8765
venv/bin/python -m tapedesk tool ts --asset BTC --source hub --hub-url ws://127.0.0.1:8765
```

Launch a tmux workspace directly:

```bash
venv/bin/python -m tapedesk tmux launch --assets BTC,ETH --tools screener,l2,ts
```

For three TS panes across the top and a full-width screener on the bottom:

```bash
venv/bin/python -m tapedesk tmux launch --layout ts-top-screener-bottom --assets BTC,ETH,SOL --tools ts,screener --session tapedesk-demo
tmux attach -t tapedesk-demo
```

That layout starts the local hub inside the tmux session for you, so you do not need to run `venv/bin/python -m tapedesk hub` separately.

Run only the local websocket hub:

```bash
venv/bin/python -m tapedesk hub
```

Tool commands default to `--source auto`: they try the local hub first, then fall back to direct Coinbase feeds if no hub is available.

If you are migrating from BTCBeeper, the matching tapedesk workflow is the `ts` tool and the tmux layout above gives you the old "multiple trade windows plus a market list" feel in one command.

## Tools

- `screener`: live USD market screener with price, volume, and RVol data.
- `l2`: level 2 order book for one asset.
- `ts`: time-and-sales tape for one asset. Press `a` to toggle trade audio and `[`/`]` to adjust the base-size audio filter. Audio starts off unless `--audio` is passed.

Assets can be passed as full Coinbase product IDs such as `BTC-USD` or as shorthand tickers such as `BTC`.
Time-and-sales audio can be started with `--audio`; `--audio-min-qty` sets the initial base-asset quantity threshold.
Time-and-sales filters are optional. `--min-notional` filters by USD trade value, which stays comparable across high-price and low-price assets, and `--min-qty` filters by the selected asset's base quantity.

## Development Checks

Compile-check the package:

```bash
venv/bin/python -m compileall tapedesk tests
```

Run tests once `pytest` is installed:

```bash
venv/bin/python -m pip install -r requirements-dev.txt
venv/bin/python -m pytest -q
```
