"""MAITE MCP Server - Sovereign AI companion for goal-tracking and personal accountability."""

import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .client import MAITEClient, MAITEAPIError
from .schemas import (
    CreateGoalInput,
    LogJournalEntryInput,
    CheckProgressInput,
    GetCompanionResponseInput,
    SetReminderInput,
    VoiceCheckInInput,
    LogProgressInput,
    JournalMoodInput,
    NextReminderInput,
)
from . import voice

# Server instance
SERVER_NAME = "maite-mcp"
SERVER_VERSION = "1.2.0"

server = Server(SERVER_NAME)


# OAuth mode (http_server.py with MAITE_MCP_AUTH=oauth) installs a resolver that maps the caller's
# access token to that user's own MAITE credential, so every tool call acts as the linked user and
# never as a shared service account. stdio and bearer modes leave it None and use MAITE_API_KEY.
_credential_resolver = None


def set_credential_resolver(fn) -> None:
    global _credential_resolver
    _credential_resolver = fn


def _per_user_api_key() -> str | None:
    if _credential_resolver is None:
        return None
    try:
        from mcp.server.auth.middleware.auth_context import get_access_token
    except ImportError:  # pragma: no cover
        return None
    access = get_access_token()
    if access is None:
        return None
    key = _credential_resolver(access)
    if not key:
        # MCP spec: never fall back to another identity's token (no token passthrough, no shared key).
        raise ValueError("No MAITE account is linked to this access token. Reconnect MAITE from your assistant app.")
    return key


def get_client() -> MAITEClient:
    """Get a MAITE client: the linked user's credential in OAuth mode, else MAITE_API_KEY from the environment."""
    api_base = os.environ.get("MAITE_API_BASE")
    user_lang = os.environ.get("MAITE_USER_LANG", "en")

    if not api_base:
        raise ValueError("MAITE_API_BASE environment variable is required")
    api_key = _per_user_api_key() or os.environ.get("MAITE_API_KEY")
    if not api_key:
        raise ValueError("MAITE_API_KEY environment variable is required")

    return MAITEClient(api_base=api_base, api_key=api_key, user_lang=user_lang)


# Tool definitions
TOOLS: list[Tool] = [
    Tool(
        name="create_goal",
        description="Create a new goal for the user. Goals are private to the user's MAITE account and never leave their device unless they sync.",
        inputSchema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "maxLength": 120, "description": "Goal title"},
                "description": {"type": "string", "maxLength": 1000, "description": "Goal description"},
                "target_date": {"type": "string", "format": "date", "description": "ISO date (YYYY-MM-DD)"},
                "category": {
                    "type": "string",
                    "enum": ["health", "career", "relationships", "creative", "financial", "spiritual", "learning", "other"],
                    "description": "Goal category",
                },
                "lang": {"type": "string", "description": "BCP47 lang code, e.g. en, es, ja"},
            },
            "required": ["title"],
        },
    ),
    Tool(
        name="log_journal_entry",
        description="Add a journal entry for the user. Supports text or a URL pointing to an audio recording (audio is transcribed via MAITE's local Whisper).",
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Journal text (skip if audio_url provided)"},
                "audio_url": {"type": "string", "format": "uri", "description": "URL of audio file (skip if text provided)"},
                "mood": {
                    "type": "string",
                    "enum": ["awful", "low", "neutral", "good", "great"],
                    "description": "Current mood",
                },
                "lang": {"type": "string", "description": "BCP47 lang code"},
            },
            "required": [],
        },
    ),
    Tool(
        name="check_progress",
        description="Check progress on one specific goal or all active goals. Returns completion percent, milestones hit, and MAITE's scaffolding advice for next steps.",
        inputSchema={
            "type": "object",
            "properties": {
                "goal_id": {"type": "string", "description": "Omit to get all active goals"},
                "include_scaffolding_advice": {"type": "boolean", "default": True, "description": "Include MAITE's scaffolding advice"},
            },
            "required": [],
        },
    ),
    Tool(
        name="get_companion_response",
        description="Send a message to MAITE's companion AI and get a response in the user's preferred language. Used for emotional support, reflection prompts, language practice. Sovereign: response is generated locally on the user's MAITE-hosting hardware.",
        inputSchema={
            "type": "object",
            "properties": {
                "user_message": {"type": "string", "maxLength": 4000, "description": "Message to MAITE"},
                "context_hint": {"type": "string", "description": "Optional: what the user is working through right now"},
                "lang": {"type": "string", "description": "BCP47 lang code"},
                "tone": {
                    "type": "string",
                    "enum": ["empathetic", "challenging", "playful", "practical"],
                    "default": "empathetic",
                    "description": "Response tone",
                },
            },
            "required": ["user_message"],
        },
    ),
    Tool(
        name="set_reminder",
        description="Set a reminder linked to a goal or standalone. The reminder fires through the user's MAITE PWA notification channel.",
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "maxLength": 240, "description": "Reminder text"},
                "fires_at": {"type": "string", "format": "date-time", "description": "ISO8601 datetime"},
                "goal_id": {"type": "string", "description": "Optional: link this reminder to a goal"},
                "repeat": {
                    "type": "string",
                    "enum": ["none", "daily", "weekly", "monthly"],
                    "default": "none",
                    "description": "Repeat interval",
                },
            },
            "required": ["text", "fires_at"],
        },
    ),
    # ---- Voice-shaped tools (Alexa+ and other spoken surfaces). One or two spoken sentences,
    # under schemas.SPOKEN_MAX_CHARS, no markdown. These hit the MAITE backend's real routes. ----
    Tool(
        name="check_in",
        description="Spoken daily check-in: how many goals are active, which one is furthest along, and the current check-in streak. Returns one or two short sentences meant to be read aloud.",
        inputSchema={
            "type": "object",
            "properties": {
                "include_streak": {"type": "boolean", "default": True, "description": "Mention the check-in streak"},
            },
            "required": [],
        },
    ),
    Tool(
        name="log_progress",
        description="Log progress on a goal by id or by part of its name, with an optional short note and optional new percent. Returns a one-sentence spoken confirmation.",
        inputSchema={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "minLength": 1, "maxLength": 120, "description": "Goal id or part of the goal title"},
                "note": {"type": "string", "maxLength": 240, "description": "Short progress note"},
                "progress": {"type": "integer", "minimum": 0, "maximum": 100, "description": "New progress percent; omit to keep the current percent"},
            },
            "required": ["goal"],
        },
    ),
    Tool(
        name="journal_mood",
        description="Log how the user feels right now as a MAITE check-in. Mood is one word (awful, low, neutral, good, great) plus an optional note. Returns a one-sentence spoken acknowledgement.",
        inputSchema={
            "type": "object",
            "properties": {
                "mood": {"type": "string", "enum": ["awful", "low", "neutral", "good", "great"], "description": "Current mood"},
                "note": {"type": "string", "maxLength": 400, "description": "Optional sentence about why"},
            },
            "required": ["mood"],
        },
    ),
    Tool(
        name="next_reminder",
        description="Say when the user's next MAITE reminder fires (the morning or evening check-in nudge). Returns one spoken sentence.",
        inputSchema={
            "type": "object",
            "properties": {
                "push_endpoint": {"type": "string", "maxLength": 2000, "description": "Push subscription endpoint to read settings for; defaults to MAITE_PUSH_ENDPOINT"},
            },
            "required": [],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available MAITE tools."""
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls for MAITE MCP."""
    client = get_client()

    try:
        if name == "create_goal":
            input_data = CreateGoalInput(**arguments)
            result = await client.create_goal(input_data)
            return [TextContent(type="text", text=str(result))]

        elif name == "log_journal_entry":
            input_data = LogJournalEntryInput(**arguments)
            result = await client.log_journal_entry(input_data)
            return [TextContent(type="text", text=str(result))]

        elif name == "check_progress":
            input_data = CheckProgressInput(**arguments)
            result = await client.check_progress(input_data)
            return [TextContent(type="text", text=str(result))]

        elif name == "get_companion_response":
            input_data = GetCompanionResponseInput(**arguments)
            result = await client.get_companion_response(input_data)
            return [TextContent(type="text", text=str(result))]

        elif name == "set_reminder":
            input_data = SetReminderInput(**arguments)
            result = await client.set_reminder(input_data)
            return [TextContent(type="text", text=str(result))]

        elif name == "check_in":
            return [TextContent(type="text", text=await voice.check_in(client, VoiceCheckInInput(**arguments)))]

        elif name == "log_progress":
            return [TextContent(type="text", text=await voice.log_progress(client, LogProgressInput(**arguments)))]

        elif name == "journal_mood":
            return [TextContent(type="text", text=await voice.journal_mood(client, JournalMoodInput(**arguments)))]

        elif name == "next_reminder":
            return [TextContent(type="text", text=await voice.next_reminder(client, NextReminderInput(**arguments)))]

        else:
            raise ValueError(f"Unknown tool: {name}")

    except MAITEAPIError as e:
        return [TextContent(type="text", text=f"MAITE API Error: {e.message} (status: {e.status_code})")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def main_async():
    """Async entry point for the MAITE MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main_sync():
    """Entry point. Transport is chosen by MAITE_MCP_TRANSPORT: stdio (default) or streamable-http
    (see http_server.py for the MAITE_MCP_* variables)."""
    import asyncio

    from .http_server import run_http, transport_is_http

    if transport_is_http():
        run_http()
        return
    asyncio.run(main_async())


if __name__ == "__main__":
    main_sync()
