"""Run any copilot MCP server over STDIO or Streamable HTTP.

    python -m copilot.servers.travel                   # stdio (launched by the host)
    python -m copilot.servers.travel --transport http  # HTTP on TRAVEL_MCP_PORT

HTTP mode adds production basics around the MCP ASGI app:
  * ``GET /healthz`` liveness endpoint
  * optional bearer-token auth (``MCP_API_KEY``)
  * DNS-rebinding protection with an allow-list of Host headers
  * stateless + JSON responses so it scales horizontally behind a load balancer
"""

from __future__ import annotations

import argparse
import hmac
import logging
from typing import Optional

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..config import get_settings
from ..config import secret


class BearerAuthMiddleware:
    """Minimal ASGI middleware: reject /mcp requests without the shared key."""

    def __init__(self, app, api_key: str):  # noqa: ANN001
        self.app = app
        self.api_key = api_key.encode()

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] == "http" and scope.get("path", "").startswith("/mcp"):
            headers = dict(scope.get("headers") or [])
            supplied = headers.get(b"authorization", b"")
            expected = b"Bearer " + self.api_key
            if not hmac.compare_digest(supplied, expected):
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_http_app(server: MCPServer, host: str):  # noqa: ANN201
    settings = get_settings()

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:  # pragma: no cover - trivial
        return JSONResponse({"status": "ok", "server": server.name})

    allowed = [h.strip() for h in settings.mcp_allowed_hosts.split(",") if h.strip()]
    security = None
    if allowed:
        security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=allowed)

    app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=security,
        host=host,
    )
    if settings.mcp_api_key:
        return BearerAuthMiddleware(app, secret(settings.mcp_api_key))
    return app


def run(server: MCPServer, default_port: int, argv: Optional[list] = None) -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=f"Run {server.name}")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default=settings.mcp_host)
    parser.add_argument("--port", type=int, default=default_port)
    args = parser.parse_args(argv)

    if args.transport == "stdio":
        # stdout is the protocol channel — logs must go to stderr only.
        logging.basicConfig(level=settings.log_level)
        server.run()
        return

    import uvicorn

    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(build_http_app(server, args.host), host=args.host, port=args.port, log_level=settings.log_level.lower())
