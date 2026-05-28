"""Tool: log_journal_entry - Add a journal entry for the user."""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from maite_mcp.schemas import InputSchema


class LogJournalEntryInput(InputSchema):
    """Input schema for log_journal_entry tool."""

    text: Optional[str] = Field(
        default=None,
        description="Journal text (skip if audio_url provided)",
    )
    audio_url: Optional[str] = Field(
        default=None,
        description="URL of audio file (skip if text provided)",
        format="uri",
    )
    mood: Optional[Literal["awful", "low", "neutral", "good", "great"]] = Field(
        default=None,
        description="Mood indicator for this entry",
    )
    lang: Optional[str] = Field(
        default=None,
        description="BCP47 language code (e.g., en, es, ja)",
    )


class LogJournalEntryResult(BaseModel):
    """Result schema for log_journal_entry tool."""

    entry_id: str = Field(description="Unique identifier for the journal entry")
    transcript: Optional[str] = Field(
        default=None,
        description="Transcribed text if audio was provided",
    )
    logged_at: str = Field(description="ISO8601 timestamp when entry was logged")
    memory_meter: int = Field(
        description="Current memory meter value after this entry",
        ge=0,
        le=100,
    )


async def log_journal_entry(
    client,
    tool_args: LogJournalEntryInput,
) -> LogJournalEntryResult:
    """
    Add a journal entry for the user.

    Supports text or a URL pointing to an audio recording (audio is
    transcribed via MAITE's local Whisper).

    Args:
        client: MAITEClient instance for making API calls.
        tool_args: Validated input arguments for the journal entry.

    Returns:
        LogJournalEntryResult with entry details and memory meter.

    Raises:
        MAITEAPIError: If the API call fails or returns an error.
        ValueError: If neither text nor audio_url is provided.
    """
    if not tool_args.text and not tool_args.audio_url:
        raise ValueError("Either 'text' or 'audio_url' must be provided")

    payload: dict = {}

    if tool_args.text is not None:
        payload["text"] = tool_args.text

    if tool_args.audio_url is not None:
        payload["audio_url"] = tool_args.audio_url

    if tool_args.mood is not None:
        payload["mood"] = tool_args.mood

    if tool_args.lang is not None:
        payload["lang"] = tool_args.lang

    response_data = await client.post(
        endpoint="/v1/journal",
        json=payload,
    )

    return LogJournalEntryResult(
        entry_id=response_data["entry_id"],
        transcript=response_data.get("transcript"),
        logged_at=response_data["logged_at"],
        memory_meter=response_data["memory_meter"],
    )
