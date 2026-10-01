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

## Transports

The same five tools are served over two transports. Pick one with `MAITE_MCP_TRANSPORT`.

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

Endpoints:

- `GET /healthz` returns the server name, version, transport, spec version and whether a bearer token is configured. It never returns the token.
- `POST`, `GET`, `DELETE` on `MAITE_MCP_PATH` is the MCP Streamable HTTP endpoint (sessions are tracked with the `Mcp-Session-Id` header by the official SDK).

Requests without a valid token get `401` with a `WWW-Authenticate: Bearer` header.

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

Errors from the backend come back as structured text (`MAITE API Error: <message> (status: <code>)`), never as tracebacks.

## Privacy

The server is a thin, stateless proxy. It stores nothing. Goals, journal entries and companion
replies live on the MAITE backend the user operates; the language model behind MAITE runs on the
operator's own hardware.

## License

MIT. See LICENSE.
