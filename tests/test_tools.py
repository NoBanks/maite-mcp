"""Tests for MAITE MCP tools."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

from maite_mcp.schemas import (
    CreateGoalInput,
    LogJournalEntryInput,
    CheckProgressInput,
    GetCompanionResponseInput,
    SetReminderInput,
)
from maite_mcp.client import MAITEClient, MAITEAPIError


def _mock_http_client():
    """Stand-in for httpx.AsyncClient. MAITEClient calls .request(method=..., url=..., ...), while the
    tests configure .post / .get return values, so route .request to the per-method mock and derive
    .is_success from the configured status code. Without this every test got a coroutine back."""
    m = AsyncMock()

    async def _request(method, url, **kwargs):
        resp = await getattr(m, method.lower())(url, **kwargs)
        if isinstance(getattr(resp, "status_code", None), int):
            resp.is_success = 200 <= resp.status_code < 300
        return resp

    m.request = AsyncMock(side_effect=_request)
    return m


class TestCreateGoal:
    """Tests for create_goal tool."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock MAITE client."""
        with patch("maite_mcp.client.httpx.AsyncClient") as mock_client_class:
            mock_client = _mock_http_client()
            mock_client_class.return_value = mock_client
            yield mock_client

    @pytest.fixture
    def client(self, mock_client):
        """Create a MAITEClient with mocked HTTP client."""
        with patch.dict("os.environ", {
            "MAITE_API_BASE": "https://api.hellomaite.com",
            "MAITE_API_KEY": "test-key",
        }):
            client = MAITEClient()
            client._client = mock_client
            return client

    @pytest.mark.asyncio
    async def test_create_goal_success(self, client, mock_client):
        """Test successful goal creation."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "goal_id": "goal_123",
            "title": "Learn Spanish",
            "created_at": "2024-01-15T10:30:00Z",
            "url": "https://api.hellomaite.com/v1/goals/goal_123",
        }
        mock_client.post.return_value = mock_response

        input_data = CreateGoalInput(
            title="Learn Spanish",
            description="Achieve conversational fluency",
            target_date="2024-12-31",
            category="learning",
            lang="en",
        )

        result = await client.create_goal(input_data)

        assert result["goal_id"] == "goal_123"
        assert result["title"] == "Learn Spanish"
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_goal_minimal(self, client, mock_client):
        """Test goal creation with only required fields."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "goal_id": "goal_456",
            "title": "Exercise Daily",
            "created_at": "2024-01-15T10:30:00Z",
            "url": "https://api.hellomaite.com/v1/goals/goal_456",
        }
        mock_client.post.return_value = mock_response

        input_data = CreateGoalInput(title="Exercise Daily")

        result = await client.create_goal(input_data)

        assert result["goal_id"] == "goal_456"
        assert result["title"] == "Exercise Daily"

    @pytest.mark.asyncio
    async def test_create_goal_api_error(self, client, mock_client):
        """Test goal creation with API error."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Invalid request"
        mock_client.post.return_value = mock_response

        input_data = CreateGoalInput(title="Test Goal")

        with pytest.raises(MAITEAPIError) as exc_info:
            await client.create_goal(input_data)

        assert exc_info.value.status_code == 400


class TestLogJournalEntry:
    """Tests for log_journal_entry tool."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock MAITE client."""
        with patch("maite_mcp.client.httpx.AsyncClient") as mock_client_class:
            mock_client = _mock_http_client()
            mock_client_class.return_value = mock_client
            yield mock_client

    @pytest.fixture
    def client(self, mock_client):
        """Create a MAITEClient with mocked HTTP client."""
        with patch.dict("os.environ", {
            "MAITE_API_BASE": "https://api.hellomaite.com",
            "MAITE_API_KEY": "test-key",
        }):
            client = MAITEClient()
            client._client = mock_client
            return client

    @pytest.mark.asyncio
    async def test_log_journal_text_success(self, client, mock_client):
        """Test successful text journal entry."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "entry_id": "entry_123",
            "transcript": None,
            "logged_at": "2024-01-15T10:30:00Z",
            "memory_meter": 75,
        }
        mock_client.post.return_value = mock_response

        input_data = LogJournalEntryInput(
            text="Today was productive. Completed two major tasks.",
            mood="good",
            lang="en",
        )

        result = await client.log_journal_entry(input_data)

        assert result["entry_id"] == "entry_123"
        assert result["memory_meter"] == 75
        assert result["transcript"] is None

    @pytest.mark.asyncio
    async def test_log_journal_audio_success(self, client, mock_client):
        """Test successful audio journal entry."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "entry_id": "entry_456",
            "transcript": "Feeling grateful for today's progress.",
            "logged_at": "2024-01-15T10:30:00Z",
            "memory_meter": 80,
        }
        mock_client.post.return_value = mock_response

        input_data = LogJournalEntryInput(
            audio_url="https://storage.hellomaite.com/audio/journal_123.m4a",
            mood="great",
            lang="en",
        )

        result = await client.log_journal_entry(input_data)

        assert result["entry_id"] == "entry_456"
        assert result["transcript"] == "Feeling grateful for today's progress."
        assert result["memory_meter"] == 80

    @pytest.mark.asyncio
    async def test_log_journal_empty_request(self, client, mock_client):
        """Test journal entry with no content."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Either text or audio_url required"
        mock_client.post.return_value = mock_response

        input_data = LogJournalEntryInput()

        with pytest.raises(MAITEAPIError):
            await client.log_journal_entry(input_data)


class TestCheckProgress:
    """Tests for check_progress tool."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock MAITE client."""
        with patch("maite_mcp.client.httpx.AsyncClient") as mock_client_class:
            mock_client = _mock_http_client()
            mock_client_class.return_value = mock_client
            yield mock_client

    @pytest.fixture
    def client(self, mock_client):
        """Create a MAITEClient with mocked HTTP client."""
        with patch.dict("os.environ", {
            "MAITE_API_BASE": "https://api.hellomaite.com",
            "MAITE_API_KEY": "test-key",
        }):
            client = MAITEClient()
            client._client = mock_client
            return client

    @pytest.mark.asyncio
    async def test_check_progress_single_goal(self, client, mock_client):
        """Test checking progress for a specific goal."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "goals": [
                {
                    "goal_id": "goal_123",
                    "title": "Learn Spanish",
                    "progress_pct": 45,
                    "milestones_hit": ["week_1", "week_2"],
                    "milestones_remaining": ["week_3", "week_4", "final"],
                    "scaffolding_advice": "Try watching a Spanish movie with subtitles.",
                }
            ]
        }
        mock_client.get.return_value = mock_response

        input_data = CheckProgressInput(
            goal_id="goal_123",
            include_scaffolding_advice=True,
        )

        result = await client.check_progress(input_data)

        assert len(result["goals"]) == 1
        assert result["goals"][0]["goal_id"] == "goal_123"
        assert result["goals"][0]["progress_pct"] == 45
        assert len(result["goals"][0]["milestones_hit"]) == 2

    @pytest.mark.asyncio
    async def test_check_progress_all_goals(self, client, mock_client):
        """Test checking progress for all active goals."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "goals": [
                {
                    "goal_id": "goal_123",
                    "title": "Learn Spanish",
                    "progress_pct": 45,
                    "milestones_hit": ["week_1"],
                    "milestones_remaining": ["week_2", "week_3"],
                    "scaffolding_advice": "Practice speaking daily.",
                },
                {
                    "goal_id": "goal_456",
                    "title": "Exercise Daily",
                    "progress_pct": 80,
                    "milestones_hit": ["month_1", "month_2"],
                    "milestones_remaining": ["month_3"],
                    "scaffolding_advice": "You're almost there!",
                },
            ]
        }
        mock_client.get.return_value = mock_response

        input_data = CheckProgressInput(include_scaffolding_advice=True)

        result = await client.check_progress(input_data)

        assert len(result["goals"]) == 2
        assert result["goals"][0]["progress_pct"] == 45
        assert result["goals"][1]["progress_pct"] == 80

    @pytest.mark.asyncio
    async def test_check_progress_without_scaffolding(self, client, mock_client):
        """Test checking progress without scaffolding advice."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "goals": [
                {
                    "goal_id": "goal_123",
                    "title": "Learn Spanish",
                    "progress_pct": 45,
                    "milestones_hit": ["week_1"],
                    "milestones_remaining": ["week_2"],
                    "scaffolding_advice": None,
                }
            ]
        }
        mock_client.get.return_value = mock_response

        input_data = CheckProgressInput(
            goal_id="goal_123",
            include_scaffolding_advice=False,
        )

        result = await client.check_progress(input_data)

        assert result["goals"][0]["scaffolding_advice"] is None


class TestGetCompanionResponse:
    """Tests for get_companion_response tool."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock MAITE client."""
        with patch("maite_mcp.client.httpx.AsyncClient") as mock_client_class:
            mock_client = _mock_http_client()
            mock_client_class.return_value = mock_client
            yield mock_client

    @pytest.fixture
    def client(self, mock_client):
        """Create a MAITEClient with mocked HTTP client."""
        with patch.dict("os.environ", {
            "MAITE_API_BASE": "https://api.hellomaite.com",
            "MAITE_API_KEY": "test-key",
        }):
            client = MAITEClient()
            client._client = mock_client
            return client

    @pytest.mark.asyncio
    async def test_get_companion_response_empathetic(self, client, mock_client):
        """Test getting companion response with empathetic tone."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": "I hear you. It sounds like today was challenging, but you did great.",
            "lang": "en",
            "memory_used": ["recent_progress", "weekly_reflection"],
            "smart_pause_suggested": False,
        }
        mock_client.post.return_value = mock_response

        input_data = GetCompanionResponseInput(
            user_message="I had a tough day but managed to finish my tasks.",
            context_hint="Productivity struggle",
            lang="en",
            tone="empathetic",
        )

        result = await client.get_companion_response(input_data)

        assert "I hear you" in result["response"]
        assert result["lang"] == "en"
        assert len(result["memory_used"]) == 2
        assert result["smart_pause_suggested"] is False

    @pytest.mark.asyncio
    async def test_get_companion_response_challenging(self, client, mock_client):
        """Test getting companion response with challenging tone."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": "What would it take for you to push past this plateau?",
            "lang": "en",
            "memory_used": ["goal_blocking"],
            "smart_pause_suggested": True,
        }
        mock_client.post.return_value = mock_response

        input_data = GetCompanionResponseInput(
            user_message="I'm stuck on my learning goal.",
            tone="challenging",
        )

        result = await client.get_companion_response(input_data)

        assert "plateau" in result["response"]
        assert result["smart_pause_suggested"] is True

    @pytest.mark.asyncio
    async def test_get_companion_response_multilingual(self, client, mock_client):
        """Test getting companion response in different language."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": "¡Hola! Parece que estás progresando bien.",
            "lang": "es",
            "memory_used": ["spanish_practice"],
            "smart_pause_suggested": False,
        }
        mock_client.post.return_value = mock_response

        input_data = GetCompanionResponseInput(
            user_message="Hola, cómo estoy progresando?",
            lang="es",
            tone="empathetic",
        )

        result = await client.get_companion_response(input_data)

        assert result["lang"] == "es"
        assert "¡Hola!" in result["response"]


class TestSetReminder:
    """Tests for set_reminder tool."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock MAITE client."""
        with patch("maite_mcp.client.httpx.AsyncClient") as mock_client_class:
            mock_client = _mock_http_client()
            mock_client_class.return_value = mock_client
            yield mock_client

    @pytest.fixture
    def client(self, mock_client):
        """Create a MAITEClient with mocked HTTP client."""
        with patch.dict("os.environ", {
            "MAITE_API_BASE": "https://api.hellomaite.com",
            "MAITE_API_KEY": "test-key",
        }):
            client = MAITEClient()
            client._client = mock_client
            return client

    @pytest.mark.asyncio
    async def test_set_reminder_success(self, client, mock_client):
        """Test successful reminder creation."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "reminder_id": "rem_123",
            "text": "Time to practice Spanish vocabulary",
            "fires_at": "2024-01-20T09:00:00Z",
            "next_fire_at": "2024-01-20T09:00:00Z",
        }
        mock_client.post.return_value = mock_response

        input_data = SetReminderInput(
            text="Time to practice Spanish vocabulary",
            fires_at="2024-01-20T09:00:00Z",
            repeat="none",
        )

        result = await client.set_reminder(input_data)

        assert result["reminder_id"] == "rem_123"
        assert result["text"] == "Time to practice Spanish vocabulary"
        assert result["next_fire_at"] == "2024-01-20T09:00:00Z"

    @pytest.mark.asyncio
    async def test_set_reminder_linked_to_goal(self, client, mock_client):
        """Test reminder linked to a specific goal."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "reminder_id": "rem_456",
            "text": "Weekly check-in: Learn Spanish progress",
            "fires_at": "2024-01-22T10:00:00Z",
            "next_fire_at": "2024-01-29T10:00:00Z",
        }
        mock_client.post.return_value = mock_response

        input_data = SetReminderInput(
            text="Weekly check-in: Learn Spanish progress",
            fires_at="2024-01-22T10:00:00Z",
            goal_id="goal_123",
            repeat="weekly",
        )

        result = await client.set_reminder(input_data)

        assert result["reminder_id"] == "rem_456"
        assert result["next_fire_at"] == "2024-01-29T10:00:00Z"

    @pytest.mark.asyncio
    async def test_set_reminder_daily_repeat(self, client, mock_client):
        """Test daily recurring reminder."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "reminder_id": "rem_789",
            "text": "Morning meditation",
            "fires_at": "2024-01-15T07:00:00Z",
            "next_fire_at": "2024-01-16T07:00:00Z",
        }
        mock_client.post.return_value = mock_response

        input_data = SetReminderInput(
            text="Morning meditation",
            fires_at="2024-01-15T07:00:00Z",
            repeat="daily",
        )

        result = await client.set_reminder(input_data)

        assert result["reminder_id"] == "rem_789"
        assert "07:00:00" in result["next_fire_at"]

    @pytest.mark.asyncio
    async def test_set_reminder_api_error(self, client, mock_client):
        """Test reminder creation with API error."""
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = "Invalid datetime format"
        mock_client.post.return_value = mock_response

        # The schema validates fires_at before any request, so an invalid string never reaches the
        # API; use a valid datetime and let the mocked 422 exercise the error path.
        input_data = SetReminderInput(
            text="Test reminder",
            fires_at="2024-01-15T07:00:00Z",
        )

        with pytest.raises(MAITEAPIError) as exc_info:
            await client.set_reminder(input_data)

        assert exc_info.value.status_code == 422


class TestInputValidation:
    """Tests for input schema validation."""

    def test_create_goal_title_max_length(self):
        """Test title max length validation."""
        with pytest.raises(ValueError):
            CreateGoalInput(title="x" * 121)

    def test_create_goal_valid_categories(self):
        """Test valid category values."""
        valid_categories = ["health", "career", "relationships", "creative", 
                          "financial", "spiritual", "learning", "other"]
        for category in valid_categories:
            goal = CreateGoalInput(title="Test", category=category)
            assert goal.category == category

    def test_create_goal_invalid_category(self):
        """Test invalid category value."""
        with pytest.raises(ValueError):
            CreateGoalInput(title="Test", category="invalid")

    def test_log_journal_mood_values(self):
        """Test valid mood values."""
        valid_moods = ["awful", "low", "neutral", "good", "great"]
        for mood in valid_moods:
            entry = LogJournalEntryInput(text="Test", mood=mood)
            assert entry.mood == mood

    def test_log_journal_invalid_mood(self):
        """Test invalid mood value."""
        with pytest.raises(ValueError):
            LogJournalEntryInput(text="Test", mood="happy")

    def test_companion_tone_values(self):
        """Test valid tone values."""
        valid_tones = ["empathetic", "challenging", "playful", "practical"]
        for tone in valid_tones:
            input_data = GetCompanionResponseInput(
                user_message="Test",
                tone=tone,
            )
            assert input_data.tone == tone

    def test_companion_invalid_tone(self):
        """Test invalid tone value."""
        with pytest.raises(ValueError):
            GetCompanionResponseInput(
                user_message="Test",
                tone="serious",
            )

    def test_reminder_repeat_values(self):
        """Test valid repeat values."""
        valid_repeats = ["none", "daily", "weekly", "monthly"]
        for repeat in valid_repeats:
            reminder = SetReminderInput(
                text="Test",
                fires_at="2024-01-15T10:00:00Z",
                repeat=repeat,
            )
            assert reminder.repeat == repeat

    def test_reminder_invalid_repeat(self):
        """Test invalid repeat value."""
        with pytest.raises(ValueError):
            SetReminderInput(
                text="Test",
                fires_at="2024-01-15T10:00:00Z",
                repeat="yearly",
            )

    def test_reminder_text_max_length(self):
        """Test text max length validation."""
        with pytest.raises(ValueError):
            SetReminderInput(
                text="x" * 241,
                fires_at="2024-01-15T10:00:00Z",
            )

    def test_companion_message_max_length(self):
        """Test user_message max length validation."""
        with pytest.raises(ValueError):
            GetCompanionResponseInput(user_message="x" * 4001)
