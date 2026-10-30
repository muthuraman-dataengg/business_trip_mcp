"""Travel MCP server.

TOOLS (agent-visible, read-only, real-time)
  get_live_weather, get_trip_forecast, search_flights, search_hotels,
  convert_currency, get_public_holidays
TOOLS (agent-visible, writes a request only)
  request_hotel_booking -> pending approval, nothing is booked
TOOLS (host-only, tagged via _meta)
  book_hotel            -> executes an approved request (requires_approval)
  reject_booking_request, create_traveler, update_traveler
RESOURCES
  travel://travelers                              (list)
  travel://travelers/{traveler_id}/profile        (template)
  travel://travelers/{traveler_id}/bookings       (template)
  travel://booking-requests/pending               (approver inbox)
  city://{city}/guide                             (template, live country facts)
PROMPTS
  plan_business_trip
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .. import db
from ..config import get_settings
from ..integrations import finance, flights, geo, holidays, hotels, weather
from ..integrations.http import ProviderError
from ..models import CabinClass, TravelerCreate, TravelerPreferences

server = MCPServer(
    "travel",
    title="Business Travel MCP Server",
    instructions="Real-time travel data (weather, flights, hotels, FX, holidays) and traveler profiles.",
    version="2.0.0",
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
HOST_ONLY = {"audience": "host"}


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ToolError(f"{field} must be an ISO date (YYYY-MM-DD), got '{value}'") from exc


async def _guard(coro):  # noqa: ANN001, ANN202
    """Turn upstream failures into clean tool errors the model can reason about."""
    try:
        return await coro
    except ProviderError as exc:
        raise ToolError(str(exc)) from exc


# ====================================================================== TOOLS
@server.tool(annotations=READ_ONLY)
async def get_live_weather(city: str) -> Dict[str, Any]:
    """Current weather conditions in a city (live, Open-Meteo)."""
    return await _guard(weather.current_weather(city))


@server.tool(annotations=READ_ONLY)
async def get_trip_forecast(city: str, start_date: str, end_date: Optional[str] = None) -> Dict[str, Any]:
    """Daily forecast for the trip dates (YYYY-MM-DD). Available up to 16 days ahead."""
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date") if end_date else start
    if end < start:
        raise ToolError("end_date must be on or after start_date")
    return await _guard(weather.trip_forecast(city, start, end))


@server.tool(annotations=READ_ONLY)
async def search_flights(
    origin: str,
    destination: str,
    departure_date: str,
    cabin_class: CabinClass = "economy",
    adults: int = 1,
) -> Dict[str, Any]:
    """Search one-way flight offers between two cities for a departure date (YYYY-MM-DD).

    Returns offers sorted by price with duration, stops and the data source
    ('duffel' for live fares, 'simulated' when no provider key is configured).
    """
    dep = _parse_date(departure_date, "departure_date")
    if dep < date.today():
        raise ToolError("departure_date is in the past")
    return await _guard(flights.search(origin, destination, dep, cabin_class, adults))


@server.tool(annotations=READ_ONLY)
async def search_hotels(city: str, min_stars: int = 3, max_stars: int = 4, limit: int = 8) -> Dict[str, Any]:
    """Find real hotels (OpenStreetMap) in a city within a star range, nearest to the centre first."""
    if not 1 <= min_stars <= max_stars <= 5:
        raise ToolError("Star range must satisfy 1 <= min_stars <= max_stars <= 5")
    return await _guard(hotels.search(city, min_stars, max_stars, max(1, min(limit, 20))))


@server.tool(annotations=READ_ONLY)
async def convert_currency(amount: float, from_currency: str, to_currency: str) -> Dict[str, Any]:
    """Convert an amount between ISO currencies using today's exchange rate."""
    return await _guard(finance.convert(amount, from_currency, to_currency))


@server.tool(annotations=READ_ONLY)
async def get_public_holidays(city: str, start_date: str, end_date: str) -> Dict[str, Any]:
    """Public holidays at the destination during the trip — offices may be closed."""
    start, end = _parse_date(start_date, "start_date"), _parse_date(end_date, "end_date")
    place = await _guard(geo.geocode(city))
    if not place.country_code:
        raise ToolError(f"Could not determine the country for {city}")
    result = await _guard(holidays.holidays_between(place.country_code, start, end))
    result["city"] = place.name
    return result


# ------------------------------------------------ approval workflow tools
@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
def request_hotel_booking(
    traveler_id: str,
    city: str,
    hotel_name: str,
    check_in: str,
    nights: int,
    stars: Optional[int] = None,
    nightly_rate: Optional[float] = None,
    currency: Optional[str] = None,
    reason: str = "",
) -> Dict[str, Any]:
    """Submit ONE hotel for human approval. This does NOT book anything.

    Creates a pending booking request that a person approves or rejects in the
    host application. Tell the user the hotel is awaiting approval.
    """
    if not 1 <= nights <= 60:
        raise ToolError("nights must be between 1 and 60")
    if not db.get_traveler(traveler_id):
        raise ToolError(f"Unknown traveler {traveler_id}")
    for r in db.list_booking_requests("pending", traveler_id):  # one pending hotel per stay
        if r.city.lower() == city.lower() and r.check_in.isoformat() == check_in:
            return {**db.to_jsonable(r), "duplicate": True}  # type: ignore[dict-item]
    req = db.create_booking_request(
        traveler_id, city, hotel_name, _parse_date(check_in, "check_in"), nights, stars, nightly_rate, currency, reason
    )
    return {**db.to_jsonable(req), "message": "Pending human approval — nothing has been booked."}  # type: ignore[dict-item]


@server.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False),
    meta={"audience": "host", "requires_approval": True},
)
def book_hotel(
    request_id: str,
    approved_by: str,
    check_in: Optional[str] = None,
    nights: Optional[int] = None,
) -> Dict[str, Any]:
    """Approve a pending booking request and confirm the hotel (SIDE EFFECT).

    Only the host calls this, in response to an explicit human approval.
    The approver may adjust check-in and nights.
    """
    if not approved_by.strip():
        raise ToolError("approved_by is required: bookings need an explicit human approver")
    req = db.get_booking_request(request_id)
    if not req:
        raise ToolError(f"Unknown booking request {request_id}")
    if req.status != "pending":
        raise ToolError(f"Request {request_id} is already {req.status}")
    n = nights if nights is not None else req.nights
    if not 1 <= n <= 60:
        raise ToolError("nights must be between 1 and 60")
    ci = _parse_date(check_in, "check_in") if check_in else req.check_in
    booking = db.create_booking(
        req.traveler_id, req.city, req.hotel_name, ci, n, req.stars, req.nightly_rate, req.currency, approved_by.strip()
    )
    db.decide_booking_request(request_id, "approved", approved_by.strip(), booking.id)
    return db.to_jsonable(booking)  # type: ignore[return-value]


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True), meta=HOST_ONLY)
def reject_booking_request(request_id: str, rejected_by: str) -> Dict[str, Any]:
    """Reject a pending booking request."""
    if not rejected_by.strip():
        raise ToolError("rejected_by is required")
    try:
        return db.to_jsonable(db.decide_booking_request(request_id, "rejected", rejected_by.strip()))  # type: ignore[return-value]
    except KeyError as exc:
        raise ToolError(str(exc)) from exc


@server.tool(annotations=ToolAnnotations(readOnlyHint=False), meta=HOST_ONLY)
def create_traveler(
    name: str,
    home_city: str,
    home_currency: str = "USD",
    email: Optional[str] = None,
    department: str = "",
    preferences: Optional[TravelerPreferences] = None,
) -> Dict[str, Any]:
    """Register a traveler profile with their travel preferences."""
    payload = TravelerCreate(
        name=name,
        home_city=home_city,
        home_currency=home_currency,
        email=email,
        department=department,
        preferences=preferences or TravelerPreferences(),
    )
    return db.to_jsonable(db.create_traveler(payload))  # type: ignore[return-value]


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True), meta=HOST_ONLY)
def update_traveler(
    traveler_id: str,
    name: str,
    home_city: str,
    home_currency: str = "USD",
    email: Optional[str] = None,
    department: str = "",
    preferences: Optional[TravelerPreferences] = None,
) -> Dict[str, Any]:
    """Replace a traveler's profile and preferences."""
    payload = TravelerCreate(
        name=name,
        home_city=home_city,
        home_currency=home_currency,
        email=email,
        department=department,
        preferences=preferences or TravelerPreferences(),
    )
    try:
        return db.to_jsonable(db.update_traveler(traveler_id, payload))  # type: ignore[return-value]
    except KeyError as exc:
        raise ToolError(str(exc)) from exc


# ================================================================== RESOURCES
@server.resource("travel://travelers", mime_type="application/json")
def list_travelers() -> str:
    """All registered travelers (id, name, home city)."""
    return json.dumps(
        [{"id": t.id, "name": t.name, "home_city": t.home_city, "department": t.department} for t in db.list_travelers()]
    )


@server.resource("travel://travelers/{traveler_id}/profile", mime_type="application/json")
def traveler_profile(traveler_id: str) -> str:
    """One traveler's profile and preferences — replaces the old hard-coded preferences."""
    traveler = db.get_traveler(traveler_id)
    if not traveler:
        raise ValueError(f"Unknown traveler {traveler_id}")
    return traveler.model_dump_json(indent=2)


@server.resource("travel://travelers/{traveler_id}/bookings", mime_type="application/json")
def traveler_bookings(traveler_id: str) -> str:
    """Confirmed bookings for a traveler."""
    return json.dumps(db.to_jsonable(db.list_bookings(traveler_id)), indent=2)


@server.resource("travel://booking-requests/pending", mime_type="application/json")
def pending_booking_requests() -> str:
    """Every booking request waiting for a human decision (approver inbox)."""
    names = {t.id: t.name for t in db.list_travelers()}
    return json.dumps(
        [{**db.to_jsonable(r), "traveler_name": names.get(r.traveler_id or "", "")} for r in db.list_booking_requests("pending")],  # type: ignore[dict-item]
        indent=2,
    )


@server.resource("city://{city}/guide", mime_type="text/markdown")
async def city_guide(city: str) -> str:
    """Live destination brief: country facts, currency, local time, practical tips."""
    try:
        place = await geo.geocode(city)
    except ProviderError as exc:
        return f"# {city}\n\nDestination data unavailable: {exc}"
    lines = [f"# {place.name}, {place.country or ''}".rstrip(", ")]
    lt = geo.local_time(place.timezone)
    if place.timezone:
        lines.append(f"- Time zone: {place.timezone}" + (f" — local time now {lt}" if lt else ""))
    if place.country_code:
        try:
            facts = await geo.country_facts(place.country_code)
            cur = ", ".join(f"{c['code']} ({c['name']})" for c in facts["currencies"])
            lines += [
                f"- Currency: {cur}",
                f"- Languages: {', '.join(facts['languages'])}",
                f"- Dialling code: {facts['calling_code']}",
                f"- Traffic drives on the {facts['driving_side']}",
            ]
        except ProviderError:
            lines.append("- Country facts temporarily unavailable")
    lines += [
        "",
        "Practical checklist:",
        "- Keep a 3-hour airport buffer for international departures.",
        "- Confirm visa / entry requirements for your passport.",
        "- Verify meeting venue address and local transport options before departure.",
    ]
    return "\n".join(lines)


# ==================================================================== PROMPTS
@server.prompt(title="Plan a business trip")
def plan_business_trip(
    traveler_name: str,
    origin: str,
    destination: str,
    start_date: str,
    end_date: str,
    purpose: str = "client meetings",
) -> str:
    """Reusable, policy-aware planning instructions for the copilot agent."""
    try:
        nights = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days
    except ValueError:
        nights = "?"
    return f"""
Plan a business trip for {traveler_name}: {origin} -> {destination},
{start_date} to {end_date} ({nights} nights). Purpose: {purpose}.

Work through these steps, calling tools rather than guessing:
1. Check the live weather and the forecast for the trip dates.
2. Check public holidays at the destination during the trip.
3. Search outbound flights (departure {start_date}) and return flights (departure {end_date})
   in the traveler's preferred cabin; respect preferred/avoided airlines and max stops.
4. Search hotels within the traveler's star range AND the policy cap.
5. Use policy search + compliance check on the chosen flight and hotel.
   Convert prices into the policy currency when needed.
6. Create 1-3 concrete preparation tasks (visa, insurance, slides...) with due dates.
7. Submit ONE hotel with request_hotel_booking. It only creates a pending request; never claim it is booked.

Finish with a concise plan: summary, weather, flights (out + back), hotel recommendation,
policy compliance (with any approvals needed), estimated cost in the traveler's home currency,
tasks created, and items awaiting human approval. Flag simulated/indicative data clearly.
""".strip()


def main() -> None:
    """Console entry point: ``trip-travel-mcp [--transport stdio|http]``."""
    from .runner import run

    run(server, get_settings().travel_mcp_port)


if __name__ == "__main__":
    main()
