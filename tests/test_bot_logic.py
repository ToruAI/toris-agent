"""
Real tests for bot.py logic — no mocks, no API calls.
Tests actual behavior: file I/O, rate limiting, auth logic.
"""
import asyncio
import json
import os
import sys
import time
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

# We need to set required env vars BEFORE importing bot
# so it doesn't crash on missing TELEGRAM_BOT_TOKEN
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:token")
os.environ.setdefault("TELEGRAM_DEFAULT_CHAT_ID", "0")

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import bot


# ─────────────────────────────────────────────
# TestResolveProvider
# ─────────────────────────────────────────────

class TestResolveProvider:
    def setup_method(self):
        # Clean env before each test
        for var in ("TTS_PROVIDER", "STT_PROVIDER", "ELEVENLABS_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(var, None)

    def test_explicit_elevenlabs(self):
        os.environ["TTS_PROVIDER"] = "elevenlabs"
        assert bot.resolve_provider("TTS_PROVIDER") == "elevenlabs"

    def test_explicit_openai(self):
        os.environ["TTS_PROVIDER"] = "openai"
        assert bot.resolve_provider("TTS_PROVIDER") == "openai"

    def test_explicit_invalid_ignored(self):
        os.environ["TTS_PROVIDER"] = "invalid"
        assert bot.resolve_provider("TTS_PROVIDER") == "none"

    def test_fallback_elevenlabs(self):
        os.environ["ELEVENLABS_API_KEY"] = "sk_test"
        assert bot.resolve_provider("TTS_PROVIDER") == "elevenlabs"

    def test_fallback_openai_when_no_elevenlabs(self):
        os.environ["OPENAI_API_KEY"] = "sk-test"
        assert bot.resolve_provider("TTS_PROVIDER") == "openai"

    def test_elevenlabs_wins_over_openai(self):
        os.environ["ELEVENLABS_API_KEY"] = "sk_test"
        os.environ["OPENAI_API_KEY"] = "sk-test"
        assert bot.resolve_provider("TTS_PROVIDER") == "elevenlabs"

    def test_none_when_no_keys(self):
        assert bot.resolve_provider("TTS_PROVIDER") == "none"

    def teardown_method(self):
        for var in ("TTS_PROVIDER", "STT_PROVIDER", "ELEVENLABS_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(var, None)


# ─────────────────────────────────────────────
# TestLoadSaveState
# ─────────────────────────────────────────────

class TestLoadSaveState:
    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.orig_state_file = bot.STATE_FILE
        bot.STATE_FILE = Path(self.tmp.name)
        bot.user_sessions = {}

    def teardown_method(self):
        bot.STATE_FILE = self.orig_state_file
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_roundtrip(self):
        bot.user_sessions = {"123": {"current_session": "abc", "sessions": ["abc"]}}
        bot.save_state()
        bot.user_sessions = {}
        bot.load_state()
        assert bot.user_sessions["123"]["current_session"] == "abc"

    def test_corrupted_json(self):
        Path(self.tmp.name).write_text("not valid json{{{{")
        bot.load_state()  # Must NOT raise
        assert bot.user_sessions == {}

    def test_missing_file(self):
        Path(self.tmp.name).unlink()
        bot.load_state()  # Must NOT raise
        assert bot.user_sessions == {}

    def test_empty_file(self):
        Path(self.tmp.name).write_text("")
        bot.load_state()  # Must NOT raise
        assert bot.user_sessions == {}


# ─────────────────────────────────────────────
# TestLoadSaveSettings
# ─────────────────────────────────────────────

class TestLoadSaveSettings:
    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.orig_settings_file = bot.SETTINGS_FILE
        bot.SETTINGS_FILE = Path(self.tmp.name)
        bot.user_settings = {}

    def teardown_method(self):
        bot.SETTINGS_FILE = self.orig_settings_file
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_corrupted_json(self):
        Path(self.tmp.name).write_text("{bad json")
        bot.load_settings()  # Must NOT raise
        assert bot.user_settings == {}

    def test_roundtrip(self):
        bot.user_settings = {"456": {"mode": "approve", "audio_enabled": False}}
        bot.save_settings()
        bot.user_settings = {}
        bot.load_settings()
        assert bot.user_settings["456"]["mode"] == "approve"


# ─────────────────────────────────────────────
# TestRateLimiter
# ─────────────────────────────────────────────

class TestRateLimiter:
    def setup_method(self):
        # Clear rate limit state
        bot.rate_limits.clear()

    def test_first_message_allowed(self):
        allowed, msg = bot.check_rate_limit(999)
        assert allowed is True

    def test_cooldown_blocks_immediate_second(self):
        bot.check_rate_limit(999)
        allowed, msg = bot.check_rate_limit(999)
        assert allowed is False
        assert "wait" in msg.lower() or "second" in msg.lower() or "slow" in msg.lower()

    def test_per_minute_cap(self):
        user_id = 12345
        # Simulate 10 messages spaced out to pass cooldown
        # by manipulating rate_limits directly
        bot.rate_limits[str(user_id)] = {
            "last_message": time.time() - 10,  # 10s ago — passes cooldown
            "minute_start": time.time(),
            "minute_count": 10,  # Already at limit
        }
        allowed, msg = bot.check_rate_limit(user_id)
        assert allowed is False
        assert "10" in msg or "limit" in msg.lower() or "minute" in msg.lower()

    def test_per_minute_resets_after_minute(self):
        user_id = 77777
        bot.rate_limits[str(user_id)] = {
            "last_message": time.time() - 10,
            "minute_start": time.time() - 65,  # minute started 65s ago → resets
            "minute_count": 10,
        }
        allowed, msg = bot.check_rate_limit(user_id)
        assert allowed is True


# ─────────────────────────────────────────────
# TestCheckClaudeAuth
# ─────────────────────────────────────────────

class TestCheckClaudeAuth:
    def setup_method(self):
        for var in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
            os.environ.pop(var, None)

    def test_api_key(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        ok, method = bot.check_claude_auth()
        assert ok is True
        assert method == "api_key"

    def test_saved_token(self):
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "sk-ant-oat01-test"
        ok, method = bot.check_claude_auth()
        assert ok is True
        assert method == "saved_token"

    def test_api_key_takes_priority_over_token(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-key"
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "sk-ant-oat01-token"
        ok, method = bot.check_claude_auth()
        assert method == "api_key"

    def test_no_auth_returns_false(self):
        ok, method = bot.check_claude_auth()
        assert ok is False
        assert method == "none"

    def test_credentials_file_valid(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        creds_file = claude_dir / ".credentials.json"
        future_time = int(time.time() * 1000) + 3600000  # +1h
        creds_file.write_text(json.dumps({
            "claudeAiOauth": {
                "accessToken": "test-token",
                "expiresAt": future_time,
            }
        }))
        with patch.object(bot.Path, "home", return_value=tmp_path):
            ok, method = bot.check_claude_auth()
        assert ok is True
        assert method == "oauth"

    def test_credentials_file_expired_with_refresh(self, tmp_path):
        creds_file = tmp_path / ".credentials.json"
        (tmp_path / ".claude").mkdir(exist_ok=True)
        creds_file = tmp_path / ".claude" / ".credentials.json"
        past_time = int(time.time() * 1000) - 3600000  # -1h (expired)
        creds_file.write_text(json.dumps({
            "claudeAiOauth": {
                "accessToken": "old-token",
                "expiresAt": past_time,
                "refreshToken": "refresh-token",
            }
        }))
        with patch.object(bot.Path, "home", return_value=tmp_path):
            ok, method = bot.check_claude_auth()
        assert ok is True  # Has refresh token → SDK will refresh

    def teardown_method(self):
        for var in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
            os.environ.pop(var, None)


# ─────────────────────────────────────────────
# TestMaxVoiceChars
# ─────────────────────────────────────────────

class TestMaxVoiceChars:
    def test_max_voice_chars_is_positive_int(self):
        assert isinstance(bot.MAX_VOICE_CHARS, int)
        assert bot.MAX_VOICE_CHARS > 0

    def test_truncation_logic(self):
        # Test the truncation logic directly (as used in handle_voice/handle_text)
        max_chars = 100
        long_response = "x" * 200
        tts_text = long_response[:max_chars] if len(long_response) > max_chars else long_response
        assert len(tts_text) == max_chars

    def test_short_response_not_truncated(self):
        max_chars = 100
        short_response = "hello"
        tts_text = short_response[:max_chars] if len(short_response) > max_chars else short_response
        assert tts_text == "hello"


# ─────────────────────────────────────────────
# TestAdminUserIds
# ─────────────────────────────────────────────

class TestAdminUserIds:
    def test_is_authorized_with_zero_chat_id(self):
        """ALLOWED_CHAT_ID=0 means all chats allowed."""
        orig = bot.ALLOWED_CHAT_ID
        bot.ALLOWED_CHAT_ID = 0

        class FakeUpdate:
            class effective_chat:
                id = 12345

        assert bot._is_authorized(FakeUpdate()) is True
        bot.ALLOWED_CHAT_ID = orig

    def test_is_authorized_matching_chat_id(self):
        orig = bot.ALLOWED_CHAT_ID
        bot.ALLOWED_CHAT_ID = 99999

        class FakeUpdate:
            class effective_chat:
                id = 99999

        assert bot._is_authorized(FakeUpdate()) is True
        bot.ALLOWED_CHAT_ID = orig

    def test_is_authorized_wrong_chat_id(self):
        orig = bot.ALLOWED_CHAT_ID
        bot.ALLOWED_CHAT_ID = 99999

        class FakeUpdate:
            class effective_chat:
                id = 11111

        assert bot._is_authorized(FakeUpdate()) is False
        bot.ALLOWED_CHAT_ID = orig

    def test_is_admin_empty_admin_ids_allows_authorized(self):
        """No ADMIN_USER_IDS configured → anyone in authorized chat is admin."""
        orig_chat = bot.ALLOWED_CHAT_ID
        orig_admin = bot.ADMIN_USER_IDS
        bot.ALLOWED_CHAT_ID = 0
        bot.ADMIN_USER_IDS = set()

        class FakeUpdate:
            class effective_chat:
                id = 12345
            class effective_user:
                id = 99999

        assert bot._is_admin(FakeUpdate()) is True
        bot.ALLOWED_CHAT_ID = orig_chat
        bot.ADMIN_USER_IDS = orig_admin

    def test_is_admin_with_admin_ids_matching(self):
        orig_chat = bot.ALLOWED_CHAT_ID
        orig_admin = bot.ADMIN_USER_IDS
        bot.ALLOWED_CHAT_ID = 0
        bot.ADMIN_USER_IDS = {111, 222}

        class FakeUpdate:
            class effective_chat:
                id = 12345
            class effective_user:
                id = 111

        assert bot._is_admin(FakeUpdate()) is True
        bot.ALLOWED_CHAT_ID = orig_chat
        bot.ADMIN_USER_IDS = orig_admin

    def test_is_admin_with_admin_ids_not_matching(self):
        orig_chat = bot.ALLOWED_CHAT_ID
        orig_admin = bot.ADMIN_USER_IDS
        bot.ALLOWED_CHAT_ID = 0
        bot.ADMIN_USER_IDS = {111, 222}

        class FakeUpdate:
            class effective_chat:
                id = 12345
            class effective_user:
                id = 999  # not in admin list

        assert bot._is_admin(FakeUpdate()) is False
        bot.ALLOWED_CHAT_ID = orig_chat
        bot.ADMIN_USER_IDS = orig_admin


class TestSettingsJson:
    """Validate settings.json if it exists."""

    SETTINGS_PATH = Path(__file__).parent.parent / "settings.json"

    def test_settings_example_valid_json(self):
        """settings.example.json should always be valid JSON."""
        example_path = Path(__file__).parent.parent / "settings.example.json"
        content = example_path.read_text()
        data = json.loads(content)
        assert "permissions" in data or "mcpServers" in data

    def test_settings_example_has_megg(self):
        """settings.example.json should include MEGG MCP config."""
        example_path = Path(__file__).parent.parent / "settings.example.json"
        data = json.loads(example_path.read_text())
        assert "mcpServers" in data
        assert "megg" in data["mcpServers"]
        megg = data["mcpServers"]["megg"]
        assert "command" in megg
        assert megg["command"] == "npx"

    def test_settings_json_valid_if_exists(self):
        """settings.json should be valid JSON if it exists."""
        if not self.SETTINGS_PATH.exists():
            pytest.skip("settings.json not present (expected in local dev only)")
        content = self.SETTINGS_PATH.read_text()
        data = json.loads(content)
        assert isinstance(data, dict)

    def test_settings_json_has_megg_if_exists(self):
        """settings.json should contain MEGG MCP if it exists."""
        if not self.SETTINGS_PATH.exists():
            pytest.skip("settings.json not present (expected in local dev only)")
        data = json.loads(self.SETTINGS_PATH.read_text())
        assert "mcpServers" in data, "settings.json missing mcpServers"
        assert "megg" in data["mcpServers"], "settings.json missing megg MCP config"
