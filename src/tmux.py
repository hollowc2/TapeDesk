from __future__ import annotations

import os
import shlex
import subprocess
import sys
from datetime import datetime
from urllib.parse import urlparse

from .shared import normalize_asset

VALID_TOOLS = {"screener", "l2", "ts"}
CUSTOM_LAYOUTS = {"rows", "ts-top-screener-bottom"}


def build_tool_commands(assets: list[str], tools: list[str], hub_url: str) -> list[str]:
    unknown_tools = sorted(set(tools) - VALID_TOOLS)
    if unknown_tools:
        raise ValueError(f"Unknown tool(s): {', '.join(unknown_tools)}")

    commands: list[str] = []
    if "screener" in tools:
        commands.append(f"{shlex.quote(sys.executable)} -m tapedesk tool screener --source hub --hub-url {shlex.quote(hub_url)}")

    for asset in [normalize_asset(asset) for asset in assets]:
        if "l2" in tools:
            commands.append(
                f"{shlex.quote(sys.executable)} -m tapedesk tool l2 --asset {shlex.quote(asset)} "
                f"--source hub --hub-url {shlex.quote(hub_url)}"
            )
        if "ts" in tools:
            commands.append(
                f"{shlex.quote(sys.executable)} -m tapedesk tool ts --asset {shlex.quote(asset)} "
                f"--source hub --hub-url {shlex.quote(hub_url)}"
            )
    return commands


def build_tool_rows(assets: list[str], tools: list[str], hub_url: str) -> list[list[str]]:
    unknown_tools = sorted(set(tools) - VALID_TOOLS)
    if unknown_tools:
        raise ValueError(f"Unknown tool(s): {', '.join(unknown_tools)}")

    rows: list[list[str]] = []
    for asset in [normalize_asset(asset) for asset in assets]:
        row: list[str] = []
        if "l2" in tools:
            row.append(
                f"{shlex.quote(sys.executable)} -m tapedesk tool l2 --asset {shlex.quote(asset)} "
                f"--source hub --hub-url {shlex.quote(hub_url)}"
            )
        if "ts" in tools:
            row.append(
                f"{shlex.quote(sys.executable)} -m tapedesk tool ts --asset {shlex.quote(asset)} "
                f"--source hub --hub-url {shlex.quote(hub_url)}"
            )
        if row:
            rows.append(row)
    return rows


def build_hub_command(hub_url: str) -> str:
    parsed = urlparse(hub_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8765
    return f"{shlex.quote(sys.executable)} -m tapedesk hub --host {shlex.quote(host)} --port {port}"


def build_screener_command(hub_url: str) -> str:
    return f"{shlex.quote(sys.executable)} -m tapedesk tool screener --source hub --hub-url {shlex.quote(hub_url)}"


def _launch_rows_layout(
    session_name: str,
    assets: list[str],
    tools: list[str],
    hub_url: str,
) -> str:
    rows = build_tool_rows(assets, tools, hub_url)
    screener_command = build_screener_command(hub_url) if "screener" in tools else None
    if not rows and not screener_command:
        raise ValueError("No tools selected")

    if rows:
        first_row = rows[0]
        first_pane = subprocess.check_output(
            ["tmux", "new-session", "-d", "-P", "-F", "#{pane_id}", "-s", session_name, "-n", "tools", first_row[0]],
            text=True,
        ).strip()
        row_panes = [first_pane]
        last_row_pane = first_pane
        for row in rows[1:]:
            last_row_pane = subprocess.check_output(
                ["tmux", "split-window", "-d", "-v", "-P", "-F", "#{pane_id}", "-t", last_row_pane, row[0]],
                text=True,
            ).strip()
            row_panes.append(last_row_pane)

        subprocess.check_call(["tmux", "select-layout", "-t", f"{session_name}:tools", "even-vertical"])

        for pane_id, row in zip(row_panes, rows):
            if len(row) > 1:
                row_width = pane_width(pane_id)
                ts_width = max(1, min(row_width - 1, round(row_width * 0.30)))
                subprocess.check_call(["tmux", "split-window", "-d", "-h", "-l", str(ts_width), "-t", pane_id, row[1]])
        subprocess.check_call(["tmux", "select-window", "-t", f"{session_name}:tools"])
        return "tools"
    elif screener_command:
        subprocess.check_call(["tmux", "new-session", "-d", "-s", session_name, "-n", "screener", screener_command])
        return "screener"
    raise ValueError("No tools selected")


def _launch_ts_top_screener_bottom_layout(session_name: str, assets: list[str], hub_url: str) -> str:
    top_assets = [normalize_asset(asset) for asset in assets]
    if len(top_assets) != 3:
        raise ValueError("The ts-top-screener-bottom layout requires exactly 3 assets")

    first_command = f"{shlex.quote(sys.executable)} -m tapedesk tool ts --asset {shlex.quote(top_assets[0])} --source hub --hub-url {shlex.quote(hub_url)}"
    second_command = f"{shlex.quote(sys.executable)} -m tapedesk tool ts --asset {shlex.quote(top_assets[1])} --source hub --hub-url {shlex.quote(hub_url)}"
    third_command = f"{shlex.quote(sys.executable)} -m tapedesk tool ts --asset {shlex.quote(top_assets[2])} --source hub --hub-url {shlex.quote(hub_url)}"
    screener_command = build_screener_command(hub_url)

    first_pane = subprocess.check_output(
        ["tmux", "new-session", "-d", "-P", "-F", "#{pane_id}", "-s", session_name, "-n", "tools", first_command],
        text=True,
    ).strip()
    subprocess.check_output(
        ["tmux", "split-window", "-d", "-v", "-l", "30%", "-P", "-F", "#{pane_id}", "-t", first_pane, screener_command],
        text=True,
    ).strip()
    second_pane = subprocess.check_output(
        ["tmux", "split-window", "-d", "-h", "-P", "-F", "#{pane_id}", "-t", first_pane, second_command],
        text=True,
    ).strip()
    third_pane = subprocess.check_output(
        ["tmux", "split-window", "-d", "-h", "-P", "-F", "#{pane_id}", "-t", second_pane, third_command],
        text=True,
    ).strip()

    top_width = sum(pane_width(pane_id) for pane_id in (first_pane, second_pane, third_pane))
    target_width = max(1, round(top_width / 3))
    for pane_id in (first_pane, second_pane, third_pane):
        subprocess.check_call(["tmux", "resize-pane", "-t", pane_id, "-x", str(target_width)])

    subprocess.check_call(["tmux", "select-window", "-t", f"{session_name}:tools"])
    return "tools"


def pane_width(pane_id: str) -> int:
    return int(
        subprocess.check_output(
            ["tmux", "display-message", "-p", "-t", pane_id, "#{pane_width}"],
            text=True,
        ).strip()
    )


def current_tmux_session_name() -> str | None:
    if not os.environ.get("TMUX"):
        return None
    try:
        session_name = subprocess.check_output(["tmux", "display-message", "-p", "#S"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return session_name or None


def kill_tmux_session(session_name: str) -> None:
    subprocess.run(["tmux", "kill-session", "-t", session_name], check=False)


def launch_tmux(
    assets: list[str],
    tools: list[str],
    session: str | None = None,
    hub_url: str = "ws://127.0.0.1:8765",
    attach: bool = True,
    layout: str = "rows",
) -> str:
    session_name = session or f"tapedesk-{datetime.now().strftime('%H%M%S')}"
    hub_command = build_hub_command(hub_url)
    screener_command = build_screener_command(hub_url) if "screener" in tools else None
    if layout not in CUSTOM_LAYOUTS:
        raise ValueError(f"Unknown layout: {layout}")

    selected_window: str
    if layout == "rows":
        selected_window = _launch_rows_layout(session_name, assets, tools, hub_url)
    elif layout == "ts-top-screener-bottom":
        if "ts" not in tools or "screener" not in tools:
            raise ValueError("The ts-top-screener-bottom layout requires both ts and screener")
        if set(tools) - {"ts", "screener"}:
            raise ValueError("The ts-top-screener-bottom layout only supports ts and screener")
        selected_window = _launch_ts_top_screener_bottom_layout(session_name, assets, hub_url)
    else:
        raise ValueError(f"Unknown layout: {layout}")

    subprocess.check_call(["tmux", "new-window", "-d", "-t", session_name, "-n", "hub", hub_command])
    if layout == "rows" and screener_command and assets:
        subprocess.check_call(["tmux", "new-window", "-d", "-t", session_name, "-n", "screener", screener_command])

    subprocess.check_call(["tmux", "select-window", "-t", f"{session_name}:{selected_window}"])

    if attach:
        subprocess.check_call(["tmux", "attach-session", "-t", session_name])
    return session_name
