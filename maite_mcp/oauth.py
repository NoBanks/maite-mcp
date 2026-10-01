"""OAuth 2.1 authorization server + account linking for the Alexa+ add-on surface.

Enabled with MAITE_MCP_AUTH=oauth (default stays "bearer", see http_server.py, so nothing changes
for existing deployments). Hand-written on purpose: this is an auth boundary, not boilerplate.

What Alexa+ demands (developer.amazon.com/docs/alexaplus/add-ons/mcp-toolkit-account-linking.html and
developer.amazon.com/docs/alexaplus/add-ons/mcp-toolkit-quickstart.html, read 2026-10-01):
  * OAuth 2.1 authorization code grant only, PKCE S256 required, deploy is blocked unless the
    authorization server metadata advertises code_challenge_methods_supported containing S256.
  * Protected Resource Metadata at https://<server>/.well-known/oauth-protected-resource whose
    "resource" matches the add-on manifest exactly; only the first authorization server is used.
  * Static client registration (client id + secret); Dynamic Client Registration is not supported.
  * Several redirect URIs, all registered, "use exactly the value Alexa passes".
  * Every token response must carry a refresh token.
  * "resource" (RFC 8707) in authorization and token requests, not in refresh requests.
  * 401 for unauthenticated requests WITHOUT a WWW-Authenticate header ("Not Supported Yet"), body
    {"error": "unauthorized", "message": "Access token required to use this tool."}.

What the MCP spec demands (modelcontextprotocol.io/specification/2025-11-25/basic/authorization):
  * PRM (RFC 9728) MUST be served; discovery MUST be via WWW-Authenticate resource_metadata OR the
    well-known URI. Serving the well-known URI alone is therefore spec-compliant, which is how the
    Alexa+ "no WWW-Authenticate" rule is honoured without breaking spec-following clients. Set
    MAITE_MCP_401_WWW_AUTHENTICATE=1 to add the header anyway for strict clients (Alexa+ says it
    does not support that yet, so leave it off for the add-on).
  * Tokens MUST be audience-bound: access tokens here carry the canonical resource and the bearer
    verifier rejects any other audience.
  * Refresh tokens are rotated on every use.

Account linking (how an Alexa user becomes a MAITE user):
  MAITE (server/auth.ts, server/mobile-auth.ts) authenticates API clients with a bearer token that
  its mobileAuthMiddleware verifies on every request; the web app uses a Google sign-in session
  cookie. This module reuses that bearer route: the /link page asks for the user's MAITE access
  token and proves it against GET /api/user on the MAITE backend before linking. No password is ever
  seen or stored. The MAITE token must be usable later on the user's behalf, so it is stored
  ENCRYPTED (Fernet, key in MAITE_MCP_LINK_KEY), while every token this server issues (codes,
  access, refresh) is stored only as a SHA-256 hash.

The SDK (mcp 1.27.0) supplies the HTTP handlers, metadata documents, PKCE verification, client
authentication and the bearer middleware. This module supplies the provider behind them, the SQLite
store, the link page and the Alexa-shaped 401 gate.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from pydantic import AnyHttpUrl, AnyUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.routes import create_auth_routes, create_protected_resource_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken, ProtectedResourceMetadata

SCOPE = "maite"
ACCESS_TTL_S = 3600            # Alexa+ sample shows expires_in 3600; short lived per OAuth 2.1 7.1
REFRESH_TTL_S = 90 * 86400     # matches MAITE's own mobile token lifetime
CODE_TTL_S = 300               # RFC 6749 10.5: short authorization code lifetime
PENDING_TTL_S = 600
LINK_RATE_LIMIT = 10           # POST /link attempts per IP per minute
AUTH_METHODS = {"none", "client_secret_post", "client_secret_basic"}


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> int:
    return int(time.time())


def canonical(url: str) -> str:
    """RFC 8707 canonical form: lowercase scheme and host, no fragment, no trailing slash."""
    p = urlparse(url.strip())
    if not p.scheme or not p.netloc or p.fragment:
        raise ValueError("resource must be an absolute URI without a fragment")
    path = p.path.rstrip("/")
    return f"{p.scheme.lower()}://{p.netloc.lower()}{path}"


@dataclass(frozen=True)
class OAuthSettings:
    public_url: str                       # issuer, e.g. https://maite-mcp.example.com (no path)
    mcp_path: str                         # e.g. /mcp; resource = public_url + mcp_path
    client_id: str
    client_secret: str | None
    client_auth_method: str               # none | client_secret_post | client_secret_basic
    redirect_uris: tuple[str, ...]
    db_path: str
    link_key: str                         # Fernet key, base64, 32 bytes
    maite_api_base: str                   # where GET /api/user is proved
    www_authenticate_on_401: bool = False

    @property
    def resource(self) -> str:
        return canonical(self.public_url.rstrip("/") + self.mcp_path)

    @classmethod
    def from_env(cls, mcp_path: str) -> "OAuthSettings":
        env = os.environ.get
        missing = [k for k in ("MAITE_MCP_PUBLIC_URL", "MAITE_MCP_OAUTH_CLIENT_ID", "MAITE_MCP_OAUTH_REDIRECT_URIS",
                               "MAITE_MCP_LINK_KEY", "MAITE_API_BASE") if not env(k)]
        if missing:
            raise ValueError("MAITE_MCP_AUTH=oauth needs these environment variables: " + ", ".join(missing))
        method = env("MAITE_MCP_OAUTH_CLIENT_AUTH_METHOD", "client_secret_post").strip()
        if method not in AUTH_METHODS:
            raise ValueError("MAITE_MCP_OAUTH_CLIENT_AUTH_METHOD must be one of " + ", ".join(sorted(AUTH_METHODS)))
        secret = env("MAITE_MCP_OAUTH_CLIENT_SECRET") or None
        if method != "none" and not secret:
            raise ValueError("MAITE_MCP_OAUTH_CLIENT_SECRET is required unless MAITE_MCP_OAUTH_CLIENT_AUTH_METHOD=none")
        uris = tuple(u.strip() for u in env("MAITE_MCP_OAUTH_REDIRECT_URIS", "").split(",") if u.strip())
        return cls(
            public_url=env("MAITE_MCP_PUBLIC_URL").rstrip("/"),
            mcp_path=mcp_path,
            client_id=env("MAITE_MCP_OAUTH_CLIENT_ID"),
            client_secret=secret,
            client_auth_method=method,
            redirect_uris=uris,
            db_path=env("MAITE_MCP_OAUTH_DB", str(Path.home() / ".maite-mcp" / "oauth.sqlite3")),
            link_key=env("MAITE_MCP_LINK_KEY"),
            maite_api_base=env("MAITE_API_BASE").rstrip("/"),
            www_authenticate_on_401=env("MAITE_MCP_401_WWW_AUTHENTICATE", "0") == "1",
        )


class Store:
    """SQLite behind one lock. Issued tokens are stored hashed; the linked MAITE token is encrypted."""

    def __init__(self, path: str, link_key: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        self._fernet = Fernet(link_key.encode() if isinstance(link_key, str) else link_key)
        with self._lock:
            self._db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS pending (id TEXT PRIMARY KEY, params TEXT NOT NULL, exp INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS links (id INTEGER PRIMARY KEY AUTOINCREMENT, maite_user_id TEXT NOT NULL,
                    enc_token BLOB NOT NULL, created INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS codes (hash TEXT PRIMARY KEY, params TEXT NOT NULL, link_id INTEGER NOT NULL,
                    exp INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS tokens (hash TEXT PRIMARY KEY, kind TEXT NOT NULL, link_id INTEGER NOT NULL,
                    client_id TEXT NOT NULL, scopes TEXT NOT NULL, resource TEXT, exp INTEGER NOT NULL,
                    family TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
                """
            )

    def _x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, args)

    def put_pending(self, params: AuthorizationParams) -> str:
        rid = secrets.token_urlsafe(32)
        self._x("INSERT INTO pending VALUES (?, ?, ?)", (rid, params.model_dump_json(), _now() + PENDING_TTL_S))
        return rid

    def pop_pending(self, rid: str) -> AuthorizationParams | None:
        row = self._x("SELECT params, exp FROM pending WHERE id = ?", (rid,)).fetchone()
        if row is None:
            return None
        self._x("DELETE FROM pending WHERE id = ?", (rid,))
        if row[1] < _now():
            return None
        return AuthorizationParams.model_validate_json(row[0])

    def peek_pending(self, rid: str) -> AuthorizationParams | None:
        row = self._x("SELECT params, exp FROM pending WHERE id = ? AND exp >= ?", (rid, _now())).fetchone()
        return AuthorizationParams.model_validate_json(row[0]) if row else None

    def put_link(self, maite_user_id: str, maite_token: str) -> int:
        cur = self._x("INSERT INTO links (maite_user_id, enc_token, created) VALUES (?, ?, ?)",
                      (maite_user_id, self._fernet.encrypt(maite_token.encode()), _now()))
        return int(cur.lastrowid)

    def maite_token_for(self, link_id: int) -> str | None:
        row = self._x("SELECT enc_token FROM links WHERE id = ?", (link_id,)).fetchone()
        if row is None:
            return None
        try:
            return self._fernet.decrypt(bytes(row[0])).decode()
        except InvalidToken:
            return None

    def put_code(self, params: AuthorizationParams, link_id: int) -> str:
        code = secrets.token_urlsafe(32)  # 256 bits, above the RFC 6749 10.10 floor of 128
        self._x("INSERT INTO codes (hash, params, link_id, exp) VALUES (?, ?, ?, ?)",
                (_h(code), params.model_dump_json(), link_id, _now() + CODE_TTL_S))
        return code

    def get_code(self, code: str) -> tuple[AuthorizationParams, int, int] | None:
        row = self._x("SELECT params, link_id, exp FROM codes WHERE hash = ? AND used = 0", (_h(code),)).fetchone()
        return (AuthorizationParams.model_validate_json(row[0]), int(row[1]), int(row[2])) if row else None

    def consume_code(self, code: str) -> bool:
        """Mark a code used exactly once. False means it was already used (or never existed)."""
        cur = self._x("UPDATE codes SET used = 1 WHERE hash = ? AND used = 0", (_h(code),))
        return cur.rowcount == 1

    def put_token(self, kind: str, link_id: int, client_id: str, scopes: list[str], resource: str | None,
                  ttl: int, family: str) -> str:
        tok = secrets.token_urlsafe(32)
        self._x("INSERT INTO tokens (hash, kind, link_id, client_id, scopes, resource, exp, family) VALUES (?,?,?,?,?,?,?,?)",
                (_h(tok), kind, link_id, client_id, " ".join(scopes), resource, _now() + ttl, family))
        return tok

    def get_token(self, kind: str, tok: str) -> dict[str, Any] | None:
        row = self._x("SELECT link_id, client_id, scopes, resource, exp, family FROM tokens WHERE hash = ? AND kind = ? AND revoked = 0",
                      (_h(tok), kind)).fetchone()
        if row is None:
            return None
        return {"link_id": int(row[0]), "client_id": row[1], "scopes": row[2].split(), "resource": row[3],
                "exp": int(row[4]), "family": row[5]}

    def revoke_token(self, tok: str) -> None:
        self._x("UPDATE tokens SET revoked = 1 WHERE hash = ?", (_h(tok),))

    def revoke_family(self, family: str) -> None:
        self._x("UPDATE tokens SET revoked = 1 WHERE family = ?", (family,))

    def purge_expired(self) -> None:
        n = _now()
        self._x("DELETE FROM pending WHERE exp < ?", (n,))
        self._x("DELETE FROM codes WHERE exp < ?", (n,))
        self._x("DELETE FROM tokens WHERE exp < ?", (n,))


MaiteVerifier = Callable[[str], Awaitable[str | None]]


async def verify_maite_token_http(api_base: str, token: str) -> str | None:
    """Prove a MAITE bearer token the same way MAITE's app clients do: GET /api/user.
    Returns the MAITE user id as a string, or None. The token value is never logged."""
    try:
        async with httpx.AsyncClient(base_url=api_base, timeout=10.0) as c:
            r = await c.get("/api/user", headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    uid = body.get("id") if isinstance(body, dict) else None
    return str(uid) if uid is not None else None


class MaiteOAuthProvider:
    """OAuthAuthorizationServerProvider for the SDK handlers (duck-typed Protocol)."""

    def __init__(self, settings: OAuthSettings, store: Store | None = None, verifier: MaiteVerifier | None = None):
        self.s = settings
        self.store = store or Store(settings.db_path, settings.link_key)
        self.verify_maite = verifier or (lambda tok: verify_maite_token_http(settings.maite_api_base, tok))
        self._client = OAuthClientInformationFull(
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            redirect_uris=[AnyUrl(u) for u in settings.redirect_uris],
            token_endpoint_auth_method=settings.client_auth_method,  # type: ignore[arg-type]
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=SCOPE,
            client_name="Alexa+ MAITE Goal Coach",
        )

    # Client registration is static (Alexa+ has no DCR).
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if hmac.compare_digest(client_id.encode(), self.s.client_id.encode()):
            return self._client
        return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        raise NotImplementedError("Dynamic Client Registration is disabled; Alexa+ registers the client statically")

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        # RFC 8707: the resource must be this server. Alexa+ always sends it; the MCP spec requires
        # clients to send it. A missing value is tolerated (token is still bound to our resource).
        if params.resource is not None:
            try:
                ok = canonical(params.resource) == self.s.resource
            except ValueError:
                ok = False
            if not ok:
                raise AuthorizeError("invalid_request", "resource does not identify this MCP server")
        params = params.model_copy(update={"scopes": params.scopes or [SCOPE]})
        rid = self.store.put_pending(params)
        return f"{self.s.public_url}/link?req={rid}"

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        found = self.store.get_code(authorization_code)
        if found is None:
            return None
        params, link_id, exp = found
        return _LinkedCode(
            code=authorization_code, scopes=params.scopes or [SCOPE], expires_at=float(exp), client_id=self.s.client_id,
            code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly, resource=params.resource, link_id=link_id,
        )

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        # Single use. A second exchange of the same code (replay) fails here; the SDK already
        # verified PKCE and expiry before calling us.
        if not self.store.consume_code(authorization_code.code):
            raise TokenError("invalid_grant", "authorization code already used")
        link_id = getattr(authorization_code, "link_id", None)
        if link_id is None:
            raise TokenError("invalid_grant", "authorization code is not linked to an account")
        return self._issue(link_id, authorization_code.scopes, family=secrets.token_urlsafe(16))

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        row = self.store.get_token("refresh", refresh_token)
        if row is None:
            return None
        return _LinkedRefresh(token=refresh_token, client_id=row["client_id"], scopes=row["scopes"], expires_at=row["exp"],
                              link_id=row["link_id"], family=row["family"])

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        # OAuth 2.1 4.3.1 rotation: the presented refresh token dies now, its whole family dies if
        # it is ever presented again (reuse detection happens in load_refresh_token returning None
        # for the revoked row, so the SDK answers invalid_grant).
        self.store.revoke_token(refresh_token.token)
        fam = getattr(refresh_token, "family", secrets.token_urlsafe(16))
        link_id = getattr(refresh_token, "link_id")
        return self._issue(link_id, scopes or refresh_token.scopes, family=fam)

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = self.store.get_token("access", token)
        if row is None or row["exp"] < _now():
            return None
        # Audience binding (MCP spec: MUST validate tokens were issued for this server).
        if row["resource"] != self.s.resource:
            return None
        return _LinkedAccess(token=token, client_id=row["client_id"], scopes=row["scopes"], expires_at=row["exp"],
                             resource=row["resource"], link_id=row["link_id"])

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        fam = self.store.get_token("access", token.token) or self.store.get_token("refresh", token.token)
        if fam:
            self.store.revoke_family(fam["family"])

    def _issue(self, link_id: int, scopes: list[str], family: str) -> OAuthToken:
        self.store.purge_expired()
        access = self.store.put_token("access", link_id, self.s.client_id, scopes, self.s.resource, ACCESS_TTL_S, family)
        refresh = self.store.put_token("refresh", link_id, self.s.client_id, scopes, None, REFRESH_TTL_S, family)
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TTL_S, scope=" ".join(scopes),
                          refresh_token=refresh)

    # Used by server.get_client(): the MAITE credential behind an access token.
    def maite_token_for_access(self, access: AccessToken) -> str | None:
        link_id = getattr(access, "link_id", None)
        return self.store.maite_token_for(link_id) if link_id is not None else None


class _LinkedCode(AuthorizationCode):
    link_id: int


class _LinkedRefresh(RefreshToken):
    link_id: int
    family: str


class _LinkedAccess(AccessToken):
    link_id: int


# --------------------------------------------------------------------------------------------
# Link page: the user proves a MAITE account, then the code is minted and the browser is sent back.
# --------------------------------------------------------------------------------------------

class _RateLimit:
    def __init__(self, per_minute: int):
        self.n = per_minute
        self.hits: dict[str, list[int]] = {}

    def allow(self, key: str) -> bool:
        now = _now()
        h = [t for t in self.hits.get(key, []) if t > now - 60]
        h.append(now)
        self.hits[key] = h
        return len(h) <= self.n


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>body{{font-family:system-ui,sans-serif;background:#0e0f12;color:#e8e8ec;margin:0;padding:24px;line-height:1.5}}
main{{max-width:480px;margin:0 auto}}h1{{font-size:1.4rem}}label{{display:block;margin:16px 0 6px}}input{{width:100%;padding:12px;border-radius:8px;border:1px solid #333;background:#16181d;color:#fff;font-size:1rem}}
button{{margin-top:16px;padding:12px 18px;border:0;border-radius:8px;background:#5b8def;color:#fff;font-size:1rem}}p.err{{color:#ff8a80}}small{{color:#9aa}}</style></head>
<body><main>{body}</main></body></html>"""
    return HTMLResponse(doc, status_code=status, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})


def _link_form(rid: str, err: str | None = None) -> HTMLResponse:
    e = f'<p class="err">{html.escape(err)}</p>' if err else ""
    body = f"""<h1>Connect MAITE to your voice assistant</h1>
<p>Paste the access token from your MAITE account. MAITE checks it, then your assistant can check in on your goals, log progress and moods, and read your next reminder. Your password is never asked for and nothing is stored in plain text.</p>{e}
<form method="post" action="/link"><input type="hidden" name="req" value="{html.escape(rid)}">
<label for="t">MAITE access token</label><input id="t" name="maite_token" type="password" autocomplete="off" required>
<button type="submit">Connect</button></form>
<p><small>Tokens are proved against your MAITE server and kept encrypted on this server only. Source: github.com/NoBanks/maite-mcp</small></p>"""
    return _page("Connect MAITE", body)


def link_routes(provider: MaiteOAuthProvider) -> list[Route]:
    limiter = _RateLimit(LINK_RATE_LIMIT)

    async def link_get(request: Request) -> Response:
        rid = request.query_params.get("req", "")
        if not rid or provider.store.peek_pending(rid) is None:
            return _page("Link expired", "<h1>This link has expired</h1><p>Start the connection again from your assistant app.</p>", 400)
        return _link_form(rid)

    async def link_post(request: Request) -> Response:
        ip = request.client.host if request.client else "unknown"
        if not limiter.allow(ip):
            return _page("Too many attempts", "<h1>Too many attempts</h1><p>Wait a minute and try again.</p>", 429)
        form = await request.form()
        rid = str(form.get("req", ""))
        token = str(form.get("maite_token", "")).strip()
        params = provider.store.peek_pending(rid)
        if params is None:
            return _page("Link expired", "<h1>This link has expired</h1><p>Start the connection again from your assistant app.</p>", 400)
        if not token:
            return _link_form(rid, "Enter your MAITE access token.")
        uid = await provider.verify_maite(token)
        if uid is None:
            return _link_form(rid, "MAITE did not accept that token. Check it and try again.")
        provider.store.pop_pending(rid)
        link_id = provider.store.put_link(uid, token)
        code = provider.store.put_code(params, link_id)
        return RedirectResponse(construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state),
                                status_code=302, headers={"Cache-Control": "no-store"})

    return [Route("/link", link_get, methods=["GET"]), Route("/link", link_post, methods=["POST"])]


# --------------------------------------------------------------------------------------------
# Alexa-shaped 401 gate (sits in front of the SDK's AuthenticationMiddleware result).
# --------------------------------------------------------------------------------------------

class AlexaUnauthorizedGate:
    """401 when the SDK bearer backend did not authenticate the request.

    Alexa+ quickstart: 401 WITHOUT WWW-Authenticate, body {"error": "unauthorized", "message": ...}.
    MCP spec: WWW-Authenticate with resource_metadata is one of two allowed discovery mechanisms; the
    other is the well-known PRM URI, which this server serves at both the root and the path form.
    MAITE_MCP_401_WWW_AUTHENTICATE=1 adds the header for strict clients; off by default for Alexa+.
    """

    def __init__(self, app, resource_metadata_url: str, add_header: bool):
        self._app = app
        self._prm = resource_metadata_url
        self._add_header = add_header

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

        if scope["type"] == "http" and not isinstance(scope.get("user"), AuthenticatedUser):
            headers = {"Cache-Control": "no-store"}
            if self._add_header:
                headers["WWW-Authenticate"] = f'Bearer resource_metadata="{self._prm}", scope="{SCOPE}"'
            resp = JSONResponse({"error": "unauthorized", "message": "Access token required to use this tool."},
                                status_code=401, headers=headers)
            await resp(scope, receive, send)
            return
        await self._app(scope, receive, send)


def oauth_routes(provider: MaiteOAuthProvider) -> list[Route]:
    """Authorization server routes + PRM at root AND path form + link page."""
    s = provider.s
    issuer = AnyHttpUrl(s.public_url)
    routes = create_auth_routes(
        provider, issuer_url=issuer,
        client_registration_options=ClientRegistrationOptions(enabled=False, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
        revocation_options=RevocationOptions(enabled=True),
    )
    resource_url = AnyHttpUrl(s.resource)
    routes += create_protected_resource_routes(resource_url, [issuer], scopes_supported=[SCOPE], resource_name="MAITE")
    # Alexa+ fetches the ROOT well-known document; the SDK registers only the path form.
    prm = ProtectedResourceMetadata(resource=resource_url, authorization_servers=[issuer], scopes_supported=[SCOPE],
                                    resource_name="MAITE")

    async def prm_root(_: Request) -> Response:
        return JSONResponse(json.loads(prm.model_dump_json(exclude_none=True)), headers={"Cache-Control": "public, max-age=3600"})

    routes.append(Route("/.well-known/oauth-protected-resource", prm_root, methods=["GET"]))
    routes += link_routes(provider)
    return routes


def new_link_key() -> str:
    """Generate a value for MAITE_MCP_LINK_KEY (printed by `python -m maite_mcp.oauth`)."""
    return Fernet.generate_key().decode()


if __name__ == "__main__":  # pragma: no cover
    print(new_link_key())
