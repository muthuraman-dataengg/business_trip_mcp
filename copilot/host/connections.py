"""Build MCP connections for the configured transport.

One ``ClientGroup`` = one connection per server, shared by:
  * the LangChain ``MCPAdapter`` (tools for the agent), and
  * the host itself (resources, prompts, host-only tools such as bookings).
"""

from __future__ import annotations

import os
from typing import Dict, Optional

from fastmcp import Client as FastMCPClient
from fastmcp.client.group import ClientGroup

from ..config import PROJECT_ROOT, Settings, get_settings, secret

SERVER_NAMES = ("travel", "policy", "work")


def _child_env() -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if isinstance(v, str)}
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + existing if existing else "")
    env.setdefault("PYTHONUTF8", "1")
    return env


def server_url(name: str, settings: Optional[Settings] = None) -> str:
    s = settings or get_settings()
    return {"travel": s.travel_mcp_url, "policy": s.policy_mcp_url, "work": s.work_mcp_url}[name]


def mcp_config(settings: Optional[Settings] = None) -> dict:
    """Standard ``mcpServers`` config (also valid for Claude Desktop / Cursor)."""
    s = settings or get_settings()
    servers = {}
    for name in SERVER_NAMES:
        if s.mcp_transport == "http":
            entry = {"url": server_url(name, s), "transport": "http"}
            if s.mcp_api_key:
                entry["headers"] = {"Authorization": f"Bearer {secret(s.mcp_api_key)}"}
        else:
            entry = {
                "command": s.python,
                "args": ["-m", f"copilot.servers.{name}"],
                "cwd": str(PROJECT_ROOT),
                "env": _child_env(),
            }
        servers[name] = entry
    return {"mcpServers": servers}


def in_process_servers() -> dict:
    from ..servers.policy import server as policy
    from ..servers.travel import server as travel
    from ..servers.work import server as work

    return {"travel": travel, "policy": policy, "work": work}


def build_client_group(settings: Optional[Settings] = None) -> ClientGroup:
    s = settings or get_settings()
    if s.mcp_transport == "inprocess":
        return ClientGroup({name: FastMCPClient(srv) for name, srv in in_process_servers().items()})
    return ClientGroup.from_config(mcp_config(s))
