"""Company Policy MCP server.

TOOLS     search_company_policy (RAG), check_trip_compliance (rules engine + live FX)
RESOURCES policy://travel-summary, policy://rules, policy://documents,
          policy://documents/{doc_id} (template)
PROMPTS   policy_exception_request
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from typing import Any, Dict, List, Optional

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from ..config import get_settings
from ..integrations import finance
from ..integrations.http import ProviderError
from ..models import CabinClass
from ..policy_index import PolicyIndex, load_documents, load_rules

server = MCPServer(
    "policy",
    title="Company Policy MCP Server",
    instructions="Semantic search over company policy documents and automated trip compliance checks.",
    version="2.0.0",
)

CABIN_RANK = {"economy": 0, "premium_economy": 1, "business": 2, "first": 3}


@lru_cache(maxsize=1)
def index() -> PolicyIndex:
    return PolicyIndex()


# ====================================================================== TOOLS
@server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def search_company_policy(query: str, top_k: int = 3) -> Dict[str, Any]:
    """Semantic search over company travel, hotel, expense and leave policies.

    Returns the most relevant policy sections with source document and score.
    """
    if not query.strip():
        raise ToolError("query must not be empty")
    results = index().search(query, max(1, min(top_k, 8)))
    mode = results[0]["retrieval"] if results else index().mode  # reflects a fallback to BM25
    return {"query": query, "retrieval": mode, "results": results}


@server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def check_trip_compliance(
    cabin_class: CabinClass,
    flight_duration_hours: float,
    international: bool,
    hotel_stars: Optional[int] = None,
    hotel_nightly_rate: Optional[float] = None,
    rate_currency: Optional[str] = None,
    departure_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Check a proposed flight + hotel against the policy rules.

    Hotel rates in any currency are converted to the policy currency with a live
    exchange rate. Returns violations, required approvals and the numbers used.
    """
    rules = load_rules()
    pc = rules["policy_currency"]
    issues, approvals, checks = [], [], []

    # ---- flight class
    f = rules["flight"]
    allowed = "economy"
    if flight_duration_hours >= f["business_class_min_hours"]:
        allowed = "business"
    elif flight_duration_hours >= f["premium_economy_min_hours"]:
        allowed = "premium_economy"
    ok = CABIN_RANK[cabin_class] <= CABIN_RANK[allowed]
    checks.append({"rule": "cabin_class", "requested": cabin_class, "max_allowed": allowed, "compliant": ok})
    if not ok:
        issues.append(f"{cabin_class} exceeds the {allowed} entitlement for a {flight_duration_hours:.1f}h flight")
        approvals.append(f"{f['upgrade_approver']} approval for cabin upgrade")

    # ---- hotel category
    h = rules["hotel"]
    if hotel_stars is not None:
        ok = hotel_stars <= h["max_stars"]
        checks.append({"rule": "hotel_stars", "requested": hotel_stars, "max_allowed": h["max_stars"], "compliant": ok})
        if not ok:
            issues.append(f"{hotel_stars}-star hotel exceeds the {h['max_stars']}-star cap")
            approvals.append(f"{h['over_budget_approver']} approval for hotel category")

    # ---- hotel budget with live FX
    if hotel_nightly_rate is not None:
        budget = h["nightly_budget"]["international" if international else "domestic"]
        src = (rate_currency or pc).upper()
        try:
            conv = await finance.convert(hotel_nightly_rate, src, pc)
        except ProviderError as exc:
            raise ToolError(f"Could not convert {src} to {pc}: {exc}") from exc
        ok = conv["converted"] <= budget
        checks.append(
            {
                "rule": "hotel_nightly_budget",
                "requested": f"{hotel_nightly_rate} {src}",
                "requested_in_policy_currency": conv["converted"],
                "budget": budget,
                "currency": pc,
                "fx_rate": conv["rate"],
                "compliant": ok,
            }
        )
        if not ok:
            issues.append(f"Nightly rate {conv['converted']:.0f} {pc} exceeds the {budget} {pc} budget")
            approvals.append(f"{h['over_budget_approver']} approval for over-budget hotel")

    # ---- booking window
    if departure_date:
        try:
            days_ahead = (date.fromisoformat(departure_date) - date.today()).days
        except ValueError as exc:
            raise ToolError("departure_date must be YYYY-MM-DD") from exc
        need = rules["booking"]["advance_days_recommended"]
        ok = days_ahead >= need
        checks.append({"rule": "booking_window", "days_ahead": days_ahead, "minimum": need, "compliant": ok})
        if not ok:
            issues.append(f"Booking only {days_ahead} days ahead (policy: {need})")
            approvals.append(f"{rules['booking']['short_notice_approver']} approval for short-notice booking")

    return {
        "compliant": not issues,
        "issues": issues,
        "approvals_required": sorted(set(approvals)),
        "checks": checks,
        "per_diem": {
            "amount": rules["per_diem"]["international" if international else "domestic"],
            "currency": pc,
        },
    }


# ================================================================== RESOURCES
@server.resource("policy://rules", mime_type="application/json")
def policy_rules() -> str:
    """Machine-readable policy thresholds (data/policy_rules.json)."""
    return json.dumps(load_rules(), indent=2)


@server.resource("policy://travel-summary", mime_type="text/markdown")
def travel_policy_summary() -> str:
    """Human-readable summary generated from the rules file (never stale)."""
    r = load_rules()
    pc, f, h = r["policy_currency"], r["flight"], r["hotel"]
    return "\n".join(
        [
            "Company travel summary:",
            f"- Economy for flights under {f['premium_economy_min_hours']}h; premium economy from "
            f"{f['premium_economy_min_hours']}h; business from {f['business_class_min_hours']}h "
            f"(otherwise {f['upgrade_approver']} approval).",
            f"- Hotels up to {h['max_stars']}-star.",
            f"- Nightly hotel budget: {h['nightly_budget']['international']:,} {pc} international, "
            f"{h['nightly_budget']['domestic']:,} {pc} domestic.",
            f"- Book at least {r['booking']['advance_days_recommended']} days ahead.",
            f"- Per diem: {r['per_diem']['international']:,} {pc}/day international, "
            f"{r['per_diem']['domestic']:,} {pc}/day domestic.",
            "- Valid receipts are required for reimbursement.",
        ]
    )


@server.resource("policy://documents", mime_type="application/json")
def policy_documents() -> str:
    """Catalogue of policy documents available for retrieval."""
    return json.dumps([{"doc_id": d.doc_id, "title": d.title, **d.meta} for d in load_documents()], indent=2)


@server.resource("policy://documents/{doc_id}", mime_type="text/markdown")
def policy_document(doc_id: str) -> str:
    """Full text of one policy document."""
    for d in load_documents():
        if d.doc_id == doc_id:
            return d.body
    raise ValueError(f"Unknown policy document '{doc_id}'")


# ==================================================================== PROMPTS
@server.prompt(title="Draft a policy exception request")
def policy_exception_request(traveler_name: str, exception: str, justification: str, approver: str = "Line manager") -> str:
    """Draft a short, factual approval request for an out-of-policy item."""
    return f"""
Draft a concise approval request email from {traveler_name} to their {approver}.
Exception requested: {exception}
Business justification: {justification}
Cite the relevant policy section (use search_company_policy), state the cost impact,
and offer the compliant alternative. Keep it under 150 words.
""".strip()


def main() -> None:
    """Console entry point: ``trip-policy-mcp [--transport stdio|http]``."""
    from .runner import run

    run(server, get_settings().policy_mcp_port)


if __name__ == "__main__":
    main()
