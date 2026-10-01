"""MAITE MCP Client - Async HTTP client for MAITE backend API."""

import os
from typing import Any

import httpx

from .schemas import (
    CreateGoalInput,
    LogJournalEntryInput,
    CheckProgressInput,
    GetCompanionResponseInput,
    SetReminderInput,
)


class MAITEAPIError(Exception):
    """Raised when the MAITE API returns an error."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"MAITE API error ({status_code}): {message}")


class MAITEClient:
    """Async client for the MAITE backend API."""

    def __init__(
        self,
        api_base: str | None = None,
        api_key: str | None = None,
        user_lang: str | None = None,
    ):
        self.api_base = (api_base or os.environ.get("MAITE_API_BASE", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("MAITE_API_KEY", "")
        self.user_lang = user_lang or os.environ.get("MAITE_USER_LANG", "en")

        if not self.api_base:
            raise ValueError("MAITE_API_BASE is required")
        if not self.api_key:
            raise ValueError("MAITE_API_KEY is required")

        self._client = httpx.AsyncClient(
            base_url=self.api_base,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated request to the MAITE API."""
        url = f"{self.api_base}{path}"
        response = await self._client.request(
            method=method,
            url=url,
            json=json,
            params=params,
        )

        if not response.is_success:
            try:
                error_body = response.json()
                message = error_body.get("error", error_body.get("message", response.text))
            except Exception:
                message = response.text or "Unknown error"

            raise MAITEAPIError(status_code=response.status_code, message=message)

        return response.json()

    async def create_goal(self, input_data: CreateGoalInput) -> dict[str, Any]:
        """Create a new goal for the user."""
        payload = input_data.model_dump(exclude_none=True)
        if "lang" not in payload:
            payload["lang"] = self.user_lang
        return await self._request("POST", "/v1/goals", json=payload)

    async def log_journal_entry(self, input_data: LogJournalEntryInput) -> dict[str, Any]:
        """Add a journal entry for the user."""
        payload = input_data.model_dump(exclude_none=True)
        if "lang" not in payload:
            payload["lang"] = self.user_lang
        return await self._request("POST", "/v1/journal", json=payload)

    async def check_progress(
        self, input_data: CheckProgressInput, include_scaffolding_advice: bool = True
    ) -> dict[str, Any]:
        """Check progress on one goal or all active goals."""
        params: dict[str, Any] = {"include_scaffolding_advice": include_scaffolding_advice}

        goal_id = input_data.goal_id
        if goal_id:
            path = f"/v1/goals/{goal_id}/progress"
        else:
            path = "/v1/goals/progress"

        return await self._request("GET", path, params=params)

    async def get_companion_response(self, input_data: GetCompanionResponseInput) -> dict[str, Any]:
        """Send a message to MAITE's companion AI and get a response."""
        payload = input_data.model_dump(exclude_none=True)
        if "lang" not in payload:
            payload["lang"] = self.user_lang
        return await self._request("POST", "/v1/companion/respond", json=payload)

    async def set_reminder(self, input_data: SetReminderInput) -> dict[str, Any]:
        """Set a reminder linked to a goal or standalone."""
        payload = input_data.model_dump(exclude_none=True)
        return await self._request("POST", "/v1/reminders", json=payload)

    # ------------------------------------------------------------------
    # Real MAITE backend routes (server/routes.ts in the MAITE app). Used by the
    # voice-shaped tools. Authentication is the same bearer header; the MAITE
    # backend accepts it through its mobile-auth middleware.
    # ------------------------------------------------------------------

    async def list_goals(self) -> list[dict[str, Any]]:
        """GET /api/goals: every goal for the authenticated user."""
        data = await self._request("GET", "/api/goals")
        return data if isinstance(data, list) else data.get("goals", [])

    async def list_checkins(self) -> list[dict[str, Any]]:
        """GET /api/checkins: every check-in for the authenticated user."""
        data = await self._request("GET", "/api/checkins")
        return data if isinstance(data, list) else data.get("checkins", [])

    async def update_goal_progress(self, goal_id: int | str, progress: int, note: str | None = None) -> dict[str, Any]:
        """PATCH /api/goals/{id}/progress with {progress, noteText}. Returns the updated goal."""
        payload: dict[str, Any] = {"progress": int(progress)}
        if note:
            payload["noteText"] = note
        return await self._request("PATCH", f"/api/goals/{goal_id}/progress", json=payload)

    async def create_checkin(self, content: str) -> dict[str, Any]:
        """POST /api/checkins with {content}. Returns the stored check-in plus MAITE's response."""
        return await self._request("POST", "/api/checkins", json={"content": content})

    async def get_user(self) -> dict[str, Any]:
        """GET /api/user: the authenticated user's record (used for user_timezone)."""
        return await self._request("GET", "/api/user")

    async def get_notification_settings(self, endpoint: str) -> dict[str, Any] | None:
        """GET /api/notifications/settings?endpoint=...: morning and evening nudge settings for one
        push subscription. The backend returns null when the endpoint has no settings."""
        data = await self._request("GET", "/api/notifications/settings", params={"endpoint": endpoint})
        return data if isinstance(data, dict) else None
