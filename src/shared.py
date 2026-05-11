from __future__ import annotations


def normalize_asset(asset: str) -> str:
    symbol = asset.strip().upper()
    if "-" not in symbol:
        symbol = f"{symbol}-USD"
    return symbol
