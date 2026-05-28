"""Set reminder tool for MAITE MCP server."""

from typing import Any

from pydantic import Field

from maite_mcp.schemas import SetReminderInput
from maite_mcp.client import MAITEClient


async def set_reminder(
    client: MAITEClient,
    input_data: SetReminderInput,
) -> dict[str, Any]:
    """
    Set a reminder linked to a goal or standalone.

    The reminder fires through the user's MAITE PWA notification channel.
    """
    payload = {
        "text": input_data.text,
        "fires_at": input_data.fires_at.isoformat(),
    }

    if input_data.goal_id:
        payload["goal_id"] = input_data.goal_id

    if input_data.repeat:
        payload["repeat"] = input_data.repeat

    response = await client.post(
        endpoint="/v1/reminders",
        json=payload,
    )

    return {
        "reminder_id": response["reminder_id"],
        "text": response["text"],
        "fires_at": response["fires_at"],
        "next_fire_at": response.get("next_fire_at"),
    }


TOOL_DEFINITION = {
    "name": "set_reminder",
    "description": "Set a reminder linked to a goal or standalone. The reminder fires through the user's MAITE PWA notification channel.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "maxLength": 240,
                "description": "The reminder text",
            },
            "fires_at": {
                "type": "string",
                "format": "date-time",
                "description": "ISO8601 datetime when the reminder should fire",
            },
            "goal_id": {
                "type": "string",
                "description": "Optional: link this reminder to a goal",
            },
            "repeat": {
                "type": "string",
                "enum": ["none", "daily", "weekly", "monthly"],
                "default": "none",
                "description": "How often the reminder should repeat",
            },
        },
        "required": ["text", "fires_at"],
    },
}
