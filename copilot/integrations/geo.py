"""Geocoding, country facts and local time (Open-Meteo + REST Countries, both keyless)."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from .http import ProviderError, get_json, ttl_cache

try:  # Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore


class Place(BaseModel):
    name: str
    country: Optional[str] = None
    country_code: Optional[str] = None
    admin1: Optional[str] = None
    latitude: float
    longitude: float
    timezone: Optional[str] = None
    population: Optional[int] = None


@ttl_cache(seconds=24 * 3600)
async def geocode(city: str) -> Place:
    data = await get_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        {"name": city.strip(), "count": 1, "language": "en", "format": "json"},
    )
    results = (data or {}).get("results") or []
    if not results:
        raise ProviderError(f"Could not find a city called '{city}'")
    r = results[0]
    return Place(
        name=r["name"],
        country=r.get("country"),
        country_code=r.get("country_code"),
        admin1=r.get("admin1"),
        latitude=r["latitude"],
        longitude=r["longitude"],
        timezone=r.get("timezone"),
        population=r.get("population"),
    )


@ttl_cache(seconds=7 * 24 * 3600)
async def country_facts(country_code: str) -> dict:
    data = await get_json(
        f"https://restcountries.com/v3.1/alpha/{country_code}",
        {"fields": "name,currencies,languages,idd,car,capital,region"},
    )
    item = data[0] if isinstance(data, list) else data
    currencies = item.get("currencies") or {}
    idd = item.get("idd") or {}
    suffixes = idd.get("suffixes") or [""]
    return {
        "country": (item.get("name") or {}).get("common"),
        "capital": (item.get("capital") or [None])[0],
        "region": item.get("region"),
        "currencies": [
            {"code": code, "name": c.get("name"), "symbol": c.get("symbol")} for code, c in currencies.items()
        ],
        "languages": list((item.get("languages") or {}).values()),
        "calling_code": f"{idd.get('root', '')}{suffixes[0] if len(suffixes) == 1 else ''}",
        "driving_side": (item.get("car") or {}).get("side"),
    }


def local_time(tz: Optional[str]) -> Optional[str]:
    if not tz or ZoneInfo is None:
        return None
    try:
        return datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M (%Z, UTC%z)")
    except Exception:  # noqa: BLE001 - tzdata may be missing on Windows
        return None


def haversine_km(a: Place, b: Place) -> float:
    r = 6371.0
    p1, p2 = math.radians(a.latitude), math.radians(b.latitude)
    dp = p2 - p1
    dl = math.radians(b.longitude - a.longitude)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))
