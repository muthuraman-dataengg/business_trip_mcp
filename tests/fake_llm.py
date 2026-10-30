"""A deterministic tool-calling chat model for offline tests and demos."""

from __future__ import annotations

import json
import uuid
from typing import Any, List, Optional

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


def trip_script(traveler_id: str, origin: str, destination: str, start: str, end: str) -> List[Any]:
    """Tool-call rounds a good model would make for a trip, then a final answer."""
    nights = 4
    return [
        [
            ("travel_get_live_weather", {"city": destination}),
            ("travel_get_trip_forecast", {"city": destination, "start_date": start, "end_date": end}),
            ("travel_get_public_holidays", {"city": destination, "start_date": start, "end_date": end}),
        ],
        [
            ("travel_search_flights", {"origin": origin, "destination": destination, "departure_date": start}),
            ("travel_search_flights", {"origin": destination, "destination": origin, "departure_date": end}),
            ("travel_search_hotels", {"city": destination, "min_stars": 3, "max_stars": 4}),
            ("policy_search_company_policy", {"query": "flight class for long-haul flights and hotel budget"}),
        ],
        [
            (
                "policy_check_trip_compliance",
                {"cabin_class": "economy", "flight_duration_hours": 17.5, "international": True,
                 "hotel_stars": 4, "hotel_nightly_rate": 185, "rate_currency": "USD", "departure_date": start},
            ),
            ("work_create_trip_task", {"title": f"Apply for {destination} visa", "traveler_id": traveler_id, "due_date": start}),
        ],
        [
            (
                "travel_request_hotel_booking",
                {"traveler_id": traveler_id, "hotel_name": "Novotel World Trade Centre", "city": destination, "check_in": start, "nights": nights,
                 "stars": 4, "nightly_rate": 185, "currency": "USD", "reason": "4-star, within policy, near venue"},
            )
        ],
        "## Trip plan\n\nEverything checked. Hotel **awaiting your approval**.",
    ]


class ScriptedToolModel(BaseChatModel):
    script: List[Any]

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedToolModel":  # noqa: D401
        return self

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        rounds_done = sum(1 for m in messages if isinstance(m, AIMessage))
        step = self.script[min(rounds_done, len(self.script) - 1)]
        if isinstance(step, str):
            tool_results = [m for m in messages if isinstance(m, ToolMessage)]
            msg = AIMessage(content=step + f"\n\n_(based on {len(tool_results)} tool results)_")
        else:
            msg = AIMessage(
                content="",
                tool_calls=[{"name": n, "args": a, "id": f"call_{uuid.uuid4().hex[:8]}", "type": "tool_call"} for n, a in step],
            )
        return ChatResult(generations=[ChatGeneration(message=msg)])


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(trip_script("TRV-1", "A", "B", "2026-11-01", "2026-11-05")[0], indent=2))
