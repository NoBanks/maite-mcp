"""Tool: get_companion_response - Send a message to MAITE's companion AI."""

from typing import Literal

from mcp.types import Tool, TextContent

from maite_mcp.schemas import GetCompanionResponseInput
from maite_mcp.client import MAITEClient


TOOL_DEFINITION = Tool(
    name="get_companion_response",
    description=(
        "Send a message to MAITE's companion AI and get a response in the user's "
        "preferred language. Used for emotional support, reflection prompts, language "
        "practice. Sovereign: response is generated locally on the user's MAITE-hosting "
        "hardware."
    ),
    inputSchema=GetCompanionResponseInput.model_json_schema(),
)


async def execute(
    client: MAITEClient,
    user_message: str,
    context_hint: str | None = None,
    lang: str | None = None,
    tone: Literal["empathetic", "challenging", "playful", "practical"] = "empathetic",
) -> TextContent:
    """
    Send a message to MAITE's companion AI and get a response.

    Args:
        client: MAITE API client instance.
        user_message: The message to send to MAITE.
        context_hint: Optional context about what the user is working through.
        lang: BCP47 lang code for the response language.
        tone: Desired tone of the companion response.

    Returns:
        TextContent with the companion response and metadata.
    """
    payload: dict = {
        "user_message": user_message,
        "tone": tone,
    }

    if context_hint is not None:
        payload["context_hint"] = context_hint
    if lang is not None:
        payload["lang"] = lang

    response = await client.post("/v1/companion/respond", json=payload)

    return TextContent(
        type="text",
        text=f"MAITE's response:\n\n{response['response']}\n\n"
        f"Language: {response['lang']}\n"
        f"Memory context: {', '.join(response['memory_used']) if response['memory_used'] else 'None'}\n"
        f"SmartPause suggested: {response['smart_pause_suggested']}",
    )
