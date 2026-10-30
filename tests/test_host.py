from __future__ import annotations

from datetime import date, timedelta

import pytest

from copilot import db
from copilot.host.orchestrator import CopilotHost, TripRequest, is_agent_safe
from copilot.models import TravelerCreate
from tests.fake_llm import ScriptedToolModel, trip_script

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_trip_request_validation():
    with pytest.raises(ValueError):
        TripRequest("T", "Dubai", "Dubai", date(2026, 12, 1), date(2026, 12, 3))
    with pytest.raises(ValueError):
        TripRequest("T", "A", "B", date(2026, 12, 3), date(2026, 12, 1))
    assert TripRequest("T", "A", "B", "2026-12-01", "2026-12-04").nights == 3


async def test_full_agent_run_and_approval():
    t = db.create_traveler(TravelerCreate(name="Muthu", home_city="Phoenix"))
    s, e = date.today() + timedelta(days=20), date.today() + timedelta(days=24)
    model = ScriptedToolModel(script=trip_script(t.id, "Phoenix", "Dubai", s.isoformat(), e.isoformat()))
    async with CopilotHost(model=model) as host:
        tools = await host.agent_tools()
        names = {x.name for x in tools}
        assert "travel_book_hotel" not in names and "travel_create_traveler" not in names
        assert "travel_request_hotel_booking" in names and all(is_agent_safe(x) for x in tools)

        events = [ev async for ev in host.plan_trip(TripRequest(t.id, "Phoenix", "Dubai", s, e))]
        kinds = [ev.kind for ev in events]
        assert kinds[0] == "context" and kinds[-1] == "final" and "error" not in kinds
        assert "approval_request" in kinds
        assert not db.list_bookings(t.id), "nothing may be booked before approval"

        req_id = host.proposals[0]["id"]
        booking = await host.approve_booking(req_id, "Priya")
        assert booking["status"] == "confirmed"
        assert db.list_bookings(t.id)[0].approved_by == "Priya"
        assert await host.pending_requests() == []


async def test_load_context_uses_profile():
    t = db.create_traveler(TravelerCreate(name="Lee", home_city="Singapore", home_currency="SGD"))
    async with CopilotHost() as host:
        ctx = await host.load_context(TripRequest(t.id, "Singapore", "London", "2026-12-01", "2026-12-05"))
    assert ctx.traveler["home_currency"] == "SGD"
    assert "GBP" in ctx.city_guide
    assert "Lee" in ctx.planning_prompt
