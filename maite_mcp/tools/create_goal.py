"""Tool: create_goal - Create a new goal for the user."""

from typing import Any

from mcp.types import Tool, TextContent

from maite_mcp.client import MAITEClient
from maite_mcp.schemas import CreateGoalInput


def get_create_goal_tool() -> Tool:
    """Return the create_goal tool definition."""
    return Tool(
        name="create_goal",
        description="Create a new goal for the user. Goals are private to the user's MAITE account and never leave their device unless they sync.",
        inputSchema={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "maxLength": 120,
                    "description": "The title of the goal"
                },
                "description": {
                    "type": "string",
                    "maxLength": 1000,
                    "description": "Optional description of the goal"
                },
                "target_date": {
                    "type": "string",
                    "format": "date",
                    "description": "ISO date (YYYY-MM-DD) for target completion"
                },
                "category": {
                    "type": "string",
                    "enum": ["health", "career", "relationships", "creative", "financial", "spiritual", "learning", "other"],
                    "description": "Category of the goal"
                },
                "lang": {
                    "type": "string",
                    "description": "BCP47 lang code, e.g. en, es, ja"
                }
            },
            "required": ["title"]
        }
    )


async def create_goal(client: MAITEClient, params: dict[str, Any]) -> TextContent:
    """Execute the create_goal tool.
    
    Args:
        client: MAITEClient instance for API calls.
        params: Tool parameters from the MCP request.
    
    Returns:
        TextContent with the created goal details.
    """
    input_data = CreateGoalInput(**params)
    
    payload = {
        "title": input_data.title,
    }
    
    if input_data.description is not None:
        payload["description"] = input_data.description
    
    if input_data.target_date is not None:
        payload["target_date"] = input_data.target_date.isoformat()
    
    if input_data.category is not None:
        payload["category"] = input_data.category
    
    if input_data.lang is not None:
        payload["lang"] = input_data.lang
    
    response = await client.post("/v1/goals", json=payload)
    
    return TextContent(
        type="text",
        text=f"Goal created successfully!\n\nGoal ID: {response['goal_id']}\nTitle: {response['title']}\nCreated at: {response['created_at']}\nURL: {response['url']}"
    )
