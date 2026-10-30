"""Isolated data dir + offline HTTP fakes for every test."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("MCP_TRANSPORT", "inprocess")
    monkeypatch.setenv("OPENAI_API_KEY", "")  # overrides any real key in .env
    monkeypatch.delenv("DUFFEL_ACCESS_TOKEN", raising=False)

    from copilot import config
    from copilot.servers import policy

    config.get_settings.cache_clear()
    config.seed_data_dir(data)  # default policies, as on a first run
    policy.index.cache_clear()
    from tests.fakes import install_fake_http

    install_fake_http()
    yield data
    config.get_settings.cache_clear()
