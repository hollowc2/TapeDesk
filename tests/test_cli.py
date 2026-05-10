from src.app import normalize_asset
from src.cli import build_parser
from src.tmux import build_tool_commands


def test_normalize_asset_defaults_to_usd_pair():
    assert normalize_asset("btc") == "BTC-USD"
    assert normalize_asset("eth-usd") == "ETH-USD"


def test_cli_parses_tool_subcommand():
    args = build_parser().parse_args(["tool", "l2", "--asset", "ETH", "--source", "direct"])

    assert args.tool_name == "l2"
    assert args.asset == "ETH"
    assert args.source == "direct"


def test_tmux_commands_include_hub_and_selected_asset_tools():
    commands = build_tool_commands(["BTC", "ETH"], ["screener", "l2", "ts"], "ws://127.0.0.1:8765")

    assert commands[0].endswith("-m src hub")
    assert any("tool screener" in command for command in commands)
    assert any("tool l2 --asset BTC-USD" in command for command in commands)
    assert any("tool ts --asset ETH-USD" in command for command in commands)
