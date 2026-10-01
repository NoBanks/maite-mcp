"""Streamable HTTP transport for the MAITE MCP server (MCP spec 2025-11-25).

The stdio server in server.py stays the default. This module wraps the same low-level
``mcp.server.Server`` instance in the SDK's ``StreamableHTTPSessionManager`` so a remote
client such as Alexa+, MCP Inspector or Claude Desktop can connect over HTTP.

Selected by environment, never by code change:

    MAITE_MCP_TRANSPORT=streamable-http   (anything else, or unset, means stdio)
    MAITE_MCP_HOST                        bind host, default 127.0.0.1
    MAITE_MCP_PORT                        bind port, default 18800
    MAITE_MCP_PATH                        MCP endpoint path, default /mcp
    MAITE_MCP_BEARER_TOKEN                if set, every request to the MCP endpoint must carry
                                          "Authorization: Bearer <token>". Unset means no auth
                                          (loopback development only). The value is never logged.
    MAITE_MCP_JSON_RESPONSE               "1" to answer with plain JSON instead of SSE streams
    MAITE_MCP_ALLOWED_HOSTS               comma separated Host header values for the SDK's DNS
                                          rebinding protection, for example
                                          "maite-mcp.nohumannearby.com,127.0.0.1:18800".
                                          Unset means protection is off (loopback only).

Routes:
    GET  /healthz   200 JSON: name, version, transport, whether a bearer token is configured
    POST/GET/DELETE <MAITE_MCP_PATH>   the MCP Streamable HTTP endpoint
"""

from __future__ import annotations

import contextlib
import hmac
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from . import __version__
from .server import SERVER_NAME, server

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18800  # checked free on 2026-10-01; never 1233-1236, 3000-3005, 8642, 18789-18792
DEFAULT_PATH = "/mcp"
TRANSPORT_NAME = "streamable-http"


@dataclass(frozen=True)
class HttpSettings:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    path: str = DEFAULT_PATH
    bearer_token: str | None = None
    json_response: bool = False
    allowed_hosts: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "HttpSettings":
        hosts = tuple(h.strip() for h in os.environ.get("MAITE_MCP_ALLOWED_HOSTS", "").split(",") if h.strip())
        path = os.environ.get("MAITE_MCP_PATH", DEFAULT_PATH)
        if not path.startswith("/"):
            path = "/" + path
        return cls(
            host=os.environ.get("MAITE_MCP_HOST", DEFAULT_HOST),
            port=int(os.environ.get("MAITE_MCP_PORT", str(DEFAULT_PORT))),
            path=path.rstrip("/") or DEFAULT_PATH,
            bearer_token=os.environ.get("MAITE_MCP_BEARER_TOKEN") or None,
            json_response=os.environ.get("MAITE_MCP_JSON_RESPONSE", "0") == "1",
            allowed_hosts=hosts,
        )


def transport_is_http() -> bool:
    return os.environ.get("MAITE_MCP_TRANSPORT", "stdio").strip().lower() in {TRANSPORT_NAME, "http", "streamable_http"}


class _BearerGate:
    """ASGI wrapper that rejects MCP requests without the configured bearer token.

    Constant-time comparison. The token value is read once from settings and never logged or
    echoed in error bodies.
    """

    def __init__(self, app, token: str | None):
        self._app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._token:
            await self._app(scope, receive, send)
            return
        header = ""
        for k, v in scope.get("headers", []):
            if k.lower() == b"authorization":
                header = v.decode("latin-1")
                break
        scheme, _, presented = header.partition(" ")
        ok = scheme.lower() == "bearer" and hmac.compare_digest(presented.strip(), self._token)
        if not ok:
            resp = JSONResponse(
                {"error": "invalid_token", "error_description": "A valid bearer token is required"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
            await resp(scope, receive, send)
            return
        await self._app(scope, receive, send)


class _McpAsgi:
    def __init__(self, manager: StreamableHTTPSessionManager):
        self._manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._manager.handle_request(scope, receive, send)


def build_session_manager(settings: HttpSettings) -> StreamableHTTPSessionManager:
    security = None
    if settings.allowed_hosts:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.allowed_hosts),
            allowed_origins=[f"https://{h}" for h in settings.allowed_hosts] + [f"http://{h}" for h in settings.allowed_hosts],
        )
    return StreamableHTTPSessionManager(
        app=server,
        json_response=settings.json_response,
        stateless=False,
        security_settings=security,
    )


def build_app(settings: HttpSettings | None = None) -> tuple[Starlette, StreamableHTTPSessionManager]:
    """Return (starlette_app, session_manager).

    The manager must be running (``async with manager.run():``) while requests are served. The
    returned app's lifespan does that under uvicorn; tests that drive the app in-process with
    ``httpx.ASGITransport`` (which does not run lifespan) enter ``manager.run()`` themselves.
    """
    settings = settings or HttpSettings.from_env()
    manager = build_session_manager(settings)

    async def healthz(_: Request) -> Response:
        return JSONResponse(
            {
                "name": SERVER_NAME,
                "version": __version__,
                "transport": TRANSPORT_NAME,
                "spec": "2025-11-25",
                "endpoint": settings.path,
                "auth": "bearer" if settings.bearer_token else "none",
            }
        )

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with manager.run():
            yield

    app = Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            # A Route (not a Mount) so that "/mcp" is served directly; a Mount answered "/mcp" with a
            # 307 to "/mcp/", which MCP clients do not follow for POST.
            Route(settings.path, _BearerGate(_McpAsgi(manager), settings.bearer_token), methods=["GET", "POST", "DELETE"]),
        ],
        lifespan=lifespan,
    )
    return app, manager


def run_http(settings: HttpSettings | None = None) -> None:
    """Serve the MCP server over Streamable HTTP with uvicorn (blocking)."""
    import uvicorn

    settings = settings or HttpSettings.from_env()
    app, _ = build_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
