"""Check progress tool for MAITE MCP server."""

from typing import Any

from mcp.types import Tool, TextContent

from maite_mcp.client import MAITEClient
from maite_mcp.schemas import CheckProgressInput


def get_check_progress_tool(client: MAITEClient) -> Tool:
    """Return the check_progress tool definition and handler."""
    
    return Tool(
        name="check_progress",
        description="Check progress on one specific goal or all active goals. Returns completion percent, milestones hit, and MAITE's scaffolding advice for next steps.",
        inputSchema={
            "type": "object",
            "properties": {
                "goal_id": {
                    "type": "string",
                    "description": "Omit to get all active goals"
                },
                "include_scaffolding_advice": {
                    "type": "boolean",
                    "default": True,
                    "description": "Whether to include MAITE's scaffolding advice"
                }
            },
            "required": []
        }
    )


async def handle_check_progress(
    client: MAITEClient,
    arguments: dict[str, Any]
) -> TextContent:
    """Handle the check_progress tool call."""
    input_data = CheckProgressInput(**arguments)
    
    include_advice = input_data.include_scaffolding_advice if input_data.include_scaffolding_advice is not None else True
    
    if input_data.goal_id:
        # Get progress for specific goal
        response = await client.get(
            f"/v1/goals/{input_data.goal_id}/progress",
            params={"include_scaffolding_advice": include_advice}
        )
        goals = [response]
    else:
        # Get progress for all active goals
        response = await client.get(
            "/v1/goals/progress",
            params={"include_scaffolding_advice": include_advice}
        )
        goals = response.get("goals", [])
    
    return TextContent(
        type="text",
        text=f"Goal Progress Report\n\n" + "\n".join([
            f"Goal: {g.get('title', 'Unknown')}\n"
            f"  ID: {g.get('goal_id')}\n"
            f"  Progress: {g.get('progress_pct', 0)}%\n"
            f"  Milestones Hit: {g.get('milestones_hit', 0)}\n"
            f"  Milestones Remaining: {g.get('milestones_remaining', 0)}\n"
            + (f"  MAITE's Advice: {g.get('scaffolding_advice')}\n" if include_advice and g.get('scaffolding_advice') else "")
            for g in goals
        ])
    )
