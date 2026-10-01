"""Streamable HTTP transport tests. Drive the Starlette app in-process (no sockets, no server left
running) with httpx.ASGITransport and the SDK's own streamable HTTP client."""

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from maite_mcp import http_server as H

TOKEN = "test-token-not-a-secret"


def _asgi_client(app, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=headers)


def test_settings_from_env_defaults(monkeypatch):
    for k in ("MAITE_MCP_HOST", "MAITE_MCP_PORT", "MAITE_MCP_PATH", "MAITE_MCP_BEARER_TOKEN",
              "MAITE_MCP_JSON_RESPONSE", "MAITE_MCP_ALLOWED_HOSTS", "MAITE_MCP_TRANSPORT"):
        monkeypatch.delenv(k, raising=False)
    s = H.HttpSettings.from_env()
    assert (s.host, s.port, s.path, s.bearer_token, s.json_response, s.allowed_hosts) == (
        "127.0.0.1", 18800, "/mcp", None, False, ())
    assert H.transport_is_http() is False


def test_settings_from_env_http(monkeypatch):
    monkeypatch.setenv("MAITE_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("MAITE_MCP_PORT", "18801")
    monkeypatch.setenv("MAITE_MCP_PATH", "mcp/v1/")
    monkeypatch.setenv("MAITE_MCP_BEARER_TOKEN", TOKEN)
    monkeypatch.setenv("MAITE_MCP_JSON_RESPONSE", "1")
    monkeypatch.setenv("MAITE_MCP_ALLOWED_HOSTS", "maite-mcp.example.test, 127.0.0.1:18801")
    s = H.HttpSettings.from_env()
    assert H.transport_is_http() is True
    assert s.port == 18801 and s.path == "/mcp/v1" and s.bearer_token == TOKEN and s.json_response is True
    assert s.allowed_hosts == ("maite-mcp.example.test", "127.0.0.1:18801")


@pytest.mark.asyncio
async def test_healthz_reports_auth_mode_without_leaking_token():
    app, manager = H.build_app(H.HttpSettings(bearer_token=TOKEN))
    async with manager.run():
        async with _asgi_client(app) as c:
            r = await c.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "maite-mcp" and body["transport"] == "streamable-http" and body["auth"] == "bearer"
    assert TOKEN not in r.text


@pytest.mark.asyncio
async def test_mcp_endpoint_rejects_missing_or_wrong_token():
    app, manager = H.build_app(H.HttpSettings(bearer_token=TOKEN))
    async with manager.run():
        async with _asgi_client(app) as c:
            r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            assert r.status_code == 401
            assert r.headers["www-authenticate"].startswith("Bearer")
        async with _asgi_client(app, token="wrong") as c:
            r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            assert r.status_code == 401


@pytest.mark.asyncio
async def test_initialize_and_list_tools_over_streamable_http():
    app, manager = H.build_app(H.HttpSettings(bearer_token=TOKEN))
    async with manager.run():
        async with _asgi_client(app, token=TOKEN) as http:
            async with streamable_http_client("http://testserver/mcp", http_client=http) as (read, write, get_sid):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    assert init.serverInfo.name == "maite-mcp"
                    tools = await session.list_tools()
                    names = sorted(t.name for t in tools.tools)
                    assert names == sorted(
                        ["create_goal", "log_journal_entry", "check_progress", "get_companion_response", "set_reminder"])
                    assert get_sid() is not None


@pytest.mark.asyncio
async def test_call_tool_over_http_returns_structured_error_when_backend_unconfigured(monkeypatch):
    monkeypatch.delenv("MAITE_API_BASE", raising=False)
    monkeypatch.delenv("MAITE_API_KEY", raising=False)
    app, manager = H.build_app(H.HttpSettings())  # no token: loopback dev mode
    async with manager.run():
        async with _asgi_client(app) as http:
            async with streamable_http_client("http://testserver/mcp", http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    res = await session.call_tool("check_progress", {})
                    text = res.content[0].text
                    assert "MAITE_API_BASE" in text and "Traceback" not in text
