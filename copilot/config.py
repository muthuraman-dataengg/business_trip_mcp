"""Central configuration.

Every tunable value lives here and is read from environment variables (or a
local ``.env`` file). Nothing about a traveler, a city, a model or a port is
hard-coded in the servers or the host — they all read from ``get_settings()``.
"""

from __future__ import annotations

import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
SEED_DIR = PACKAGE_DIR / "seed"  # default policies shipped inside the package
APP_NAME = "trip-copilot"


def is_source_checkout() -> bool:
    """True when running from the git repo (or an editable install), not site-packages."""
    return (PROJECT_ROOT / "pyproject.toml").exists() and "site-packages" not in PROJECT_ROOT.parts


def default_data_dir() -> Path:
    """Repo ./data for a checkout; otherwise the OS's per-user app-data folder."""
    if os.environ.get("DATA_DIR"):
        return Path(os.environ["DATA_DIR"]).expanduser()
    if is_source_checkout():
        return PROJECT_ROOT / "data"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home())
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


def env_files() -> tuple:
    """.env lookup, lowest to highest priority: data dir, project root, current directory."""
    files = [default_data_dir() / ".env"]
    if is_source_checkout():
        files.append(PROJECT_ROOT / ".env")
    files.append(Path.cwd() / ".env")
    return tuple(dict.fromkeys(p.resolve() for p in files))  # same file once, order kept


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------------- LLM ----------------
    llm_model: str = Field(
        "openai:gpt-5.6",
        description="LangChain init_chat_model id, e.g. openai:gpt-5.6, anthropic:claude-sonnet-5-5",
    )
    llm_temperature: Optional[float] = None
    llm_reasoning_effort: Optional[str] = Field(
        "none", description="Passed to OpenAI reasoning models; ignored for other providers"
    )
    embedding_model: str = "text-embedding-3-small"
    openai_api_key: Optional[SecretStr] = None

    # ---------------- Storage ----------------
    data_dir: Path = Field(default_factory=default_data_dir)
    database_path: Optional[Path] = None
    policy_dir: Optional[Path] = None

    # ---------------- MCP transport ----------------
    mcp_transport: Literal["stdio", "http", "inprocess"] = Field(
        "stdio", description="stdio: host spawns servers | http: connect to running servers | inprocess: same Python process"
    )
    mcp_host: str = "127.0.0.1"
    travel_mcp_url: str = "http://127.0.0.1:8001/mcp"
    policy_mcp_url: str = "http://127.0.0.1:8002/mcp"
    work_mcp_url: str = "http://127.0.0.1:8003/mcp"
    travel_mcp_port: int = 8001
    policy_mcp_port: int = 8002
    work_mcp_port: int = 8003
    mcp_api_key: Optional[SecretStr] = Field(
        None, description="If set, HTTP MCP servers require 'Authorization: Bearer <key>'"
    )
    mcp_allowed_hosts: str = Field(
        "", description="Comma separated Host headers allowed by HTTP servers (DNS-rebinding protection)"
    )

    # ---------------- External providers ----------------
    http_timeout_seconds: float = 15.0
    duffel_access_token: Optional[SecretStr] = Field(
        None, description="Duffel API token (test or live). Without it flights are simulated and labelled."
    )
    overpass_url: str = "https://overpass-api.de/api/interpreter"

    # ---------------- Agent ----------------
    agent_recursion_limit: int = 40
    log_level: str = "INFO"

    # ---------- derived paths ----------
    @property
    def db_path(self) -> Path:
        return self.database_path or (self.data_dir / "copilot.db")

    @property
    def policies_path(self) -> Path:
        return self.policy_dir or (self.data_dir / "policies")

    @property
    def cache_dir(self) -> Path:
        path = self.data_dir / "cache"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def python(self) -> str:
        return sys.executable


def secret(value) -> Optional[str]:  # noqa: ANN001
    """Accept SecretStr or plain str (e.g. from Settings.model_copy overrides)."""
    if value is None:
        return None
    return value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)


def _quiet_noisy_loggers() -> None:
    import logging

    for name in ("httpx", "httpx2", "mcp.client", "fastmcp"):
        logging.getLogger(name).setLevel(logging.WARNING)


def seed_data_dir(data_dir: Path) -> None:
    """Copy the default policies into the data dir on first run. Never overwrites edits."""
    data_dir.mkdir(parents=True, exist_ok=True)
    for src in SEED_DIR.rglob("*"):
        if src.is_file():
            dst = data_dir / src.relative_to(SEED_DIR)
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _quiet_noisy_loggers()
    settings = Settings()
    seed_data_dir(settings.data_dir)
    # Make the key visible to LangChain/OpenAI SDKs that read os.environ.
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key.get_secret_value())
    return settings
