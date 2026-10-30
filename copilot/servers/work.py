"""Work-management MCP server (persistent trip tasks in SQLite).

Swap the db calls for Jira / Linear / ServiceNow / GitHub Issues in production —
the MCP contract (tool names + schemas) stays the same.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Dict, List, Optional

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .. import db
from ..config import get_settings
from ..models import TaskStatus

server = MCPServer(
    "work",
    title="Work Management MCP Server",
    instructions="Create and track trip preparation tasks.",
    version="2.0.0",
)


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
def create_trip_task(
    title: str,
    traveler_id: Optional[str] = None,
    due_date: Optional[str] = None,
    notes: str = "",
) -> Dict[str, Any]:
    """Create a trip-preparation task (e.g. 'Apply for UAE visa') owned by the traveler."""
    if traveler_id and not db.get_traveler(traveler_id):
        raise ToolError(f"Unknown traveler {traveler_id}")
    due = None
    if due_date:
        try:
            due = date.fromisoformat(due_date)
        except ValueError as exc:
            raise ToolError("due_date must be YYYY-MM-DD") from exc
    # de-duplicate: same open title for the same traveler
    for t in db.list_tasks(traveler_id, "open"):
        if t.title.strip().lower() == title.strip().lower():
            return {**db.to_jsonable(t), "duplicate": True}  # type: ignore[dict-item]
    return db.to_jsonable(db.create_task(traveler_id, title, notes, due))  # type: ignore[return-value]


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_trip_tasks(traveler_id: Optional[str] = None, status: Optional[TaskStatus] = None) -> List[Dict[str, Any]]:
    """List trip tasks, optionally filtered by traveler and status."""
    return db.to_jsonable(db.list_tasks(traveler_id, status))  # type: ignore[return-value]


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True), meta={"audience": "host"})
def update_task_status(task_id: str, status: TaskStatus) -> Dict[str, Any]:
    """Move a task to open / in_progress / done / cancelled."""
    try:
        return db.to_jsonable(db.update_task_status(task_id, status))  # type: ignore[return-value]
    except KeyError as exc:
        raise ToolError(str(exc)) from exc


@server.resource("work://travelers/{traveler_id}/tasks", mime_type="application/json")
def traveler_tasks(traveler_id: str) -> str:
    """Open and in-progress tasks for a traveler."""
    tasks = [t for t in db.list_tasks(traveler_id) if t.status in ("open", "in_progress")]
    return json.dumps(db.to_jsonable(tasks), indent=2)


def main() -> None:
    """Console entry point: ``trip-work-mcp [--transport stdio|http]``."""
    from .runner import run

    run(server, get_settings().work_mcp_port)


if __name__ == "__main__":
    main()
