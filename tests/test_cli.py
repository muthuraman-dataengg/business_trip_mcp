from __future__ import annotations

import json
import sys

import pytest

from copilot import cli


def test_local_config_uses_running_python(monkeypatch):
    monkeypatch.setattr(cli, "package_installed", lambda: True)
    cfg = cli.build_config("claude-desktop", "local", "trip-", None, None)
    entry = cfg["mcpServers"]["trip-travel"]
    assert entry == {"command": sys.executable, "args": ["-m", "copilot.servers.travel"]}


def test_local_config_falls_back_to_source_folder(monkeypatch):
    monkeypatch.setattr(cli, "package_installed", lambda: False)
    entry = cli.build_config("cursor", "local", "trip-", None, None)["mcpServers"]["trip-work"]
    assert entry["cwd"] == str(cli.PROJECT_ROOT) and entry["env"]["PYTHONPATH"] == str(cli.PROJECT_ROOT)


def test_uvx_config_has_no_local_paths():
    src = "git+https://github.com/example/business_trip_copilot_mcp"
    cfg = cli.build_config("claude-desktop", "uvx", "trip-", src, None)
    assert cfg["mcpServers"]["trip-policy"] == {"command": "uvx", "args": ["--from", src, "trip-policy-mcp"]}


def test_http_config_formats(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "k1")
    from copilot import config

    config.get_settings.cache_clear()
    vs = cli.build_config("vscode", "http", "trip-", None, "https://mcp.example.com")
    assert vs["servers"]["trip-travel"] == {
        "type": "http", "url": "https://mcp.example.com/travel/mcp", "headers": {"Authorization": "Bearer k1"}}
    tpl = cli.build_config("cursor", "http", "", None, "http://localhost:{port}/mcp")
    assert tpl["mcpServers"]["policy"]["url"] == "http://localhost:8002/mcp"
    with pytest.raises(SystemExit):
        cli.build_config("claude-desktop", "http", "trip-", None, None)


def test_install_merges_and_backs_up(tmp_path, monkeypatch):
    target = tmp_path / "mcp.json"
    target.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    monkeypatch.setattr(cli, "client_config_path", lambda client: target)
    cli.install("cursor", cli.build_config("cursor", "uvx", "trip-", "git+https://x/y", None))
    merged = json.loads(target.read_text())
    assert set(merged["mcpServers"]) == {"other", "trip-travel", "trip-policy", "trip-work"}
    assert merged["theme"] == "dark"
    assert list(tmp_path.glob("mcp.json.bak-*"))
