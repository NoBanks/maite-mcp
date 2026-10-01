"""Voice-shaped tools for spoken surfaces (Alexa+ first). Added October 2026.

Every handler here returns ONE string of one or two sentences, under SPOKEN_MAX_CHARS, with no
newlines, no markdown and numbers spelled the way a person says them. These tools call the MAITE
backend's real routes (server/routes.ts in the MAITE app):

  check_in       GET  /api/goals  +  GET /api/checkins
  log_progress   GET  /api/goals  ->  PATCH /api/goals/{id}/progress  {progress, noteText}
  journal_mood   POST /api/checkins  {content}
  next_reminder  GET  /api/user  +  GET /api/notifications/settings?endpoint=...

MAITE has no reminders table. Its only scheduled nudges are the morning and evening check-in push
notifications stored per push subscription, so next_reminder reads those. See README, "Known limits".
"""

from __future__ import annotations

import datetime as dt
import os
import re
from typing import Any
from zoneinfo import ZoneInfo

from .client import MAITEClient
from .schemas import SPOKEN_MAX_CHARS, JournalMoodInput, LogProgressInput, NextReminderInput, VoiceCheckInInput

ACTIVE_STATUSES = {"in_progress", "active"}

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
         "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def spell(n: int) -> str:
    """Spell an integer 0 to 999 the way it is said aloud. Larger values fall back to digits."""
    n = int(n)
    if n < 0:
        return "minus " + spell(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _TENS[t] + ("" if o == 0 else " " + _ONES[o])
    if n < 1000:
        h, r = divmod(n, 100)
        return _ONES[h] + " hundred" + ("" if r == 0 else " " + spell(r))
    return str(n)


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{spell(n)} {one if n == 1 else (many or one + 's')}"


_MARKDOWN = re.compile(r"[*_`#>|\[\]]")


def spoken(text: str) -> str:
    """Enforce the spoken contract: single line, no markdown characters, capped length, ends with a period."""
    t = _MARKDOWN.sub("", text)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > SPOKEN_MAX_CHARS:
        cut = t[: SPOKEN_MAX_CHARS - 1]
        # prefer ending on a sentence, else a word
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        cut = cut[: end + 1] if end > SPOKEN_MAX_CHARS // 2 else cut[: cut.rfind(" ")]
        t = cut.rstrip(" ,;:")
    if t and t[-1] not in ".!?":
        t += "."
    return t


def _parse_ts(v: Any) -> dt.datetime | None:
    if not v:
        return None
    try:
        s = str(v).replace("Z", "+00:00")
        d = dt.datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def streak_days(checkins: list[dict[str, Any]], today: dt.date | None = None, tz: dt.tzinfo | None = None) -> int:
    """Consecutive calendar days with at least one check-in, counting back from today or yesterday.
    Dates are taken in the user's timezone when known, otherwise UTC."""
    tz = tz or dt.timezone.utc
    days = {d.astimezone(tz).date() for d in (_parse_ts(c.get("created_at")) for c in checkins) if d}
    if not days:
        return 0
    today = today or dt.datetime.now(tz).date()
    cur = today if today in days else today - dt.timedelta(days=1)
    if cur not in days:
        return 0
    n = 0
    while cur in days:
        n += 1
        cur -= dt.timedelta(days=1)
    return n


def _user_tz(user: dict[str, Any] | None) -> dt.tzinfo:
    name = (user or {}).get("user_timezone")
    if name:
        try:
            return ZoneInfo(name)
        except Exception:  # unknown zone name on this machine
            pass
    return dt.timezone.utc


def _active(goals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [g for g in goals if str(g.get("status", "in_progress")).lower() in ACTIVE_STATUSES]


def _pct(g: dict[str, Any]) -> int:
    try:
        return max(0, min(100, int(g.get("progress") or 0)))
    except (TypeError, ValueError):
        return 0


async def check_in(client: MAITEClient, args: VoiceCheckInInput) -> str:
    goals = _active(await client.list_goals())
    parts: list[str] = []
    if not goals:
        parts.append("You have no active goals right now.")
    else:
        lead = max(goals, key=_pct)
        parts.append(f"You have {plural(len(goals), 'active goal')}.")
        parts.append(f"{lead.get('title', 'Your top goal')} is furthest along at {spell(_pct(lead))} percent.")
    if args.include_streak:
        user = None
        try:
            user = await client.get_user()
        except Exception:
            user = None
        n = streak_days(await client.list_checkins(), tz=_user_tz(user))
        parts.append("No check-in streak yet, today is a good day to start." if n == 0
                     else f"Your check-in streak is {plural(n, 'day')}.")
    return spoken(" ".join(parts))


def resolve_goal(goals: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    """Match by exact id first, then by case-insensitive title containment. Ambiguous = None."""
    k = key.strip().lower()
    for g in goals:
        if str(g.get("id", "")).lower() == k:
            return g
    exact = [g for g in goals if str(g.get("title", "")).strip().lower() == k]
    if len(exact) == 1:
        return exact[0]
    hits = [g for g in goals if k in str(g.get("title", "")).lower()]
    return hits[0] if len(hits) == 1 else None


async def log_progress(client: MAITEClient, args: LogProgressInput) -> str:
    goals = await client.list_goals()
    g = resolve_goal(goals, args.goal)
    if g is None:
        hits = [x for x in goals if args.goal.strip().lower() in str(x.get("title", "")).lower()]
        if len(hits) > 1:
            names = " or ".join(str(h.get("title")) for h in hits[:2])
            return spoken(f"I found more than one goal like that. Did you mean {names}?")
        return spoken(f"I could not find a goal called {args.goal}.")
    pct = _pct(g) if args.progress is None else int(args.progress)
    updated = await client.update_goal_progress(g.get("id"), pct, args.note)
    new_pct = _pct(updated) if isinstance(updated, dict) and "progress" in updated else pct
    title = str(g.get("title", "that goal"))
    tail = " Note saved." if args.note else ""
    if new_pct >= 100:
        return spoken(f"Logged. {title} is complete, nice work.{tail}")
    return spoken(f"Logged. {title} is at {spell(new_pct)} percent.{tail}")


async def journal_mood(client: MAITEClient, args: JournalMoodInput) -> str:
    content = f"Mood check-in: feeling {args.mood.value}."
    if args.note:
        content += f" {args.note}"
    await client.create_checkin(content)
    mood = args.mood.value
    ack = {
        "awful": "I am sorry today feels awful. I logged it, and I am here when you want to talk.",
        "low": "Noted that you are feeling low today. Logged, and be gentle with yourself.",
        "neutral": "Got it, a neutral day. Logged.",
        "good": "Glad you are feeling good today. Logged.",
        "great": "Love that you are feeling great today. Logged.",
    }[mood]
    return spoken(ack)


def _next_fire(now: dt.datetime, times: list[tuple[str, str | None, bool]]) -> tuple[str, dt.datetime] | None:
    """times: (label, 'HH:MM[:SS]', enabled). Returns the soonest enabled fire time at or after now."""
    best: tuple[str, dt.datetime] | None = None
    for label, hhmm, enabled in times:
        if not enabled or not hhmm:
            continue
        try:
            h, m = int(str(hhmm)[:2]), int(str(hhmm)[3:5])
        except ValueError:
            continue
        cand = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if cand <= now:
            cand += dt.timedelta(days=1)
        if best is None or cand < best[1]:
            best = (label, cand)
    return best


def _say_time(d: dt.datetime) -> str:
    h12 = d.hour % 12 or 12
    ampm = "in the morning" if d.hour < 12 else ("in the afternoon" if d.hour < 17 else "in the evening")
    return f"{spell(h12)}" + ("" if d.minute == 0 else f" {spell(d.minute):s}") + f" {ampm}"


async def next_reminder(client: MAITEClient, args: NextReminderInput) -> str:
    endpoint = args.push_endpoint or os.environ.get("MAITE_PUSH_ENDPOINT")
    if not endpoint:
        return spoken("You do not have a reminder set up here. Turn on the morning or evening check-in nudge in the MAITE app.")
    user = None
    try:
        user = await client.get_user()
    except Exception:
        user = None
    tz = _user_tz(user)
    settings = await client.get_notification_settings(endpoint)
    if not settings:
        return spoken("No reminders are scheduled on this device yet. You can turn on a daily check-in nudge in the MAITE app.")
    now = dt.datetime.now(tz)
    nxt = _next_fire(now, [
        ("morning check-in", settings.get("notification_time"), bool(settings.get("notifications_enabled"))),
        ("evening check-in", settings.get("evening_notification_time"), bool(settings.get("evening_notifications_enabled"))),
    ])
    if nxt is None:
        return spoken("Your check-in reminders are turned off right now. You can switch them on in the MAITE app.")
    label, when = nxt
    day = "today" if when.date() == now.date() else "tomorrow"
    return spoken(f"Your next reminder is the {label} {day} at {_say_time(when)}.")
