"""Shared async HTTP client with retries, timeouts and a tiny TTL cache."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple, TypeVar

import httpx

from ..config import get_settings

log = logging.getLogger("copilot.http")
logging.getLogger("httpx").setLevel(logging.WARNING)
T = TypeVar("T")

_clients: Dict[int, httpx.AsyncClient] = {}


class ProviderError(RuntimeError):
    """Raised when an upstream API fails; message is safe to show to the model."""


def client() -> httpx.AsyncClient:
    """One pooled client per event loop (Streamlit/notebooks may create several loops)."""
    loop = asyncio.get_running_loop()
    key = id(loop)
    existing = _clients.get(key)
    if existing is None or existing.is_closed:
        settings = get_settings()
        existing = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.http_timeout_seconds),
            transport=httpx.AsyncHTTPTransport(retries=2),
            headers={"User-Agent": "business-trip-copilot-mcp/2.0"},
            follow_redirects=True,
        )
        _clients[key] = existing
    return existing


async def get_json(url: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> Any:
    try:
        resp = await client().get(url, params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise ProviderError(f"Network error calling {httpx.URL(url).host}: {exc.__class__.__name__}") from exc
    if resp.status_code == 204:
        return None
    if resp.status_code >= 400:
        raise ProviderError(f"{httpx.URL(url).host} returned HTTP {resp.status_code}")
    return resp.json()


async def post_json(url: str, payload: Any = None, headers: Optional[dict] = None, data: Optional[dict] = None) -> Any:
    try:
        if data is not None:
            resp = await client().post(url, data=data, headers=headers)
        else:
            resp = await client().post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise ProviderError(f"Network error calling {httpx.URL(url).host}: {exc.__class__.__name__}") from exc
    if resp.status_code >= 400:
        detail = resp.text[:300]
        raise ProviderError(f"{httpx.URL(url).host} returned HTTP {resp.status_code}: {detail}")
    return resp.json()


def ttl_cache(seconds: int) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Memoise an async function's successful results for ``seconds``."""

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        store: Dict[str, Tuple[float, T]] = {}

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            key = json.dumps([args, kwargs], sort_keys=True, default=str).lower()
            hit = store.get(key)
            if hit and hit[0] > time.monotonic():
                return hit[1]
            value = await fn(*args, **kwargs)
            store[key] = (time.monotonic() + seconds, value)
            return value

        wrapper.cache_clear = store.clear  # type: ignore[attr-defined]
        return wrapper

    return decorator
