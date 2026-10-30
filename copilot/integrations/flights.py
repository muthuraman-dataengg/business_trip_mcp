"""Flight search.

* With ``DUFFEL_ACCESS_TOKEN`` set: real offers from the Duffel API
  (use a ``duffel_test_...`` token for sandbox data).
* Without it: a deterministic simulator based on the real great-circle distance
  between the two geocoded cities. Every simulated result carries
  ``"source": "simulated"`` so the agent and the UI never present it as real.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import List, Optional

from ..config import get_settings
from .geo import geocode, haversine_km
from .http import ProviderError, get_json, post_json, ttl_cache

DUFFEL = "https://api.duffel.com"
CABIN_MULTIPLIER = {"economy": 1.0, "premium_economy": 1.7, "business": 3.4, "first": 5.5}


def _duffel_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Duffel-Version": "v2",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _iso_duration_hours(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?", value)
    if not m:
        return None
    d, h, mi = (int(x) if x else 0 for x in m.groups())
    return round(d * 24 + h + mi / 60, 2)


@ttl_cache(seconds=7 * 24 * 3600)
async def _duffel_iata(city: str, token: str) -> str:
    data = await get_json(f"{DUFFEL}/places/suggestions", {"query": city}, headers=_duffel_headers(token))
    places = (data or {}).get("data") or []
    for p in places:  # prefer a metropolitan city code (covers all airports)
        if p.get("type") == "city" and p.get("iata_code"):
            return p["iata_code"]
    for p in places:
        if p.get("iata_code"):
            return p["iata_code"]
    raise ProviderError(f"No airport found for '{city}'")


async def _duffel_search(origin: str, destination: str, departure: date, cabin: str, adults: int, token: str) -> List[dict]:
    o, d = await _duffel_iata(origin, token), await _duffel_iata(destination, token)
    body = {
        "data": {
            "slices": [{"origin": o, "destination": d, "departure_date": departure.isoformat()}],
            "passengers": [{"type": "adult"} for _ in range(max(1, adults))],
            "cabin_class": cabin,
        }
    }
    data = await post_json(
        f"{DUFFEL}/air/offer_requests?return_offers=true&supplier_timeout=20000",
        body,
        headers=_duffel_headers(token),
    )
    offers = ((data or {}).get("data") or {}).get("offers") or []
    results = []
    for offer in sorted(offers, key=lambda x: float(x.get("total_amount", 1e12)))[:6]:
        sl = offer["slices"][0]
        segs = sl.get("segments", [])
        first, last = segs[0], segs[-1]
        results.append(
            {
                "offer_id": offer.get("id"),
                "airline": (offer.get("owner") or {}).get("name"),
                "flights": [
                    f"{(s.get('marketing_carrier') or {}).get('iata_code', '')}{s.get('marketing_carrier_flight_number', '')}"
                    for s in segs
                ],
                "origin": o,
                "destination": d,
                "departs_at": first.get("departing_at"),
                "arrives_at": last.get("arriving_at"),
                "duration_hours": _iso_duration_hours(sl.get("duration")),
                "stops": max(0, len(segs) - 1),
                "cabin_class": cabin,
                "total_price": float(offer["total_amount"]),
                "currency": offer.get("total_currency"),
                "source": "duffel",
            }
        )
    return results


async def _simulated(origin: str, destination: str, departure: date, cabin: str, adults: int) -> List[dict]:
    a, b = await geocode(origin), await geocode(destination)
    km = haversine_km(a, b)
    if km < 150:
        raise ProviderError(f"{a.name} and {b.name} are {km:.0f} km apart — consider rail or road instead of a flight.")
    seed = int(hashlib.sha256(f"{a.name}{b.name}{departure}".encode()).hexdigest(), 16)
    base_hours = km / 780 + 0.6
    base_fare = (55 + km * 0.085) * CABIN_MULTIPLIER.get(cabin, 1.0)
    shapes = [  # (label, stops, extra hours, price factor, departure hour)
        ("Simulated Carrier A", 0, 0.0, 1.18, 8 + seed % 4),
        ("Simulated Carrier B", 1, 2.5 + (seed % 3), 0.86, 13 + seed % 5),
        ("Simulated Carrier C", 0 if km < 9000 else 1, 0.4, 1.05, 20 + seed % 3),
    ]
    out = []
    for i, (carrier, stops, extra, factor, hour) in enumerate(shapes):
        jitter = 0.92 + ((seed >> (i * 8)) % 17) / 100
        out.append(
            {
                "offer_id": f"SIM-{(seed >> i) % 100000:05d}",
                "airline": carrier,
                "flights": [f"SIM{(seed >> (i + 3)) % 900 + 100}"] + ([f"SIM{(seed >> (i + 5)) % 900 + 100}"] if stops else []),
                "origin": a.name,
                "destination": b.name,
                "departs_at": f"{departure.isoformat()}T{hour:02d}:{(seed >> i) % 4 * 15:02d}",
                "duration_hours": round(base_hours + extra, 1),
                "distance_km": round(km),
                "stops": stops,
                "cabin_class": cabin,
                "total_price": round(base_fare * factor * jitter * max(1, adults), 0),
                "currency": "USD",
                "source": "simulated",
            }
        )
    return sorted(out, key=lambda x: x["total_price"])


async def search(origin: str, destination: str, departure: date, cabin: str = "economy", adults: int = 1) -> dict:
    token = get_settings().duffel_access_token
    if token:
        from ..config import secret

        offers = await _duffel_search(origin, destination, departure, cabin, adults, secret(token))
        provider = "duffel"
    else:
        offers = await _simulated(origin, destination, departure, cabin, adults)
        provider = "simulated"
    result = {"provider": provider, "offers": offers}
    if provider == "simulated":
        result["disclaimer"] = (
            "Simulated fares (no DUFFEL_ACCESS_TOKEN configured). Durations use real distance; "
            "prices and carriers are illustrative only."
        )
    return result
