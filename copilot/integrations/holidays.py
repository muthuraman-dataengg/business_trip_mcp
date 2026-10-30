"""Public holidays (Nager.Date, keyless)."""

from __future__ import annotations

from datetime import date
from typing import List

from .http import ProviderError, get_json, ttl_cache


@ttl_cache(seconds=24 * 3600)
async def _year(country_code: str, year: int) -> List[dict]:
    try:
        data = await get_json(f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country_code.upper()}")
    except ProviderError as exc:
        if "404" in str(exc):
            return []
        raise
    return data or []


async def holidays_between(country_code: str, start: date, end: date) -> dict:
    found = []
    for year in range(start.year, end.year + 1):
        for h in await _year(country_code, year):
            d = date.fromisoformat(h["date"])
            if start <= d <= end:
                found.append({"date": h["date"], "name": h.get("name"), "local_name": h.get("localName")})
    return {
        "country_code": country_code.upper(),
        "from": start.isoformat(),
        "to": end.isoformat(),
        "holidays": found,
        "coverage_note": None if found else "No public holidays found in range (or country not covered by Nager.Date).",
        "source": "date.nager.at",
    }
