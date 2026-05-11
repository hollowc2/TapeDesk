from __future__ import annotations

import argparse
import logging
import sys

from .app import TapewormApp, DEFAULT_HUB_URL
from .hub import run_hub
from .shared import normalize_asset
from .tmux import launch_tmux


TOOLS = {"screener", "l2", "ts"}


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler("tapeworm.log")],
    )


def comma_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def prompt_orchestrator(args: argparse.Namespace) -> int:
    print("tapeworm orchestrator")
    print("Enter assets as Coinbase symbols or tickers. Example: BTC,ETH,SOL")
    assets = comma_list(input("Assets: ").strip() or "BTC")
    print("Tools: screener, l2, ts")
    tools = comma_list(input("Tools: ").strip() or "screener,l2,ts")
    unknown_tools = sorted(set(tools) - TOOLS)
    if unknown_tools:
        raise SystemExit(f"Unknown tool(s): {', '.join(unknown_tools)}")
    session = launch_tmux(
        assets=assets,
        tools=tools,
        session=args.session,
        hub_url=args.hub_url,
        attach=not args.no_attach,
    )
    print(f"Started tmux session {session}")
    return 0


def run_tool(args: argparse.Namespace) -> int:
    configure_logging()
    symbol = normalize_asset(args.asset)
    TapewormApp(
        mode=args.tool_name,
        symbol=symbol,
        source=args.source,
        hub_url=args.hub_url,
        time_sales_min_notional=getattr(args, "min_notional", 0),
        time_sales_min_size=getattr(args, "min_qty", None),
    ).run()
    return 0


def run_legacy_app(_: argparse.Namespace | None = None) -> int:
    configure_logging()
    TapewormApp().run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tapeworm")
    subparsers = parser.add_subparsers(dest="command")

    orchestrator = subparsers.add_parser("orchestrator", help="Pick assets/tools and launch a tmux workspace")
    orchestrator.add_argument("--session", help="tmux session name")
    orchestrator.add_argument("--hub-url", default=DEFAULT_HUB_URL)
    orchestrator.add_argument("--no-attach", action="store_true", help="Create tmux session without attaching")
    orchestrator.set_defaults(func=prompt_orchestrator)

    hub = subparsers.add_parser("hub", help="Run the local market-data websocket hub")
    hub.add_argument("--host", default="127.0.0.1")
    hub.add_argument("--port", type=int, default=8765)
    hub.set_defaults(func=lambda args: run_hub(args.host, args.port) or 0)

    tmux = subparsers.add_parser("tmux", help="tmux helpers")
    tmux_subparsers = tmux.add_subparsers(dest="tmux_command", required=True)
    launch = tmux_subparsers.add_parser("launch", help="Launch a tapeworm tmux layout")
    launch.add_argument("--assets", default="BTC", help="Comma-separated assets, e.g. BTC,ETH")
    launch.add_argument("--tools", default="screener,l2,ts", help="Comma-separated tools: screener,l2,ts")
    launch.add_argument("--session", help="tmux session name")
    launch.add_argument("--hub-url", default=DEFAULT_HUB_URL)
    launch.add_argument("--no-attach", action="store_true")
    launch.set_defaults(
        func=lambda args: launch_tmux(
            assets=comma_list(args.assets),
            tools=comma_list(args.tools),
            session=args.session,
            hub_url=args.hub_url,
            attach=not args.no_attach,
        )
        and 0
    )

    tool = subparsers.add_parser("tool", help="Run one tapeworm tool")
    tool_subparsers = tool.add_subparsers(dest="tool_name", required=True)
    for tool_name in ("screener", "l2", "ts"):
        tool_parser = tool_subparsers.add_parser(tool_name)
        tool_parser.add_argument("--asset", default="BTC-USD")
        tool_parser.add_argument("--source", choices=["auto", "hub", "direct"], default="auto")
        tool_parser.add_argument("--hub-url", default=DEFAULT_HUB_URL)
        if tool_name == "ts":
            tool_parser.add_argument(
                "--min-notional",
                type=float,
                default=0,
                help="Only show prints with at least this USD notional value",
            )
            tool_parser.add_argument(
                "--min-qty",
                type=float,
                default=None,
                help="Only show prints with at least this base-asset quantity",
            )
        tool_parser.set_defaults(func=run_tool)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        return run_legacy_app()
    result = args.func(args)
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
