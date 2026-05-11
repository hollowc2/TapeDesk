from src.app import TapewormApp, normalize_asset
from src.cli import build_parser
from src.tmux import build_hub_command, build_tool_commands, build_tool_rows, current_tmux_session_name, launch_tmux


def test_normalize_asset_defaults_to_usd_pair():
    assert normalize_asset("btc") == "BTC-USD"
    assert normalize_asset("eth-usd") == "ETH-USD"


def test_cli_parses_tool_subcommand():
    args = build_parser().parse_args(["tool", "l2", "--asset", "ETH", "--source", "direct"])

    assert args.tool_name == "l2"
    assert args.asset == "ETH"
    assert args.source == "direct"


def test_cli_parses_time_sales_filters():
    args = build_parser().parse_args(["tool", "ts", "--asset", "XRP", "--min-notional", "25", "--min-qty", "100"])

    assert args.tool_name == "ts"
    assert args.asset == "XRP"
    assert args.min_notional == 25
    assert args.min_qty == 100


def test_tmux_commands_exclude_hub_and_include_selected_asset_tools():
    commands = build_tool_commands(["BTC", "ETH"], ["screener", "l2", "ts"], "ws://127.0.0.1:8765")

    assert any("tool screener" in command for command in commands)
    assert any("tool l2 --asset BTC-USD" in command for command in commands)
    assert any("tool ts --asset ETH-USD" in command for command in commands)


def test_tmux_rows_group_l2_and_ts_per_asset():
    rows = build_tool_rows(["BTC", "ETH"], ["l2", "ts"], "ws://127.0.0.1:8765")

    assert len(rows) == 2
    assert "tool l2 --asset BTC-USD" in rows[0][0]
    assert "tool ts --asset BTC-USD" in rows[0][1]
    assert "tool l2 --asset ETH-USD" in rows[1][0]
    assert "tool ts --asset ETH-USD" in rows[1][1]


def test_hub_command_uses_host_and_port_from_url():
    command = build_hub_command("ws://127.0.0.1:8765")

    assert command.endswith("-m src hub --host 127.0.0.1 --port 8765")


def test_current_tmux_session_name_returns_none_outside_tmux(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)

    assert current_tmux_session_name() is None


def test_shutdown_workspace_quits_when_not_in_tmux(monkeypatch):
    app = TapewormApp()
    quit_called = []
    monkeypatch.setattr("src.app.current_tmux_session_name", lambda: None)
    monkeypatch.setattr(app, "quit", lambda: quit_called.append(True))

    app.action_shutdown_workspace()

    assert quit_called == [True]


def test_shutdown_workspace_kills_tmux_session(monkeypatch):
    app = TapewormApp()
    killed = []
    monkeypatch.setattr("src.app.current_tmux_session_name", lambda: "demo")
    monkeypatch.setattr("src.app.kill_tmux_session", lambda session_name: killed.append(session_name))
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,123,0")

    app.action_shutdown_workspace()

    assert killed == ["demo"]


def test_launch_tmux_uses_seventy_thirty_splits_for_each_row():
    calls: list[list[str]] = []
    outputs = iter(["%1", "%2", "100", "100"])

    def fake_check_output(cmd, text=False):
        calls.append(cmd)
        return next(outputs)

    def fake_check_call(cmd):
        calls.append(cmd)

    from unittest.mock import patch

    with patch("src.tmux.subprocess.check_output", side_effect=fake_check_output), patch(
        "src.tmux.subprocess.check_call", side_effect=fake_check_call
    ):
        launch_tmux(["BTC", "ETH"], ["l2", "ts"], session="demo", attach=False)

    assert calls[0][:5] == ["tmux", "new-session", "-d", "-P", "-F"]
    assert any(cmd[:5] == ["tmux", "split-window", "-d", "-v", "-P"] for cmd in calls)
    assert any(cmd[:6] == ["tmux", "display-message", "-p", "-t", "%1", "#{pane_width}"] for cmd in calls)
    assert any(cmd[:6] == ["tmux", "split-window", "-d", "-h", "-l", "30"] for cmd in calls)
