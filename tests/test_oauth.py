"""OAuth 2.1 + PKCE layer (MAITE_MCP_AUTH=oauth). In-process over httpx.ASGITransport: no sockets, no
server left running, no MAITE backend (the MAITE token check is a fake verifier)."""

import base64
import hashlib
import secrets
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from maite_mcp import http_server as H
from maite_mcp import oauth as O
from maite_mcp import server as S

PUBLIC = "http://127.0.0.1:18800"
REDIRECT = "https://alexa.amazon.com/api/skill/link/M3IHEJ1OZMSGZ3"
CLIENT_ID = "alexa-maite-test-client"
CLIENT_SECRET = "test-client-secret-not-real"
GOOD_MAITE_TOKEN = "maite-token-for-user-42"


def _settings(tmp_path, **over):
    base = dict(public_url=PUBLIC, mcp_path="/mcp", client_id=CLIENT_ID, client_secret=CLIENT_SECRET,
                client_auth_method="client_secret_post", redirect_uris=(REDIRECT, "https://pitangui.amazon.com/api/skill/link/M3IHEJ1OZMSGZ3"),
                db_path=str(tmp_path / "oauth.sqlite3"), link_key=O.new_link_key(), maite_api_base="https://maite.example.test")
    base.update(over)
    return O.OAuthSettings(**base)


async def _fake_verify(token: str):
    return "42" if token == GOOD_MAITE_TOKEN else None


@pytest.fixture
def stack(tmp_path):
    settings = _settings(tmp_path)
    provider = O.MaiteOAuthProvider(settings, verifier=_fake_verify)
    app, manager = H.build_app(H.HttpSettings(auth_mode="oauth"), oauth_provider=provider)
    yield app, manager, provider
    S.set_credential_resolver(None)


def _client(app, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC, headers=headers)


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


async def _authorize_and_link(c, challenge, state="st-123", resource=PUBLIC + "/mcp", maite_token=GOOD_MAITE_TOKEN):
    r = await c.get("/authorize", params={
        "client_id": CLIENT_ID, "redirect_uri": REDIRECT, "response_type": "code", "code_challenge": challenge,
        "code_challenge_method": "S256", "state": state, "scope": "maite", "resource": resource})
    assert r.status_code == 302, r.text
    link = r.headers["location"]
    assert link.startswith(PUBLIC + "/link?req=")
    rid = parse_qs(urlparse(link).query)["req"][0]
    page = await c.get("/link", params={"req": rid})
    assert page.status_code == 200 and "MAITE access token" in page.text
    r = await c.post("/link", data={"req": rid, "maite_token": maite_token})
    return r


async def _token(c, **form):
    base = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
    base.update(form)
    return await c.post("/token", data=base)


# ---------------------------------------------------------------- metadata

@pytest.mark.asyncio
async def test_metadata_documents(stack):
    app, manager, _ = stack
    async with manager.run(), _client(app) as c:
        asm = (await c.get("/.well-known/oauth-authorization-server")).json()
        assert asm["issuer"].rstrip("/") == PUBLIC
        assert asm["authorization_endpoint"] == PUBLIC + "/authorize" and asm["token_endpoint"] == PUBLIC + "/token"
        assert "S256" in asm["code_challenge_methods_supported"]           # Alexa+ blocks deploy without this
        assert "registration_endpoint" not in asm                           # no Dynamic Client Registration
        assert set(asm["grant_types_supported"]) == {"authorization_code", "refresh_token"}
        for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
            prm = (await c.get(path)).json()
            assert prm["resource"] == PUBLIC + "/mcp" and prm["authorization_servers"][0].rstrip("/") == PUBLIC
            assert prm["scopes_supported"] == ["maite"]
        health = (await c.get("/healthz")).json()
        assert health["auth"] == "oauth"


@pytest.mark.asyncio
async def test_401_shape_matches_alexa_without_www_authenticate(stack):
    app, manager, _ = stack
    async with manager.run(), _client(app) as c:
        r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert r.status_code == 401
        assert r.json() == {"error": "unauthorized", "message": "Access token required to use this tool."}
        assert "www-authenticate" not in {k.lower() for k in r.headers}
        r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}, headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_401_header_opt_in_for_spec_strict_clients(tmp_path):
    settings = _settings(tmp_path, www_authenticate_on_401=True)
    provider = O.MaiteOAuthProvider(settings, verifier=_fake_verify)
    app, manager = H.build_app(H.HttpSettings(auth_mode="oauth"), oauth_provider=provider)
    try:
        async with manager.run(), _client(app) as c:
            r = await c.post("/mcp", json={})
            assert r.status_code == 401
            assert 'resource_metadata="' + PUBLIC + '/.well-known/oauth-protected-resource"' in r.headers["www-authenticate"]
    finally:
        S.set_credential_resolver(None)


# ---------------------------------------------------------------- the flow

@pytest.mark.asyncio
async def test_pkce_happy_path_end_to_end(stack, monkeypatch):
    app, manager, provider = stack
    verifier, challenge = _pkce()
    captured = {}

    class FakeMAITE:
        def __init__(self, api_base=None, api_key=None, **_):
            captured["api_key"] = api_key

        async def list_goals(self):
            return [{"id": 1, "title": "Run", "progress": 80, "status": "in_progress"}]

        async def list_checkins(self):
            return []

        async def get_user(self):
            return {"id": 42, "timezone": "America/Los_Angeles"}

    monkeypatch.setenv("MAITE_API_BASE", "https://maite.example.test")
    monkeypatch.delenv("MAITE_API_KEY", raising=False)
    monkeypatch.setattr(S, "MAITEClient", FakeMAITE)

    async with manager.run(), _client(app) as c:
        r = await _authorize_and_link(c, challenge)
        assert r.status_code == 302
        loc = urlparse(r.headers["location"])
        assert loc.scheme + "://" + loc.netloc + loc.path == REDIRECT
        q = parse_qs(loc.query)
        assert q["state"] == ["st-123"]
        code = q["code"][0]

        r = await _token(c, grant_type="authorization_code", code=code, redirect_uri=REDIRECT, code_verifier=verifier,
                         resource=PUBLIC + "/mcp")
        assert r.status_code == 200, r.text
        tok = r.json()
        assert tok["token_type"] == "Bearer" and tok["expires_in"] == 3600 and tok["refresh_token"]  # Alexa+: refresh mandatory
        assert tok["scope"] == "maite"

        # Issued tokens are stored hashed only.
        rows = provider.store._x("SELECT hash FROM tokens").fetchall()
        assert all(h[0] != tok["access_token"] and h[0] != tok["refresh_token"] for h in rows)
        assert GOOD_MAITE_TOKEN.encode() not in provider.store._x("SELECT enc_token FROM links").fetchone()[0]

        async with _client(app, token=tok["access_token"]) as http:
            async with streamable_http_client(PUBLIC + "/mcp", http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    assert init.serverInfo.name == "maite-mcp"
                    names = {t.name for t in (await session.list_tools()).tools}
                    assert "check_in" in names
                    res = await session.call_tool("check_in", {})
                    assert "Run" in res.content[0].text
        # The tool acted as the LINKED user, with their own MAITE credential, not a shared key.
        assert captured["api_key"] == GOOD_MAITE_TOKEN


@pytest.mark.asyncio
async def test_wrong_verifier_rejected(stack):
    app, manager, _ = stack
    _, challenge = _pkce()
    async with manager.run(), _client(app) as c:
        code = parse_qs(urlparse((await _authorize_and_link(c, challenge)).headers["location"]).query)["code"][0]
        r = await _token(c, grant_type="authorization_code", code=code, redirect_uri=REDIRECT, code_verifier="not-the-verifier")
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_reused_code_rejected(stack):
    app, manager, _ = stack
    verifier, challenge = _pkce()
    async with manager.run(), _client(app) as c:
        code = parse_qs(urlparse((await _authorize_and_link(c, challenge)).headers["location"]).query)["code"][0]
        first = await _token(c, grant_type="authorization_code", code=code, redirect_uri=REDIRECT, code_verifier=verifier)
        assert first.status_code == 200
        again = await _token(c, grant_type="authorization_code", code=code, redirect_uri=REDIRECT, code_verifier=verifier)
        assert again.status_code == 400 and again.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_wrong_client_secret_and_unregistered_redirect(stack):
    app, manager, _ = stack
    _, challenge = _pkce()
    async with manager.run(), _client(app) as c:
        r = await c.get("/authorize", params={"client_id": CLIENT_ID, "redirect_uri": "https://evil.example/cb", "response_type": "code",
                                              "code_challenge": challenge, "code_challenge_method": "S256"})
        assert r.status_code == 400  # no redirect to an unregistered URI
        code = parse_qs(urlparse((await _authorize_and_link(c, challenge)).headers["location"]).query)["code"][0]
        r = await c.post("/token", data={"client_id": CLIENT_ID, "client_secret": "wrong", "grant_type": "authorization_code",
                                         "code": code, "redirect_uri": REDIRECT, "code_verifier": "x"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_resource_mismatch_rejected_at_authorize(stack):
    app, manager, _ = stack
    _, challenge = _pkce()
    async with manager.run(), _client(app) as c:
        r = await c.get("/authorize", params={"client_id": CLIENT_ID, "redirect_uri": REDIRECT, "response_type": "code",
                                              "code_challenge": challenge, "code_challenge_method": "S256", "state": "s",
                                              "resource": "https://other-server.example/mcp"})
        assert r.status_code == 302
        q = parse_qs(urlparse(r.headers["location"]).query)
        assert q["error"] == ["invalid_request"] and q["state"] == ["s"] and "code" not in q


@pytest.mark.asyncio
async def test_link_rejects_bad_maite_token_and_expired_request(stack):
    app, manager, provider = stack
    _, challenge = _pkce()
    async with manager.run(), _client(app) as c:
        r = await _authorize_and_link(c, challenge, maite_token="bogus")
        assert r.status_code == 200 and "did not accept" in r.text
        assert provider.store._x("SELECT COUNT(*) FROM links").fetchone()[0] == 0
        r = await c.get("/link", params={"req": "does-not-exist"})
        assert r.status_code == 400
        r = await c.post("/link", data={"req": "does-not-exist", "maite_token": GOOD_MAITE_TOKEN})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_expired_access_token_rejected(stack, monkeypatch):
    app, manager, provider = stack
    verifier, challenge = _pkce()
    async with manager.run(), _client(app) as c:
        code = parse_qs(urlparse((await _authorize_and_link(c, challenge)).headers["location"]).query)["code"][0]
        tok = (await _token(c, grant_type="authorization_code", code=code, redirect_uri=REDIRECT, code_verifier=verifier)).json()
        later = time.time() + O.ACCESS_TTL_S + 5
        monkeypatch.setattr(O, "_now", lambda: int(later))
        monkeypatch.setattr(time, "time", lambda: later)
        async with _client(app, token=tok["access_token"]) as http:
            r = await http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            assert r.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rotates_and_old_refresh_dies(stack):
    app, manager, _ = stack
    verifier, challenge = _pkce()
    async with manager.run(), _client(app) as c:
        code = parse_qs(urlparse((await _authorize_and_link(c, challenge)).headers["location"]).query)["code"][0]
        t1 = (await _token(c, grant_type="authorization_code", code=code, redirect_uri=REDIRECT, code_verifier=verifier)).json()
        r = await _token(c, grant_type="refresh_token", refresh_token=t1["refresh_token"])
        assert r.status_code == 200, r.text
        t2 = r.json()
        assert t2["refresh_token"] and t2["refresh_token"] != t1["refresh_token"]
        assert t2["access_token"] != t1["access_token"]
        again = await _token(c, grant_type="refresh_token", refresh_token=t1["refresh_token"])
        assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
        async with _client(app, token=t2["access_token"]) as http:
            r = await http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
            assert r.status_code != 401


# ---------------------------------------------------------------- settings and helpers

def test_canonical_resource():
    assert O.canonical("HTTPS://Maite-MCP.Example.com/mcp/") == "https://maite-mcp.example.com/mcp"
    with pytest.raises(ValueError):
        O.canonical("maite-mcp.example.com/mcp")


def test_settings_from_env_requires_the_oauth_variables(monkeypatch, tmp_path):
    for k in ("MAITE_MCP_PUBLIC_URL", "MAITE_MCP_OAUTH_CLIENT_ID", "MAITE_MCP_OAUTH_CLIENT_SECRET", "MAITE_MCP_OAUTH_REDIRECT_URIS",
              "MAITE_MCP_LINK_KEY", "MAITE_API_BASE", "MAITE_MCP_OAUTH_DB", "MAITE_MCP_OAUTH_CLIENT_AUTH_METHOD"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValueError) as e:
        O.OAuthSettings.from_env("/mcp")
    assert "MAITE_MCP_PUBLIC_URL" in str(e.value)
    monkeypatch.setenv("MAITE_MCP_PUBLIC_URL", "https://maite-mcp.example.test/")
    monkeypatch.setenv("MAITE_MCP_OAUTH_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("MAITE_MCP_OAUTH_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("MAITE_MCP_OAUTH_REDIRECT_URIS", REDIRECT + " , https://layla.amazon.com/api/skill/link/X")
    monkeypatch.setenv("MAITE_MCP_LINK_KEY", O.new_link_key())
    monkeypatch.setenv("MAITE_API_BASE", "https://maite.example.test/")
    monkeypatch.setenv("MAITE_MCP_OAUTH_DB", str(tmp_path / "o.sqlite3"))
    s = O.OAuthSettings.from_env("/mcp")
    assert s.resource == "https://maite-mcp.example.test/mcp" and len(s.redirect_uris) == 2
    assert s.client_auth_method == "client_secret_post" and s.www_authenticate_on_401 is False


def test_bearer_mode_is_unchanged_default(monkeypatch):
    monkeypatch.delenv("MAITE_MCP_AUTH", raising=False)
    assert H.HttpSettings.from_env().auth_mode == "bearer"
    assert S._credential_resolver is None
