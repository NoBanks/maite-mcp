"""Pydantic v2 schemas for MAITE MCP tools."""

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class GoalCategory(str, Enum):
    HEALTH = "health"
    CAREER = "career"
    RELATIONSHIPS = "relationships"
    CREATIVE = "creative"
    FINANCIAL = "financial"
    SPIRITUAL = "spiritual"
    LEARNING = "learning"
    OTHER = "other"


class Mood(str, Enum):
    AWFUL = "awful"
    LOW = "low"
    NEUTRAL = "neutral"
    GOOD = "good"
    GREAT = "great"


class Tone(str, Enum):
    EMPATHETIC = "empathetic"
    CHALLENGING = "challenging"
    PLAYFUL = "playful"
    PRACTICAL = "practical"


class RepeatInterval(str, Enum):
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class CreateGoalInput(BaseModel):
    """Input schema for creating a new goal."""

    title: str = Field(..., max_length=120, description="Goal title")
    description: Optional[str] = Field(None, max_length=1000, description="Goal description")
    target_date: Optional[date] = Field(None, description="Target completion date (YYYY-MM-DD)")
    category: Optional[GoalCategory] = Field(None, description="Goal category")
    lang: Optional[str] = Field(None, description="BCP47 language code")


class LogJournalEntryInput(BaseModel):
    """Input schema for logging a journal entry."""

    text: Optional[str] = Field(None, description="Journal text (skip if audio_url provided)")
    audio_url: Optional[str] = Field(None, description="URL of audio file (skip if text provided)")
    mood: Optional[Mood] = Field(None, description="Current mood")
    lang: Optional[str] = Field(None, description="BCP47 language code")


class CheckProgressInput(BaseModel):
    """Input schema for checking goal progress."""

    goal_id: Optional[str] = Field(None, description="Specific goal ID (omit for all active goals)")
    include_scaffolding_advice: bool = Field(True, description="Include MAITE's scaffolding advice")


class GetCompanionResponseInput(BaseModel):
    """Input schema for getting a companion response from MAITE."""

    user_message: str = Field(..., max_length=4000, description="Message to MAITE")
    context_hint: Optional[str] = Field(None, description="Optional context about what user is working through")
    lang: Optional[str] = Field(None, description="BCP47 language code")
    tone: Tone = Field(Tone.EMPATHETIC, description="Preferred tone of response")


class SetReminderInput(BaseModel):
    """Input schema for setting a reminder."""

    text: str = Field(..., max_length=240, description="Reminder text")
    fires_at: datetime = Field(..., description="When to fire the reminder (ISO8601)")
    goal_id: Optional[str] = Field(None, description="Optional goal ID to link reminder to")
    repeat: RepeatInterval = Field(RepeatInterval.NONE, description="Repeat interval")


class CreateGoalOutput(BaseModel):
    """Output schema for create_goal tool."""

    goal_id: str
    title: str
    created_at: datetime
    url: str


class LogJournalEntryOutput(BaseModel):
    """Output schema for log_journal_entry tool."""

    entry_id: str
    transcript: Optional[str] = None
    logged_at: datetime
    memory_meter: int


class MilestoneInfo(BaseModel):
    """Milestone information for goal progress."""

    milestone_id: str
    title: str
    completed: bool


class GoalProgress(BaseModel):
    """Progress information for a single goal."""

    goal_id: str
    title: str
    progress_pct: int
    milestones_hit: int
    milestones_remaining: int
    scaffolding_advice: Optional[str] = None


class CheckProgressOutput(BaseModel):
    """Output schema for check_progress tool."""

    goals: list[GoalProgress]


class GetCompanionResponseOutput(BaseModel):
    """Output schema for get_companion_response tool."""

    response: str
    lang: str
    memory_used: list[str]
    smart_pause_suggested: bool


class SetReminderOutput(BaseModel):
    """Output schema for set_reminder tool."""

    reminder_id: str
    text: str
    fires_at: datetime
    next_fire_at: datetime


# ---------------------------------------------------------------------------
# Voice-shaped tools (Alexa+ and other spoken surfaces). Added October 2026.
# These schemas target the MAITE backend's real routes (/api/goals, /api/checkins,
# /api/notifications/settings), not the /v1 contract the long-form tools above use.
# ---------------------------------------------------------------------------

from typing import Annotated  # noqa: E402

from pydantic import StringConstraints  # noqa: E402

SPOKEN_MAX_CHARS = 220  # one or two spoken sentences; every voice tool result is capped here


class VoiceCheckInInput(BaseModel):
    """Input schema for check_in. No required fields."""

    include_streak: bool = Field(True, description="Mention the check-in streak in the spoken reply")


class LogProgressInput(BaseModel):
    """Input schema for log_progress. Identify the goal by id or by (part of) its name."""

    goal: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)] = Field(
        ..., description="Goal id or part of the goal title, case-insensitive"
    )
    note: Annotated[Optional[str], StringConstraints(strip_whitespace=True, max_length=240)] = Field(
        None, description="Short progress note to attach"
    )
    progress: Annotated[Optional[int], Field(ge=0, le=100)] = Field(
        None, description="New progress percent. Omit to keep the current percent and only add the note"
    )


class JournalMoodInput(BaseModel):
    """Input schema for journal_mood."""

    mood: Mood = Field(..., description="One of awful, low, neutral, good, great")
    note: Annotated[Optional[str], StringConstraints(strip_whitespace=True, max_length=400)] = Field(
        None, description="Optional sentence about why"
    )


class NextReminderInput(BaseModel):
    """Input schema for next_reminder. No required fields."""

    push_endpoint: Annotated[Optional[str], StringConstraints(strip_whitespace=True, max_length=2000)] = Field(
        None,
        description="Push subscription endpoint whose reminder settings to read. Defaults to MAITE_PUSH_ENDPOINT",
    )
