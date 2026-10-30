from __future__ import annotations

from datetime import date, timedelta

import pytest
from mcp import Client

from copilot.servers.policy import server as policy
from copilot.servers.travel import server as travel
from copilot.servers.work import server as work

pytestmark = pytest.mark.anyio
SOON = (date.today() + timedelta(days=20)).isoformat()
LATER = (date.today() + timedelta(days=24)).isoformat()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def data(result):
    assert not result.is_error, result.content
    sc = result.structured_content
    return sc["result"] if set(sc) == {"result"} else sc


async def _traveler(c) -> str:
    r = await c.call_tool("create_traveler", {"name": "Asha", "home_city": "London", "home_currency": "GBP",
                                              "preferences": {"seat": "window", "hotel_max_stars": 4}})
    return data(r)["id"]


async def test_no_hardcoded_preferences():
    async with Client(travel) as c:
        templates = [t.uri_template for t in (await c.list_resource_templates()).resource_templates]
        assert "travel://travelers/{traveler_id}/profile" in templates
        assert "travel://preferences" not in [str(r.uri) for r in (await c.list_resources()).resources]
        tid = await _traveler(c)
        profile = (await c.read_resource(f"travel://travelers/{tid}/profile")).contents[0].text
        assert '"seat": "window"' in profile


async def test_host_only_tools_are_tagged():
    async with Client(travel) as c:
        meta = {t.name: t.meta or {} for t in (await c.list_tools()).tools}
    assert meta["book_hotel"]["requires_approval"] is True
    assert meta["create_traveler"]["audience"] == "host"
    assert meta["request_hotel_booking"] == {}
    assert meta["get_live_weather"] == {}


async def test_realtime_tools():
    async with Client(travel) as c:
        assert data(await c.call_tool("get_live_weather", {"city": "Dubai"}))["condition"] == "Mainly clear"
        d1, d2 = (date.today() + timedelta(days=3)).isoformat(), (date.today() + timedelta(days=7)).isoformat()
        fc = data(await c.call_tool("get_trip_forecast", {"city": "Dubai", "start_date": d1, "end_date": d2}))
        assert fc["available"] and len(fc["days"]) == 5
        far = (date.today() + timedelta(days=60)).isoformat()
        assert data(await c.call_tool("get_trip_forecast", {"city": "Dubai", "start_date": far}))["available"] is False
        fl = data(await c.call_tool("search_flights", {"origin": "Phoenix", "destination": "Dubai", "departure_date": SOON}))
        assert fl["provider"] == "simulated" and all(o["source"] == "simulated" for o in fl["offers"])
        hs = data(await c.call_tool("search_hotels", {"city": "Dubai", "min_stars": 4, "max_stars": 4}))
        assert {h["stars"] for h in hs["hotels"]} <= {4, None}
        fx = data(await c.call_tool("convert_currency", {"amount": 100, "from_currency": "USD", "to_currency": "AED"}))
        assert fx["converted"] == pytest.approx(367.25)
        guide = (await c.read_resource("city://Dubai/guide")).contents[0].text
        assert "AED" in guide and "Asia/Dubai" in guide


async def test_tool_errors_are_clean():
    async with Client(travel) as c:
        r = await c.call_tool("search_flights", {"origin": "Atlantis", "destination": "Dubai", "departure_date": SOON})
        assert r.is_error and "Atlantis" in r.content[0].text
        r = await c.call_tool("search_flights", {"origin": "Phoenix", "destination": "Dubai", "departure_date": "2001-01-01"})
        assert r.is_error and "past" in r.content[0].text


async def test_approval_workflow():
    async with Client(travel) as c:
        tid = await _traveler(c)
        req = data(await c.call_tool("request_hotel_booking", {"traveler_id": tid, "city": "Dubai",
                                                               "hotel_name": "Novotel", "check_in": SOON, "nights": 4}))
        assert req["status"] == "pending"
        pending = (await c.read_resource("travel://booking-requests/pending")).contents[0].text
        assert req["id"] in pending
        r = await c.call_tool("book_hotel", {"request_id": req["id"], "approved_by": " "})
        assert r.is_error
        booking = data(await c.call_tool("book_hotel", {"request_id": req["id"], "approved_by": "Priya", "nights": 3}))
        assert booking["nights"] == 3 and booking["approved_by"] == "Priya"
        again = await c.call_tool("book_hotel", {"request_id": req["id"], "approved_by": "Priya"})
        assert again.is_error and "already approved" in again.content[0].text


async def test_policy_rag_and_compliance():
    async with Client(policy) as c:
        res = data(await c.call_tool("search_company_policy", {"query": "business class long flight"}))
        assert res["retrieval"] == "lexical" and res["results"][0]["section"] == "Flight class"
        ok = data(await c.call_tool("check_trip_compliance", {"cabin_class": "economy", "flight_duration_hours": 3,
                                                              "international": True, "hotel_stars": 4,
                                                              "hotel_nightly_rate": 120, "rate_currency": "USD",
                                                              "departure_date": SOON}))
        assert ok["compliant"], ok
        bad = data(await c.call_tool("check_trip_compliance", {"cabin_class": "business", "flight_duration_hours": 3,
                                                               "international": True, "hotel_stars": 5}))
        assert not bad["compliant"] and len(bad["issues"]) == 2
        summary = (await c.read_resource("policy://travel-summary")).contents[0].text
        assert "12,000 INR" in summary


async def test_policy_rules_are_data(isolated):
    import json

    rules_file = isolated / "policy_rules.json"
    rules = json.loads(rules_file.read_text())
    rules["hotel"]["max_stars"] = 5
    rules_file.write_text(json.dumps(rules))
    async with Client(policy) as c:
        assert "up to 5-star" in (await c.read_resource("policy://travel-summary")).contents[0].text


async def test_work_tasks_dedupe():
    async with Client(travel) as t:
        tid = await _traveler(t)
    async with Client(work) as c:
        a = data(await c.call_tool("create_trip_task", {"title": "Get visa", "traveler_id": tid, "due_date": SOON}))
        b = data(await c.call_tool("create_trip_task", {"title": "get visa ", "traveler_id": tid}))
        assert a["id"] == b["id"] and b["duplicate"]
        done = data(await c.call_tool("update_task_status", {"task_id": a["id"], "status": "done"}))
        assert done["status"] == "done"
