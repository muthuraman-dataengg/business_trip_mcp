"""Hotel discovery from OpenStreetMap (Overpass API, keyless).

Hotel names, star ratings and locations are real OpenStreetMap data. OSM has no
prices, so ``indicative_nightly_usd`` is an estimate by star band and is
labelled as such.
"""

from __future__ import annotations

from typing import List, Optional

from ..config import get_settings
from .geo import geocode, haversine_km, Place
from .http import ProviderError, post_json, ttl_cache

INDICATIVE_USD = {1: 45, 2: 70, 3: 110, 4: 185, 5: 340}


def _stars(tags: dict) -> Optional[int]:
    raw = (tags.get("stars") or "").strip().upper().replace("S", "")
    try:
        value = int(float(raw[:1]))
        return value if 1 <= value <= 5 else None
    except ValueError:
        return None


@ttl_cache(seconds=6 * 3600)
async def _overpass(lat: float, lon: float, radius_m: int) -> List[dict]:
    query = f"""
[out:json][timeout:25];
(
  node["tourism"="hotel"]["name"](around:{radius_m},{lat},{lon});
  way["tourism"="hotel"]["name"](around:{radius_m},{lat},{lon});
);
out center tags 150;
"""
    data = await post_json(get_settings().overpass_url, data={"data": query})
    return (data or {}).get("elements", [])


async def search(city: str, min_stars: int = 1, max_stars: int = 5, limit: int = 8, radius_km: float = 8.0) -> dict:
    place = await geocode(city)
    try:
        elements = await _overpass(place.latitude, place.longitude, int(radius_km * 1000))
    except ProviderError as exc:
        raise ProviderError(f"Hotel directory unavailable right now ({exc}). Try again shortly.") from exc

    hotels = []
    for el in elements:
        tags = el.get("tags", {})
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        stars = _stars(tags)
        if stars is not None and not (min_stars <= stars <= max_stars):
            continue
        dist = haversine_km(place, Place(name="h", latitude=lat, longitude=lon))
        hotels.append(
            {
                "name": tags.get("name"),
                "stars": stars,
                "brand": tags.get("brand") or tags.get("operator"),
                "distance_to_centre_km": round(dist, 1),
                "address": " ".join(
                    filter(None, [tags.get("addr:housenumber"), tags.get("addr:street"), tags.get("addr:city")])
                )
                or None,
                "website": tags.get("website") or tags.get("contact:website"),
                "indicative_nightly_usd": INDICATIVE_USD.get(stars) if stars else None,
                "osm_id": f"{el.get('type')}/{el.get('id')}",
            }
        )
    # rated hotels inside the requested band first, then by distance
    hotels.sort(key=lambda h: (h["stars"] is None, h["distance_to_centre_km"]))
    return {
        "city": place.name,
        "star_range": [min_stars, max_stars],
        "hotels": hotels[:limit],
        "price_note": "Prices are indicative estimates by star band — confirm with the property or TMC.",
        "source": "openstreetmap.org (Overpass)",
    }
