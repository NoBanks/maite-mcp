"""Voice-shaped tools: driven through the server's own list_tools / call_tool functions (awaited
directly, never server.list_tools(), because mcp Server decorators register handlers). The MAITE
client is replaced by a fake that answers the real backend routes' shapes."""

import datetime as dt
import re

import pytest

from maite_mcp import server as S, voice as V
from maite_mcp.schemas import SPOKEN_MAX_CHARS, LogProgressInput, Mood

VOICE_TOOLS = {"check_in", "log_progress", "journal_mood", "next_reminder"}
MARKDOWN = re.compile(r"[*_`#>|\[\]]|\n")


class FakeClient:
    def __init__(self, goals=None, checkins=None, user=None, settings=None):
        self.goals = goals if goals is not None else []
        self.checkins = checkins if checkins is not None else []
        self.user = user or {"id": 1, "user_timezone": "America/Los_Angeles"}
        self.settings = settings
        self.patched = []
        self.posted = []

    async def list_goals(self):
        return self.goals

    async def list_checkins(self):
        return self.checkins

    async def get_user(self):
        return self.user

    async def update_goal_progress(self, goal_id, progress, note=None):
        self.patched.append((goal_id, progress, note))
        g = next(x for x in self.goals if x["id"] == goal_id)
        g = dict(g, progress=progress)
        return g

    async def create_checkin(self, content):
        self.posted.append(content)
        return {"id": 9, "content": content, "response": "Thanks for checking in.", "created_at": "2026-10-01T15:00:00Z"}

    async def get_notification_settings(self, endpoint):
        return self.settings


def _iso(d: dt.date) -> str:
    return dt.datetime(d.year, d.month, d.day, 18, 0, tzinfo=dt.timezone.utc).isoformat()


@pytest.fixture
def fake(monkeypatch):
    today = dt.datetime.now(dt.timezone.utc).date()
    goals = [
        {"id": 11, "title": "Learn Spanish", "status": "in_progress", "progress": 45},
        {"id": 12, "title": "Run three times a week", "status": "in_progress", "progress": 80},
        {"id": 13, "title": "Old archived thing", "status": "archived", "progress": 100},
    ]
    checkins = [{"id": i, "content": "x", "created_at": _iso(today - dt.timedelta(days=i))} for i in range(4)]
    c = FakeClient(goals=goals, checkins=checkins, settings={
        "notifications_enabled": True, "notification_time": "08:30:00",
        "evening_notifications_enabled": True, "evening_notification_time": "20:00:00"})
    monkeypatch.setattr(S, "get_client", lambda: c)
    monkeypatch.setenv("MAITE_PUSH_ENDPOINT", "https://push.example.test/sub/abc")
    return c


def _text(result):
    assert len(result) == 1 and result[0].type == "text"
    return result[0].text


def _assert_spoken(t: str):
    assert 0 < len(t) < SPOKEN_MAX_CHARS, t
    assert not MARKDOWN.search(t), t
    assert not re.search(r"\d", t), f"digits should be spelled: {t}"
    assert t[-1] in ".!?"


@pytest.mark.asyncio
async def test_voice_tools_are_listed_with_schemas():
    tools = await S.list_tools()
    names = {t.name for t in tools}
    assert VOICE_TOOLS <= names
    by = {t.name: t for t in tools}
    assert by["log_progress"].inputSchema["required"] == ["goal"]
    assert by["journal_mood"].inputSchema["properties"]["mood"]["enum"] == ["awful", "low", "neutral", "good", "great"]
    assert by["check_in"].inputSchema["required"] == []


@pytest.mark.asyncio
async def test_check_in_counts_active_goals_and_streak(fake):
    t = _text(await S.call_tool("check_in", {}))
    _assert_spoken(t)
    assert "two active goals" in t
    assert "Run three times a week" in t and "eighty percent" in t
    assert "four days" in t


@pytest.mark.asyncio
async def test_check_in_without_streak_or_goals(fake):
    fake.goals = []
    fake.checkins = []
    t = _text(await S.call_tool("check_in", {"include_streak": False}))
    _assert_spoken(t)
    assert "no active goals" in t and "streak" not in t


@pytest.mark.asyncio
async def test_log_progress_by_name_with_note_patches_real_route(fake):
    t = _text(await S.call_tool("log_progress", {"goal": "spanish", "note": "Finished unit four", "progress": 50}))
    _assert_spoken(t)
    assert fake.patched == [(11, 50, "Finished unit four")]
    assert "Learn Spanish" in t and "fifty percent" in t and "Note saved" in t


@pytest.mark.asyncio
async def test_log_progress_by_id_keeps_current_percent(fake):
    t = _text(await S.call_tool("log_progress", {"goal": "12", "note": "ran today"}))
    _assert_spoken(t)
    assert fake.patched == [(12, 80, "ran today")]


@pytest.mark.asyncio
async def test_log_progress_unknown_and_ambiguous(fake):
    t = _text(await S.call_tool("log_progress", {"goal": "piano"}))
    _assert_spoken(t)
    assert "could not find" in t and fake.patched == []
    fake.goals.append({"id": 14, "title": "Spanish cooking", "status": "active", "progress": 5})
    t = _text(await S.call_tool("log_progress", {"goal": "spanish"}))
    _assert_spoken(t)
    assert "more than one" in t and fake.patched == []


@pytest.mark.asyncio
async def test_log_progress_complete_wording(fake):
    t = _text(await S.call_tool("log_progress", {"goal": "Learn Spanish", "progress": 100}))
    _assert_spoken(t)
    assert "complete" in t


@pytest.mark.asyncio
async def test_journal_mood_posts_checkin(fake):
    t = _text(await S.call_tool("journal_mood", {"mood": "low", "note": "Rough night."}))
    _assert_spoken(t)
    assert fake.posted == ["Mood check-in: feeling low. Rough night."]
    assert "low" in t
    for m in Mood:
        _assert_spoken(_text(await S.call_tool("journal_mood", {"mood": m.value})))


@pytest.mark.asyncio
async def test_journal_mood_rejects_unknown_mood(fake):
    t = _text(await S.call_tool("journal_mood", {"mood": "happy"}))
    assert t.startswith("Error:") and fake.posted == []


@pytest.mark.asyncio
async def test_next_reminder_picks_soonest_enabled(fake, monkeypatch):
    fixed = dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.timezone.utc)

    class _Now(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed

    monkeypatch.setattr(V.dt, "datetime", _Now)
    fake.user = {"id": 1, "user_timezone": "UTC"}
    t = _text(await S.call_tool("next_reminder", {}))
    _assert_spoken(t)
    assert "evening check-in today at eight in the evening" in t


@pytest.mark.asyncio
async def test_next_reminder_without_endpoint_or_settings(fake, monkeypatch):
    monkeypatch.delenv("MAITE_PUSH_ENDPOINT", raising=False)
    t = _text(await S.call_tool("next_reminder", {}))
    _assert_spoken(t)
    assert "do not have a reminder" in t
    fake.settings = None
    t = _text(await S.call_tool("next_reminder", {"push_endpoint": "https://push.example.test/x"}))
    _assert_spoken(t)
    assert "No reminders are scheduled" in t
    fake.settings = {"notifications_enabled": False, "notification_time": "08:00:00",
                     "evening_notifications_enabled": False, "evening_notification_time": None}
    t = _text(await S.call_tool("next_reminder", {"push_endpoint": "https://push.example.test/x"}))
    _assert_spoken(t)
    assert "turned off" in t


def test_spoken_contract_helpers():
    assert V.spell(0) == "zero" and V.spell(17) == "seventeen" and V.spell(45) == "forty five"
    assert V.spell(100) == "one hundred" and V.spell(999) == "nine hundred ninety nine"
    long = "Sentence one is here. " * 30
    out = V.spoken(long + "**bold** # heading\nline")
    assert len(out) < SPOKEN_MAX_CHARS and "\n" not in out and "*" not in out and "#" not in out
    assert out.endswith(".")


def test_streak_counts_consecutive_days_only():
    today = dt.date(2026, 10, 1)
    mk = lambda days: [{"created_at": _iso(today - dt.timedelta(days=d))} for d in days]
    assert V.streak_days(mk([0, 1, 2]), today=today) == 3
    assert V.streak_days(mk([1, 2]), today=today) == 2  # yesterday counts, streak alive
    assert V.streak_days(mk([2, 3]), today=today) == 0
    assert V.streak_days([], today=today) == 0


def test_log_progress_input_constraints():
    with pytest.raises(ValueError):
        LogProgressInput(goal="   ")
    with pytest.raises(ValueError):
        LogProgressInput(goal="x", progress=101)
