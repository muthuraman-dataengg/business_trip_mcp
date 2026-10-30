"""Live weather and trip-date forecast from Open-Meteo (keyless)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from .geo import geocode
from .http import get_json, ttl_cache

WMO = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle", 55: "Dense drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Heavy freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Rain showers", 81: "Heavy showers", 82: "Violent showers",
    85: "Snow showers", 86: "Heavy snow showers", 95: "Thunderstorm",
    96: "Thunderstorm with hail", 99: "Severe thunderstorm with hail",
}
FORECAST_HORIZON_DAYS = 16


@ttl_cache(seconds=600)
async def current_weather(city: str) -> dict:
    place = await geocode(city)
    data = await get_json(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": place.latitude,
            "longitude": place.longitude,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code,precipitation",
            "timezone": "auto",
        },
    )
    cur = data.get("current", {})
    return {
        "city": place.name,
        "country": place.country,
        "observed_at_local": cur.get("time"),
        "condition": WMO.get(cur.get("weather_code"), "Unknown"),
        "temperature_c": cur.get("temperature_2m"),
        "feels_like_c": cur.get("apparent_temperature"),
        "humidity_percent": cur.get("relative_humidity_2m"),
        "wind_speed_kmh": cur.get("wind_speed_10m"),
        "precipitation_mm": cur.get("precipitation"),
        "source": "open-meteo.com",
    }


@ttl_cache(seconds=1800)
async def trip_forecast(city: str, start: date, end: Optional[date] = None) -> dict:
    end = end or start
    today = date.today()
    horizon = today + timedelta(days=FORECAST_HORIZON_DAYS - 1)
    place = await geocode(city)
    if start > horizon:
        return {
            "city": place.name,
            "available": False,
            "message": (
                f"Daily forecasts are only published {FORECAST_HORIZON_DAYS} days ahead "
                f"(until {horizon}). Re-check closer to departure."
            ),
        }
    q_start, q_end = max(start, today), min(end, horizon)
    data = await get_json(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": place.latitude,
            "longitude": place.longitude,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max",
            "timezone": "auto",
            "start_date": q_start.isoformat(),
            "end_date": q_end.isoformat(),
        },
    )
    d = data.get("daily", {})
    days = []
    for i, day in enumerate(d.get("time", [])):
        days.append(
            {
                "date": day,
                "condition": WMO.get((d.get("weather_code") or [None])[i], "Unknown"),
                "max_c": (d.get("temperature_2m_max") or [None])[i],
                "min_c": (d.get("temperature_2m_min") or [None])[i],
                "rain_chance_percent": (d.get("precipitation_probability_max") or [None])[i],
                "max_wind_kmh": (d.get("wind_speed_10m_max") or [None])[i],
            }
        )
    out = {"city": place.name, "available": True, "days": days, "source": "open-meteo.com"}
    if end > horizon:
        out["message"] = f"Forecast truncated at {horizon}; later days not yet published."
    return out
