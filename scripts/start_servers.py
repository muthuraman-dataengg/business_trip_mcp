"""Start all three MCP servers over Streamable HTTP and wait until healthy.

    python scripts/start_servers.py

Ports and auth come from .env (TRAVEL_MCP_PORT, POLICY_MCP_PORT, WORK_MCP_PORT, MCP_API_KEY).
Press Ctrl+C to stop them all.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from copilot.config import get_settings  # noqa: E402


def wait_healthy(port: int, host: str, timeout: float = 30.0) -> bool:
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}/healthz"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:  # noqa: S310 - local health check
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(0.4)
    return False


def main() -> int:
    s = get_settings()
    servers = [("travel", s.travel_mcp_port), ("policy", s.policy_mcp_port), ("work", s.work_mcp_port)]
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1"}
    procs = []
    for name, port in servers:
        cmd = [sys.executable, "-m", f"copilot.servers.{name}", "--transport", "http", "--host", s.mcp_host, "--port", str(port)]
        procs.append((name, port, subprocess.Popen(cmd, cwd=ROOT, env=env)))

    ok = True
    for name, port, proc in procs:
        healthy = wait_healthy(port, s.mcp_host)
        ok &= healthy
        status = "ready" if healthy else "FAILED"
        print(f"  {name:<7} http://{s.mcp_host}:{port}/mcp  [{status}]")
    print("\nAuth:", "Bearer token required" if s.mcp_api_key else "disabled (set MCP_API_KEY to enable)")
    if not ok:
        print("Some servers failed to start — see logs above.")
    print("Press Ctrl+C to stop.\n")
    try:
        while all(p.poll() is None for _, _, p in procs):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for _, _, p in procs:
            if p.poll() is None:
                p.terminate()
        for _, _, p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        print("All MCP servers stopped.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
