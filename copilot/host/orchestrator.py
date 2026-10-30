"""The AI host: MCP context + LangChain agent + human approval gate.

    async with CopilotHost() as host:
        async for event in host.plan_trip(request):
            ...
        for request in host.proposals:
            await host.approve_booking(request["id"], approved_by="Jane (manager)")

Security boundary (same idea as the original project, now data-driven):
  * Tools whose MCP ``_meta`` says ``audience=host`` or ``requires_approval``
    are never handed to the model (decided by the server, not a hard-coded name).
  * The model can only call ``travel_request_hotel_booking``, which stores a
    *pending* request. A human approves or rejects it (from any session); only
    then does the host call ``travel.book_hotel``.
"""

from __future__ import annotations

import json
import logging
import warnings
from dataclasses import dataclass
from datetime import date
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

from langchain_core.tools import BaseTool

from ..config import Settings, get_settings
from .connections import SERVER_NAMES, build_client_group

warnings.filterwarnings("ignore", message=".*langchain.mcp.*beta.*")
from langchain.mcp import MCPAdapter  # noqa: E402

log = logging.getLogger("copilot.host")


# ============================================================== data classes
@dataclass
class TripRequest:
    traveler_id: str
    origin: str
    destination: str
    start_date: date
    end_date: date
    purpose: str = "client meetings"
    instructions: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.start_date, str):
            self.start_date = date.fromisoformat(self.start_date)
        if isinstance(self.end_date, str):
            self.end_date = date.fromisoformat(self.end_date)
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        if self.origin.strip().lower() == self.destination.strip().lower():
            raise ValueError("origin and destination must differ")

    @property
    def nights(self) -> int:
        return (self.end_date - self.start_date).days


@dataclass
class TripContext:
    traveler: Dict[str, Any]
    city_guide: str
    policy_summary: str
    open_tasks: List[Dict[str, Any]]
    planning_prompt: str


@dataclass
class AgentEvent:
    kind: str  # context | tool_call | tool_result | approval_request | final | error
    title: str
    data: Any = None


class ToolCallError(RuntimeError):
    pass


# ================================================================== helpers
def unwrap(result: Any) -> Any:
    """MCP CallToolResult / structured content -> plain Python data."""
    sc = getattr(result, "structured_content", None)
    if sc is None and isinstance(result, dict):
        sc = result.get("structured_content", result)
    if isinstance(sc, dict):
        return sc["result"] if set(sc) == {"result"} else sc
    content = getattr(result, "content", None) or []
    texts = [getattr(c, "text", "") for c in content]
    joined = "\n".join(t for t in texts if t)
    try:
        return json.loads(joined)
    except (TypeError, ValueError):
        return joined


def _message_text(msg: Any) -> str:
    text = getattr(msg, "text", None)
    if isinstance(text, str):  # langchain-core >= 1.0: property (str subclass)
        if text:
            return text
    elif callable(text):
        text = text()
        if text:
            return text
    content = getattr(msg, "content", "")
    if isinstance(content, list):
        return "\n".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
    return str(content)


def _tool_meta(tool: BaseTool) -> Dict[str, Any]:
    return ((tool.metadata or {}).get("mcp", {}).get("tool", {}) or {}).get("_meta") or {}


def is_agent_safe(tool: BaseTool) -> bool:
    meta = _tool_meta(tool)
    return meta.get("audience") != "host" and not meta.get("requires_approval")


def make_model(settings: Settings):  # noqa: ANN201
    from langchain.chat_models import init_chat_model

    kwargs: Dict[str, Any] = {}
    provider = settings.llm_model.split(":", 1)[0] if ":" in settings.llm_model else "openai"
    if settings.llm_temperature is not None:
        kwargs["temperature"] = settings.llm_temperature
    if provider == "openai" and settings.llm_reasoning_effort:
        kwargs["reasoning_effort"] = settings.llm_reasoning_effort
    return init_chat_model(settings.llm_model, **kwargs)


# ===================================================================== host
class CopilotHost:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        servers: Sequence[str] = SERVER_NAMES,
        model: Any = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.server_names = tuple(servers)
        self._model = model
        self._adapter: Optional[MCPAdapter] = None
        self.proposals: List[Dict[str, Any]] = []
        self.tool_results: Dict[str, List[Any]] = {}

    # ------------------------------------------------------- connection
    async def __aenter__(self) -> "CopilotHost":
        group = build_client_group(self.settings)
        if set(self.server_names) != set(SERVER_NAMES):
            from fastmcp.client.group import ClientGroup

            group = ClientGroup({n: c for n, c in group.clients.items() if n in self.server_names})
        self._adapter = MCPAdapter(group)
        await self._adapter.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._adapter is not None:
            await self._adapter.__aexit__(*exc)
            self._adapter = None

    def client(self, server: str):  # noqa: ANN201
        if self._adapter is None:
            raise RuntimeError("Use 'async with CopilotHost() as host:' first")
        return self._adapter.client.clients[server]

    # --------------------------------------------------- MCP primitives
    async def read_text(self, server: str, uri: str) -> str:
        contents = await self.client(server).read_resource(uri)
        return "\n".join(getattr(c, "text", "") for c in contents)

    async def read_json(self, server: str, uri: str) -> Any:
        return json.loads(await self.read_text(server, uri))

    async def get_prompt_text(self, server: str, name: str, arguments: Dict[str, Any]) -> str:
        result = await self.client(server).get_prompt(name, {k: str(v) for k, v in arguments.items()})
        return "\n".join(getattr(m.content, "text", "") for m in result.messages)

    async def call(self, server: str, tool: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        result = await self.client(server).call_tool(tool, arguments or {}, raise_on_error=False)
        if result.is_error:
            raise ToolCallError(unwrap(result) if result.content else f"{tool} failed")
        return unwrap(result)

    async def inspect(self) -> Dict[str, Any]:
        """Everything each server exposes — used by the UI's MCP Inspector."""
        out: Dict[str, Any] = {}
        for name in self.server_names:
            c = self.client(name)
            tools = await c.list_tools()
            out[name] = {
                "tools": [
                    {
                        "name": t.name,
                        "description": t.description,
                        "input_schema": t.input_schema,
                        "annotations": t.annotations.model_dump(exclude_none=True) if t.annotations else {},
                        "meta": t.meta or {},
                    }
                    for t in tools
                ],
                "resources": [{"uri": str(r.uri), "description": r.description} for r in await c.list_resources()],
                "templates": [
                    {"uri_template": t.uri_template, "description": t.description}
                    for t in await c.list_resource_templates()
                ],
                "prompts": [
                    {"name": p.name, "description": p.description, "arguments": [a.name for a in (p.arguments or [])]}
                    for p in await c.list_prompts()
                ],
            }
        return out

    # -------------------------------------------------------- context
    async def load_context(self, req: TripRequest) -> TripContext:
        import asyncio

        traveler_raw, guide, policy, tasks = await asyncio.gather(
            self.read_text("travel", f"travel://travelers/{req.traveler_id}/profile"),
            self.read_text("travel", f"city://{req.destination}/guide"),
            self.read_text("policy", "policy://travel-summary"),
            self.read_text("work", f"work://travelers/{req.traveler_id}/tasks"),
        )
        traveler = json.loads(traveler_raw)
        prompt = await self.get_prompt_text(
            "travel",
            "plan_business_trip",
            {
                "traveler_name": traveler["name"],
                "origin": req.origin,
                "destination": req.destination,
                "start_date": req.start_date.isoformat(),
                "end_date": req.end_date.isoformat(),
                "purpose": req.purpose,
            },
        )
        return TripContext(traveler, guide, policy, json.loads(tasks or "[]"), prompt)

    # --------------------------------------------------------- tools
    async def agent_tools(self) -> List[BaseTool]:
        assert self._adapter is not None
        discovered = await self._adapter.list_tools()
        return [t for t in discovered if is_agent_safe(t)]

    def system_prompt(self, req: TripRequest, ctx: TripContext) -> str:
        t = ctx.traveler
        return f"""
You are an enterprise business-trip copilot. Today is {date.today().isoformat()}.

TRAVELER (MCP resource travel://travelers/{req.traveler_id}/profile)
- id: {req.traveler_id}   name: {t['name']}   home city: {t['home_city']}   home currency: {t['home_currency']}
- preferences: {json.dumps(t['preferences'])}

DESTINATION BRIEF (MCP resource city://{req.destination}/guide)
{ctx.city_guide}

COMPANY POLICY SUMMARY (MCP resource policy://travel-summary)
{ctx.policy_summary}

OPEN TASKS ALREADY TRACKED (avoid duplicates)
{json.dumps([x['title'] for x in ctx.open_tasks]) if ctx.open_tasks else 'none'}

PLANNING INSTRUCTIONS (MCP prompt plan_business_trip)
{ctx.planning_prompt}

RULES
- Always pass traveler_id="{req.traveler_id}" to work_create_trip_task and travel_request_hotel_booking.
- Use tools for facts; never invent prices, weather or policy text.
- Preferences never override policy: when they conflict, follow policy and say so.
- You cannot book anything. travel_request_hotel_booking only files a pending request for a human to approve.
- Format the final answer in Markdown with short sections and tables where useful.
{('ADDITIONAL INSTRUCTIONS FROM TRAVELER: ' + req.instructions) if req.instructions else ''}
""".strip()

    # ---------------------------------------------------------- agent
    async def plan_trip(self, req: TripRequest) -> AsyncIterator[AgentEvent]:
        from langchain.agents import create_agent

        self.proposals, self.tool_results = [], {}
        try:
            ctx = await self.load_context(req)
        except Exception as exc:  # noqa: BLE001
            yield AgentEvent("error", "Could not load MCP context", str(exc))
            return
        yield AgentEvent(
            "context",
            "Loaded MCP resources + prompt",
            {"traveler": ctx.traveler["name"], "city_guide": ctx.city_guide, "policy": ctx.policy_summary, "prompt": ctx.planning_prompt},
        )

        tools = await self.agent_tools()
        yield AgentEvent("context", f"{len(tools)} tools available to the agent", [t.name for t in tools])

        model = self._model or make_model(self.settings)
        agent = create_agent(model=model, tools=tools, system_prompt=self.system_prompt(req, ctx))
        question = (
            f"Plan my business trip from {req.origin} to {req.destination}, "
            f"{req.start_date} to {req.end_date}, purpose: {req.purpose}."
        )
        final = ""
        try:
            async for update in agent.astream(
                {"messages": [{"role": "user", "content": question}]},
                stream_mode="updates",
                config={"recursion_limit": self.settings.agent_recursion_limit},
            ):
                for _node, state in (update or {}).items():
                    for msg in (state or {}).get("messages", []) if isinstance(state, dict) else []:
                        kind = type(msg).__name__
                        if kind == "AIMessage":
                            for call in getattr(msg, "tool_calls", None) or []:
                                yield AgentEvent("tool_call", call["name"], call.get("args", {}))
                            if not getattr(msg, "tool_calls", None):
                                final = _message_text(msg)
                        elif kind == "ToolMessage":
                            artifact = getattr(msg, "artifact", None)
                            data = unwrap(artifact) if artifact else _message_text(msg)
                            if isinstance(data, str):
                                try:
                                    data = json.loads(data)
                                except ValueError:
                                    pass
                            is_error = getattr(msg, "status", "") == "error"
                            if not is_error:
                                self.tool_results.setdefault(msg.name, []).append(data)
                            yield AgentEvent("tool_result", msg.name, {"result": data, "is_error": is_error})
                            if msg.name == "travel_request_hotel_booking" and not is_error and isinstance(data, dict):
                                if not any(p.get("id") == data.get("id") for p in self.proposals):
                                    self.proposals.append(data)
                                yield AgentEvent("approval_request", "Booking awaiting approval", data)
        except Exception as exc:  # noqa: BLE001
            log.exception("Agent run failed")
            yield AgentEvent("error", "Agent run failed", f"{type(exc).__name__}: {exc}")
            return
        yield AgentEvent("final", "Trip plan", final)

    # --------------------------------------------------- approval gate
    async def pending_requests(self) -> List[Dict[str, Any]]:
        return await self.read_json("travel", "travel://booking-requests/pending")

    async def approve_booking(
        self, request_id: str, approved_by: str, check_in: Optional[str] = None, nights: Optional[int] = None
    ) -> Dict[str, Any]:
        """Execute the side-effecting MCP tool — only ever called from a human action."""
        if not approved_by.strip():
            raise ValueError("approved_by is required")
        args: Dict[str, Any] = {"request_id": request_id, "approved_by": approved_by.strip()}
        if check_in:
            args["check_in"] = check_in
        if nights:
            args["nights"] = int(nights)
        return await self.call("travel", "book_hotel", args)

    async def reject_booking(self, request_id: str, rejected_by: str) -> Dict[str, Any]:
        return await self.call("travel", "reject_booking_request", {"request_id": request_id, "rejected_by": rejected_by})
