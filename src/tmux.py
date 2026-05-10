from __future__ import annotations

import shlex
import subprocess
import sys
from datetime import datetime

from .app import normalize_asset

VALID_TOOLS = {"screener", "l2", "ts"}


def build_tool_commands(assets: list[str], tools: list[str], hub_url: str) -> list[str]:
    unknown_tools = sorted(set(tools) - VALID_TOOLS)
    if unknown_tools:
        raise ValueError(f"Unknown tool(s): {', '.join(unknown_tools)}")

    commands = [f"{shlex.quote(sys.executable)} -m src hub"]
    if "screener" in tools:
        commands.append(f"{shlex.quote(sys.executable)} -m src tool screener --source hub --hub-url {shlex.quote(hub_url)}")

    for asset in [normalize_asset(asset) for asset in assets]:
        if "l2" in tools:
            commands.append(
                f"{shlex.quote(sys.executable)} -m src tool l2 --asset {shlex.quote(asset)} "
                f"--source hub --hub-url {shlex.quote(hub_url)}"
            )
        if "ts" in tools:
            commands.append(
                f"{shlex.quote(sys.executable)} -m src tool ts --asset {shlex.quote(asset)} "
                f"--source hub --hub-url {shlex.quote(hub_url)}"
            )
    return commands


def launch_tmux(
    assets: list[str],
    tools: list[str],
    session: str | None = None,
    hub_url: str = "ws://127.0.0.1:8765",
    attach: bool = True,
) -> str:
    session_name = session or f"tapeworm-{datetime.now().strftime('%H%M%S')}"
    commands = build_tool_commands(assets, tools, hub_url)
    if not commands:
        raise ValueError("No tools selected")

    subprocess.check_call(["tmux", "new-session", "-d", "-s", session_name, commands[0]])
    for command in commands[1:]:
        subprocess.check_call(["tmux", "split-window", "-t", f"{session_name}:0", command])
        subprocess.check_call(["tmux", "select-layout", "-t", f"{session_name}:0", "tiled"])

    if attach:
        subprocess.check_call(["tmux", "attach-session", "-t", session_name])
    return session_name
