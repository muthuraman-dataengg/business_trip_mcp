"""Offline fakes for every external API, shaped like the real responses.

Used by the test-suite (and handy for classroom demos without internet):

    from tests.fakes import install_fake_http
    install_fake_http()
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx

CITIES = {
    "dubai": dict(name="Dubai", country="United Arab Emirates", country_code="AE", latitude=25.0772, longitude=55.3093, timezone="Asia/Dubai", population=3478300),
    "bangalore": dict(name="Bengaluru", country="India", country_code="IN", latitude=12.9719, longitude=77.5937, timezone="Asia/Kolkata", population=8443675),
    "bengaluru": dict(name="Bengaluru", country="India", country_code="IN", latitude=12.9719, longitude=77.5937, timezone="Asia/Kolkata", population=8443675),
    "london": dict(name="London", country="United Kingdom", country_code="GB", latitude=51.5085, longitude=-0.1257, timezone="Europe/London", population=7556900),
    "phoenix": dict(name="Phoenix", country="United States", country_code="US", latitude=33.4484, longitude=-112.074, timezone="America/Phoenix", population=1680992),
    "new york": dict(name="New York", country="United States", country_code="US", latitude=40.7143, longitude=-74.006, timezone="America/New_York", population=8804190),
    "singapore": dict(name="Singapore", country="Singapore", country_code="SG", latitude=1.2897, longitude=103.8501, timezone="Asia/Singapore", population=3547809),
}
COUNTRIES = {
    "AE": {"name": {"common": "United Arab Emirates"}, "currencies": {"AED": {"name": "UAE dirham", "symbol": "د.إ"}}, "languages": {"ara": "Arabic"}, "idd": {"root": "+9", "suffixes": ["71"]}, "car": {"side": "right"}, "capital": ["Abu Dhabi"], "region": "Asia"},
    "IN": {"name": {"common": "India"}, "currencies": {"INR": {"name": "Indian rupee", "symbol": "₹"}}, "languages": {"eng": "English", "hin": "Hindi"}, "idd": {"root": "+9", "suffixes": ["1"]}, "car": {"side": "left"}, "capital": ["New Delhi"], "region": "Asia"},
    "GB": {"name": {"common": "United Kingdom"}, "currencies": {"GBP": {"name": "British pound", "symbol": "£"}}, "languages": {"eng": "English"}, "idd": {"root": "+4", "suffixes": ["4"]}, "car": {"side": "left"}, "capital": ["London"], "region": "Europe"},
    "US": {"name": {"common": "United States"}, "currencies": {"USD": {"name": "United States dollar", "symbol": "$"}}, "languages": {"eng": "English"}, "idd": {"root": "+1", "suffixes": ["201", "202"]}, "car": {"side": "right"}, "capital": ["Washington, D.C."], "region": "Americas"},
    "SG": {"name": {"common": "Singapore"}, "currencies": {"SGD": {"name": "Singapore dollar", "symbol": "$"}}, "languages": {"eng": "English"}, "idd": {"root": "+6", "suffixes": ["5"]}, "car": {"side": "left"}, "capital": ["Singapore"], "region": "Asia"},
}
USD_RATES = {"USD": 1.0, "INR": 88.2, "AED": 3.6725, "GBP": 0.75, "EUR": 0.86, "SGD": 1.29}


def _rates(base: str) -> dict:
    b = USD_RATES[base]
    return {k: v / b for k, v in USD_RATES.items()}


def _handler(request: httpx.Request) -> httpx.Response:
    host, path, q = request.url.host, request.url.path, request.url.params
    if host == "geocoding-api.open-meteo.com":
        c = CITIES.get(q.get("name", "").lower())
        return httpx.Response(200, json={"results": [c]} if c else {"generationtime_ms": 0.1})
    if host == "api.open-meteo.com":
        if "current" in q:
            return httpx.Response(200, json={"current": {"time": "2026-10-27T21:00", "temperature_2m": 29.4, "apparent_temperature": 31.0, "relative_humidity_2m": 58, "wind_speed_10m": 12.2, "weather_code": 1, "precipitation": 0.0}})
        s, e = date.fromisoformat(q["start_date"]), date.fromisoformat(q["end_date"])
        days = [(s + timedelta(days=i)).isoformat() for i in range((e - s).days + 1)]
        n = len(days)
        return httpx.Response(200, json={"daily": {"time": days, "weather_code": [0, 1, 2, 61][:1] * n, "temperature_2m_max": [33.0 + i % 3 for i in range(n)], "temperature_2m_min": [24.0 + i % 2 for i in range(n)], "precipitation_probability_max": [5 * (i % 3) for i in range(n)], "wind_speed_10m_max": [18.0] * n}})
    if host == "open.er-api.com":
        base = path.rsplit("/", 1)[-1].upper()
        if base not in USD_RATES:
            return httpx.Response(404, json={"result": "error"})
        return httpx.Response(200, json={"result": "success", "base_code": base, "rates": _rates(base), "time_last_update_utc": "Tue, 27 Oct 2026 00:02:31 +0000"})
    if host == "date.nager.at":
        year, cc = path.split("/")[-2:]
        data = {"IN": [{"date": f"{year}-11-08", "localName": "Diwali", "name": "Diwali"}], "AE": [{"date": f"{year}-12-02", "localName": "اليوم الوطني", "name": "National Day"}], "US": [{"date": f"{year}-11-26", "localName": "Thanksgiving Day", "name": "Thanksgiving Day"}]}
        return httpx.Response(200, json=data.get(cc, [])) if cc in data else httpx.Response(404)
    if host == "restcountries.com":
        cc = path.rsplit("/", 1)[-1].upper()
        return httpx.Response(200, json=[COUNTRIES[cc]]) if cc in COUNTRIES else httpx.Response(404)
    if host == "overpass-api.de":
        body = request.content.decode()
        names = [("Rove Downtown", "3"), ("Novotel World Trade Centre", "4"), ("Hilton Garden Inn", "4"), ("Palace Downtown", "5"), ("City Lodge", None)]
        els = [{"type": "node", "id": 1000 + i, "lat": 25.08 + i * 0.01, "lon": 55.30 + i * 0.01, "tags": {"tourism": "hotel", "name": n, **({"stars": s} if s else {}), "addr:street": "Sheikh Zayed Road"}} for i, (n, s) in enumerate(names)]
        assert "tourism" in body
        return httpx.Response(200, json={"elements": els})
    return httpx.Response(404, json={"error": f"no fake for {host}{path}"})


def install_fake_http() -> None:
    """Monkey-patch copilot's shared HTTP client to use the offline fakes."""
    from copilot.integrations import http as http_mod

    fake = httpx.AsyncClient(transport=httpx.MockTransport(_handler))

    def client() -> httpx.AsyncClient:
        return fake

    http_mod.client = client  # type: ignore[assignment]


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(sorted(CITIES)))
