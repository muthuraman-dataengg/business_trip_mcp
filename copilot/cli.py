"""trip-copilot command line.

    trip-copilot config                          # print config for Claude Desktop (this machine)
    trip-copilot config --client cursor          # cursor | vscode | claude-code | claude-desktop
    trip-copilot config --mode uvx --source git+https://github.com/you/business_trip_copilot_mcp
    trip-copilot config --mode http --base-url https://mcp.example.com
    trip-copilot config --client claude-desktop --install   # merge into the client's config file
    trip-copilot serve travel --transport http
    trip-copilot info                            # where data, .env and the database live

Nothing is hard-coded: paths come from the Python that runs this command,
ports and keys from your settings.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .config import PROJECT_ROOT, env_files, get_settings, is_source_checkout, secret

SERVERS = ("travel", "policy", "work")
CLIENTS = ("claude-desktop", "cursor", "vscode", "claude-code")
DIST_NAME = "business-trip-copilot"


# ------------------------------------------------------------------ helpers
def package_installed() -> bool:
    """True if `pip install .` / `pip install -e .` has been run for this Python."""
    try:
        from importlib.metadata import PackageNotFoundError, distribution

        distribution(DIST_NAME)
        return True
    except PackageNotFoundError:
        return False


def client_config_path(client: str) -> Path:
    """Where each client keeps its MCP config on this OS."""
    home = Path.home()
    if client == "claude-desktop":
        if sys.platform == "win32":
            return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"
        if sys.platform == "darwin":
            return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        return home / ".config" / "Claude" / "claude_desktop_config.json"
    if client == "cursor":
        return home / ".cursor" / "mcp.json"
    if client == "vscode":
        return Path.cwd() / ".vscode" / "mcp.json"
    return Path.cwd() / ".mcp.json"  # claude-code (project scope)


def server_entry(name: str, mode: str, client: str, source: Optional[str], base_url: Optional[str]) -> Dict[str, Any]:
    s = get_settings()
    typed = client in ("vscode", "claude-code")  # these formats carry an explicit "type"

    if mode == "http":
        port = {"travel": s.travel_mcp_port, "policy": s.policy_mcp_port, "work": s.work_mcp_port}[name]
        if base_url:
            url = f"{base_url.rstrip('/')}/{name}/mcp" if "{" not in base_url else base_url.format(name=name, port=port)
        else:
            url = {"travel": s.travel_mcp_url, "policy": s.policy_mcp_url, "work": s.work_mcp_url}[name]
        entry: Dict[str, Any] = {"type": "http", "url": url} if typed else {"url": url}
        if s.mcp_api_key:
            entry["headers"] = {"Authorization": f"Bearer {secret(s.mcp_api_key)}"}
        return entry

    if mode == "uvx":
        src = source or str(PROJECT_ROOT)
        entry = {"command": "uvx", "args": ["--from", src, f"trip-{name}-mcp"]}
    else:  # local: the exact Python running this command
        entry = {"command": sys.executable, "args": ["-m", f"copilot.servers.{name}"]}
        if not package_installed():
            # Not pip-installed yet: point Python at the source folder instead.
            entry["cwd"] = str(PROJECT_ROOT)
            entry["env"] = {"PYTHONPATH": str(PROJECT_ROOT)}
    return {"type": "stdio", **entry} if typed else entry


def build_config(client: str, mode: str, prefix: str, source: Optional[str], base_url: Optional[str]) -> Dict[str, Any]:
    if client == "claude-desktop" and mode == "http":
        raise SystemExit(
            "Claude Desktop's config file launches local servers. Use --mode local or --mode uvx, "
            "or add the HTTP URL as a custom connector in Claude Desktop's settings."
        )
    servers = {f"{prefix}{n}": server_entry(n, mode, client, source, base_url) for n in SERVERS}
    key = "servers" if client == "vscode" else "mcpServers"
    return {key: servers}


def install(client: str, config: Dict[str, Any]) -> Path:
    """Merge our servers into the client's config file, keeping everything else (with a backup)."""
    path = client_config_path(client)
    key = next(iter(config))
    existing: Dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path} is not valid JSON ({exc}); fix it or remove it, then retry.") from exc
        backup = path.with_suffix(path.suffix + f".bak-{datetime.now():%Y%m%d%H%M%S}")
        shutil.copy2(path, backup)
        print(f"Backed up existing config to {backup}")
    existing.setdefault(key, {}).update(config[key])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return path


# ----------------------------------------------------------------- commands
def cmd_config(args: argparse.Namespace) -> int:
    config = build_config(args.client, args.mode, args.prefix, args.source, args.base_url)
    if args.install:
        path = install(args.client, config)
        print(f"Added {', '.join(next(iter(config.values())))} to {path}")
        if args.client == "claude-desktop":
            print("Restart Claude Desktop to load the servers.")
    else:
        print(json.dumps(config, indent=2))
    if args.mode == "local" and not package_installed():
        print("\nTip: run `pip install -e .` once and the generated config no longer needs cwd/PYTHONPATH.", file=sys.stderr)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from importlib import import_module

    from .servers.runner import run

    module = import_module(f"copilot.servers.{args.server}")
    s = get_settings()
    port = args.port or {"travel": s.travel_mcp_port, "policy": s.policy_mcp_port, "work": s.work_mcp_port}[args.server]
    argv = ["--transport", args.transport, "--port", str(port)] + (["--host", args.host] if args.host else [])
    run(module.server, port, argv)
    return 0


def cmd_info(_: argparse.Namespace) -> int:
    s = get_settings()
    found = [str(p) for p in env_files() if p.exists()]
    print(f"Python        : {sys.executable}")
    print(f"Installed     : {'yes' if package_installed() else 'no (running from source)'}")
    print(f"Source folder : {PROJECT_ROOT if is_source_checkout() else '(installed package)'}")
    print(f"Data folder   : {s.data_dir}")
    print(f"Database      : {s.db_path}")
    print(f"Policies      : {s.policies_path}")
    print(f".env files    : {', '.join(found) if found else 'none found — looked in ' + ', '.join(map(str, env_files()))}")
    print(f"Transport     : {s.mcp_transport}")
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="trip-copilot", description="Business Trip Copilot MCP tools")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("config", help="generate (or install) MCP client config for this machine")
    c.add_argument("--client", choices=CLIENTS, default="claude-desktop")
    c.add_argument("--mode", choices=("local", "uvx", "http"), default="local",
                   help="local: this Python | uvx: run from a path or git URL | http: connect to running servers")
    c.add_argument("--source", help="uvx source: a git URL (git+https://...) or a folder (default: this project)")
    c.add_argument("--base-url", help="http mode: e.g. https://mcp.example.com  ->  .../travel/mcp, or a template with {name}/{port}")
    c.add_argument("--prefix", default="trip-", help="server name prefix in the client (default: trip-)")
    c.add_argument("--install", action="store_true", help="merge into the client's config file instead of printing")
    c.set_defaults(func=cmd_config)

    sv = sub.add_parser("serve", help="run one MCP server")
    sv.add_argument("server", choices=SERVERS)
    sv.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    sv.add_argument("--host")
    sv.add_argument("--port", type=int)
    sv.set_defaults(func=cmd_serve)

    i = sub.add_parser("info", help="show where data, database and .env files are")
    i.set_defaults(func=cmd_info)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
