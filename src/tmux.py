from __future__ import annotations

import os
import shlex
import subprocess
import sys
from datetime import datetime
from urllib.parse import urlparse

from .shared import normalize_asset

VALID_TOOLS = {"screener", "l2", "ts"}


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
) -> str:
    session_name = session or f"tapedesk-{datetime.now().strftime('%H%M%S')}"
    hub_command = build_hub_command(hub_url)
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
    elif screener_command:
        subprocess.check_call(["tmux", "new-session", "-d", "-s", session_name, "-n", "screener", screener_command])

    subprocess.check_call(["tmux", "new-window", "-d", "-t", session_name, "-n", "hub", hub_command])
    if screener_command and rows:
        subprocess.check_call(["tmux", "new-window", "-d", "-t", session_name, "-n", "screener", screener_command])

    if rows:
        subprocess.check_call(["tmux", "select-window", "-t", f"{session_name}:tools"])
    elif screener_command:
        subprocess.check_call(["tmux", "select-window", "-t", f"{session_name}:screener"])

    if attach:
        subprocess.check_call(["tmux", "attach-session", "-t", session_name])
    return session_name
