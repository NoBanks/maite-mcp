# maite-mcp

MAITE MCP Server - A Model Context Protocol server for MAITE, the sovereign AI companion for goal-tracking and personal accountability.

## Overview

This MCP server enables AI agents (Claude Desktop, Cursor, etc.) to interact with MAITE's backend on behalf of users. It provides tools for managing goals, journaling, checking progress, getting companion responses, and setting reminders.

**MAITE** is a sovereign AI companion that prioritizes user privacy and data ownership. All diary entries, tasks, goals, and AI interactions remain on the user's device unless they explicitly choose to sync.

## Features

- **Goal Management**: Create and track personal goals across 8 categories
- **Journaling**: Log text or audio journal entries with mood tracking
- **Progress Tracking**: Monitor goal completion with AI-powered scaffolding advice
- **Companion Chat**: Get empathetic, sovereign AI responses in 8 languages
- **Reminders**: Set goal-linked or standalone reminders
- **Voice tools** (October 2026): four tools that answer in one or two spoken sentences for Alexa+ and
  other voice surfaces

## Installation

```
git clone https://github.com/NoBanks/maite-mcp
cd maite-mcp
python3.11 -m pip install -e ".[dev]"
python3.11 -m pytest tests/ -q
```

## Configuration

| Variable | Required | Meaning |
|---|---|---|
| `MAITE_API_BASE` | yes | Base URL of the MAITE backend the server talks to on the user's behalf |
| `MAITE_API_KEY` | yes | Bearer token for that backend. Read from the environment, never from code |
| `MAITE_USER_LANG` | no | Default BCP47 language for replies, default `en` |
| `MAITE_PUSH_ENDPOINT` | no | Push subscription endpoint whose morning and evening nudge settings `next_reminder` reads when the call does not pass one |

## Transports

The same nine tools are served over two transports. Pick one with `MAITE_MCP_TRANSPORT`.

### stdio (default)

For Claude Desktop, Cursor and other local clients that launch the server as a subprocess.

```
maite-mcp
```

Claude Desktop entry:

```json
{
  "mcpServers": {
    "maite": {
      "command": "maite-mcp",
      "env": {
        "MAITE_API_BASE": "https://your-maite-backend.example",
        "MAITE_API_KEY": "set-in-your-local-config-only"
      }
    }
  }
}
```

### Streamable HTTP (MCP spec 2025-11-25)

For remote clients such as Alexa+, MCP Inspector, or any client that speaks the Streamable HTTP
transport. Added October 2026.

```
MAITE_MCP_TRANSPORT=streamable-http \
MAITE_MCP_HOST=127.0.0.1 \
MAITE_MCP_PORT=18800 \
MAITE_MCP_BEARER_TOKEN=<a long random token> \
maite-mcp
```

| Variable | Default | Meaning |
|---|---|---|
| `MAITE_MCP_TRANSPORT` | `stdio` | `streamable-http` turns the HTTP server on |
| `MAITE_MCP_HOST` | `127.0.0.1` | Bind address. Keep loopback and put a tunnel or reverse proxy in front |
| `MAITE_MCP_PORT` | `18800` | Bind port |
| `MAITE_MCP_PATH` | `/mcp` | The MCP endpoint path |
| `MAITE_MCP_BEARER_TOKEN` | unset | When set, every MCP request must send `Authorization: Bearer <token>`. Unset means no auth, loopback development only |
| `MAITE_MCP_JSON_RESPONSE` | `0` | `1` answers with plain JSON instead of SSE streams |
| `MAITE_MCP_ALLOWED_HOSTS` | unset | Comma separated `Host` values for DNS rebinding protection, for example `maite-mcp.example.com,127.0.0.1:18800` |
| `MAITE_MCP_AUTH` | `bearer` | `oauth` turns on the OAuth 2.1 authorization server and account linking described under Alexa+ |

Endpoints:

- `GET /healthz` returns the server name, version, transport, spec version and whether a bearer token is configured. It never returns the token.
- `POST`, `GET`, `DELETE` on `MAITE_MCP_PATH` is the MCP Streamable HTTP endpoint (sessions are tracked with the `Mcp-Session-Id` header by the official SDK).

In bearer mode, requests without a valid token get `401` with a `WWW-Authenticate: Bearer` header. In OAuth mode the 401 takes the shape Alexa+ asks for (see below).

Connect with the official Python SDK:

```python
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

http = httpx.AsyncClient(headers={"Authorization": "Bearer <token>"})
async with streamable_http_client("http://127.0.0.1:18800/mcp", http_client=http) as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
```

## Tools

| Tool | What it does |
|---|---|
| `create_goal` | Create a goal (title, description, target date, one of 8 categories, language) |
| `log_journal_entry` | Journal text or an audio URL (transcribed by MAITE's local Whisper) with a mood |
| `check_progress` | Progress on one goal or all active goals, with scaffolding advice |
| `get_companion_response` | A companion reply in the user's language and chosen tone |
| `set_reminder` | A goal-linked or standalone reminder, optional repeat |

### Voice tools (Alexa+ and other spoken surfaces)

These four return ONE string of one or two sentences, under 220 characters, no newlines, no markdown,
numbers spelled the way a person says them ("forty five percent", "four days"). They call the MAITE
backend's real routes, listed so an operator can audit the traffic.

| Tool | Says | Backend routes |
|---|---|---|
| `check_in` | How many goals are active, which is furthest along, the check-in streak | `GET /api/goals`, `GET /api/checkins`, `GET /api/user` |
| `log_progress` | One-sentence confirmation after saving a note and, optionally, a new percent. Goal by id or part of its title | `GET /api/goals`, `PATCH /api/goals/{id}/progress` |
| `journal_mood` | One-sentence acknowledgement after logging the mood (awful, low, neutral, good, great) plus an optional note as a check-in | `POST /api/checkins` |
| `next_reminder` | When the next morning or evening check-in nudge fires, in the user's timezone | `GET /api/user`, `GET /api/notifications/settings?endpoint=...` |

Sample spoken output:

```
You have two active goals. Run three times a week is furthest along at eighty percent. Your check-in streak is four days.
Logged. Learn Spanish is at fifty percent. Note saved.
Noted that you are feeling low today. Logged, and be gentle with yourself.
Your next reminder is the evening check-in today at eight in the evening.
```

### Known limits

- MAITE has no reminders table. Its scheduled nudges are the morning and evening check-in push
  notifications, stored per push subscription. `next_reminder` reads those, so it needs the
  subscription endpoint (argument or `MAITE_PUSH_ENDPOINT`). With neither it says so instead of guessing.
- The streak is computed client-side from check-in dates in the user's `user_timezone` (UTC when unset).
  The backend does not store a streak.
- `log_progress` by name requires a unique match. Two goals that both contain the word get a spoken
  "did you mean" instead of a guess.
- The five long-form tools above target the `/v1` contract in `client.py`. The voice tools target the
  routes the MAITE app actually serves (`/api/...`). Both use the same bearer token.

Errors from the backend come back as structured text (`MAITE API Error: <message> (status: <code>)`), never as tracebacks.

## Alexa+

Alexa+ connects to self-hosted MCP servers over Streamable HTTP on MCP spec 2025-11-25 through an
MCP add-on (developer.amazon.com/docs/alexaplus/add-ons/mcp-toolkit-overview.html). Per-user data
like MAITE's needs account linking, which Alexa+ does with OAuth 2.1 authorization code plus PKCE
S256, a Protected Resource Metadata document, a statically registered client and refresh tokens
(developer.amazon.com/docs/alexaplus/add-ons/mcp-toolkit-account-linking.html). This server ships
that layer in `maite_mcp/oauth.py`. Turn it on with `MAITE_MCP_AUTH=oauth`.

### Setup

```bash
MAITE_MCP_TRANSPORT=streamable-http \
MAITE_MCP_AUTH=oauth \
MAITE_MCP_PUBLIC_URL=https://maite-mcp.example.com \
MAITE_MCP_OAUTH_CLIENT_ID=<the client id you register with Alexa> \
MAITE_MCP_OAUTH_CLIENT_SECRET=<the matching secret> \
MAITE_MCP_OAUTH_REDIRECT_URIS=<every redirect URI alexa-ai configure-account-linking prints, comma separated> \
MAITE_MCP_LINK_KEY=$(python3 -m maite_mcp.oauth) \
MAITE_API_BASE=https://your-maite-backend.example \
maite-mcp
```

| Variable | Default | Meaning |
|---|---|---|
| `MAITE_MCP_PUBLIC_URL` | required | Issuer and public origin, no path. The resource identifier is this plus `MAITE_MCP_PATH`, and it must match the add-on manifest exactly |
| `MAITE_MCP_OAUTH_CLIENT_ID` | required | The one statically registered client (Alexa+ has no Dynamic Client Registration) |
| `MAITE_MCP_OAUTH_CLIENT_SECRET` | required unless auth method is `none` | Its secret, compared in constant time at the token endpoint |
| `MAITE_MCP_OAUTH_CLIENT_AUTH_METHOD` | `client_secret_post` | `client_secret_post`, `client_secret_basic` or `none` |
| `MAITE_MCP_OAUTH_REDIRECT_URIS` | required | Comma separated. Exact-match validated; an unregistered URI gets a 400, never a redirect |
| `MAITE_MCP_LINK_KEY` | required | Fernet key that encrypts linked MAITE credentials at rest. Generate with `python3 -m maite_mcp.oauth` |
| `MAITE_MCP_OAUTH_DB` | `~/.maite-mcp/oauth.sqlite3` | SQLite file for pending requests, codes, tokens and links |
| `MAITE_MCP_401_WWW_AUTHENTICATE` | `0` | `1` adds `WWW-Authenticate: Bearer resource_metadata=...` to 401s for spec-strict clients. Alexa+ documents that it does not support the header yet, so leave it off for the add-on |

### What it serves

- `/.well-known/oauth-authorization-server` (RFC 8414) with `code_challenge_methods_supported: ["S256"]`, which Alexa+ checks at deploy time.
- `/.well-known/oauth-protected-resource` and `/.well-known/oauth-protected-resource<MAITE_MCP_PATH>` (RFC 9728). Alexa+ reads the root form; the MCP spec allows either.
- `/authorize`, `/token`, `/revoke` from the official SDK handlers (PKCE verification, client authentication, redirect URI binding, code expiry).
- `/link`: the account linking page. The user pastes their MAITE access token, the server proves it with `GET /api/user` on the MAITE backend (the same bearer route MAITE's own mobile client uses, see `server/mobile-auth.ts` in the MAITE app), then mints the authorization code and sends the browser back to Alexa. Passwords are never asked for.
- Unauthenticated MCP requests get `401` with body `{"error": "unauthorized", "message": "Access token required to use this tool."}` and no `WWW-Authenticate` header, exactly as the Alexa+ quickstart specifies. Discovery works through the well-known PRM URI, which the MCP spec accepts as the alternative to the header.

### Token handling

- Access tokens live 3600 seconds and are bound to this server's resource (RFC 8707 audience check on every request). Refresh tokens live 90 days and rotate on every use; a replayed refresh token is rejected.
- Authorization codes are single use, expire after 300 seconds, carry 256 bits of entropy.
- Everything this server issues is stored as a SHA-256 hash. The linked MAITE credential has to be used later on the user's behalf, so it is stored encrypted with `MAITE_MCP_LINK_KEY`, never in plain text.
- Every tool call in OAuth mode acts as the linked user with that user's own MAITE credential. There is no shared service account and no token passthrough: the Alexa access token never reaches MAITE.

### Known limits

- MAITE today issues bearer tokens only to its Android sign-in flow. A "connect a voice assistant" screen in the MAITE app that hands the user a token to paste is MAITE-side work and is not part of this repository.
- Not yet exercised against a live Alexa+ device or simulator. Every requirement above was taken from the Amazon documents read on 2026-10-01 and tested in-process.

## Privacy

The server is a thin, stateless proxy. It stores nothing. Goals, journal entries and companion
replies live on the MAITE backend the user operates; the language model behind MAITE runs on the
operator's own hardware.

## License

MIT. See LICENSE.
