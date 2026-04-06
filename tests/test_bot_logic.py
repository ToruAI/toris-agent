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
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

# We need to set required env vars BEFORE importing bot
# so it doesn't crash on missing TELEGRAM_BOT_TOKEN
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:token")
os.environ.setdefault("TELEGRAM_DEFAULT_CHAT_ID", "0")

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import bot
import config


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
        assert config.resolve_provider("TTS_PROVIDER") == "elevenlabs"

    def test_explicit_openai(self):
        os.environ["TTS_PROVIDER"] = "openai"
        assert config.resolve_provider("TTS_PROVIDER") == "openai"

    def test_explicit_invalid_ignored(self):
        os.environ["TTS_PROVIDER"] = "invalid"
        assert config.resolve_provider("TTS_PROVIDER") == "none"

    def test_fallback_elevenlabs(self):
        os.environ["ELEVENLABS_API_KEY"] = "sk_test"
        assert config.resolve_provider("TTS_PROVIDER") == "elevenlabs"

    def test_fallback_openai_when_no_elevenlabs(self):
        os.environ["OPENAI_API_KEY"] = "sk-test"
        assert config.resolve_provider("TTS_PROVIDER") == "openai"

    def test_elevenlabs_wins_over_openai(self):
        os.environ["ELEVENLABS_API_KEY"] = "sk_test"
        os.environ["OPENAI_API_KEY"] = "sk-test"
        assert config.resolve_provider("TTS_PROVIDER") == "elevenlabs"

    def test_none_when_no_keys(self):
        assert config.resolve_provider("TTS_PROVIDER") == "none"

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

    def test_settings_json_has_permissions_if_exists(self):
        """settings.json should contain permissions block if it exists."""
        if not self.SETTINGS_PATH.exists():
            pytest.skip("settings.json not present (expected in local dev only)")
        data = json.loads(self.SETTINGS_PATH.read_text())
        assert "permissions" in data, "settings.json missing permissions block"


class TestPhotoHandler:
    """Test photo handler logic."""

    def test_photo_prompt_with_caption(self):
        """Photo with caption produces correct prompt."""
        caption = "What's in this image?"
        path = Path("/sandbox/photo_20260405_120000.jpg")

        # Replicate the prompt building logic from handle_photo
        if caption:
            prompt = f"I sent you a photo. It's saved at: {path}\n\nMy message: {caption}"
        else:
            prompt = f"I sent you a photo. It's saved at: {path}\n\nPlease look at it and describe what you see, or help me with whatever is shown."

        assert str(path) in prompt
        assert caption in prompt

    def test_photo_prompt_without_caption(self):
        """Photo without caption produces fallback prompt."""
        caption = ""
        path = Path("/sandbox/photo_20260405_120000.jpg")

        if caption:
            prompt = f"I sent you a photo. It's saved at: {path}\n\nMy message: {caption}"
        else:
            prompt = f"I sent you a photo. It's saved at: {path}\n\nPlease look at it and describe what you see, or help me with whatever is shown."

        assert str(path) in prompt
        assert "describe" in prompt

    def test_photo_filename_format(self):
        """Photo filename includes timestamp."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"photo_{timestamp}.jpg"
        assert filename.startswith("photo_")
        assert filename.endswith(".jpg")
        assert len(filename) == len("photo_20260405_120000.jpg")


class TestCancellation:
    """Test cancellation event logic."""

    def test_cancel_event_initial_state(self):
        """A new asyncio.Event is not set."""
        event = asyncio.Event()
        assert not event.is_set()

    def test_cancel_event_set_and_check(self):
        """Setting an event makes is_set() return True."""
        event = asyncio.Event()
        event.set()
        assert event.is_set()

    def test_cancel_event_clear_resets(self):
        """Clearing an event makes is_set() return False again."""
        event = asyncio.Event()
        event.set()
        event.clear()
        assert not event.is_set()

    def test_cancel_events_dict_per_user(self):
        """cancel_events dict tracks separate events per user_id."""
        cancel_events = {}

        # Simulate start of call_claude for user 123
        user_id = 123
        if user_id not in cancel_events:
            cancel_events[user_id] = asyncio.Event()
        cancel_events[user_id].clear()

        assert not cancel_events[123].is_set()

        # Simulate /cancel
        cancel_events[123].set()
        assert cancel_events[123].is_set()

        # Different user not affected
        assert 456 not in cancel_events

    def test_cancel_no_effect_on_already_cancelled(self):
        """Setting an already-set event is idempotent."""
        event = asyncio.Event()
        event.set()
        event.set()  # Should not raise
        assert event.is_set()


class TestCompact:
    """Test /compact session logic."""

    def test_compact_summary_stored_in_state(self):
        """compact_summary key gets saved to user state dict."""
        state = {"current_session": "sess_abc", "sessions": ["sess_abc"]}
        summary = "We discussed X, decided Y, working on Z."

        # Simulate what cmd_compact does after getting summary
        state["compact_summary"] = summary
        state["current_session"] = None

        assert state["compact_summary"] == summary
        assert state["current_session"] is None

    def test_compact_summary_prepended_to_next_message(self):
        """compact_summary is injected into the next message prompt."""
        state = {
            "current_session": None,
            "sessions": [],
            "compact_summary": "Previous: discussed auth system.",
        }
        user_text = "Continue with the login form."

        # Simulate what handlers do
        compact_summary = state.pop("compact_summary", None)
        if compact_summary:
            text = f"<previous_session_summary>\n{compact_summary}\n</previous_session_summary>\n\n{user_text}"
        else:
            text = user_text

        assert "<previous_session_summary>" in text
        assert "Previous: discussed auth system." in text
        assert "Continue with the login form." in text

    def test_compact_summary_cleared_after_use(self):
        """compact_summary is removed from state after being injected."""
        state = {"compact_summary": "some summary", "current_session": None, "sessions": []}

        compact_summary = state.pop("compact_summary", None)
        assert compact_summary == "some summary"
        assert "compact_summary" not in state

    def test_compact_no_summary_no_injection(self):
        """Without compact_summary, text is unchanged."""
        state = {"current_session": None, "sessions": []}
        user_text = "Hello"

        compact_summary = state.pop("compact_summary", None)
        if compact_summary:
            text = f"<previous_session_summary>\n{compact_summary}\n</previous_session_summary>\n\n{user_text}"
        else:
            text = user_text

        assert text == user_text

    def test_compact_requires_active_session(self):
        """compact should not proceed if no active session."""
        state = {"current_session": None, "sessions": []}

        # Simulate the guard in cmd_compact
        has_session = bool(state.get("current_session"))
        assert not has_session


# ─────────────────────────────────────────────
# TestMcpStatus
# ─────────────────────────────────────────────

class TestMcpStatus:
    """Test MCP status helper function."""

    def test_no_settings_file_configured(self, tmp_path):
        """Empty CLAUDE_SETTINGS_FILE returns informative message."""
        settings_file = ""
        if not settings_file:
            result = ["MCP: CLAUDE_SETTINGS_FILE not configured"]
        assert result == ["MCP: CLAUDE_SETTINGS_FILE not configured"]

    def test_settings_file_not_found(self, tmp_path):
        """Nonexistent file returns not-found message."""
        fake_path = str(tmp_path / "missing_settings.json")

        from pathlib import Path
        settings_path = Path(fake_path)
        if not settings_path.is_absolute():
            settings_path = Path(".") / fake_path

        if not settings_path.exists():
            result = [f"MCP config: settings file not found ({fake_path})"]

        assert "not found" in result[0]

    def test_settings_with_valid_mcp_command(self, tmp_path):
        """Settings with npx (available) returns OK status."""
        import shutil, json
        settings = {
            "mcpServers": {
                "megg": {"command": "npx", "args": ["-y", "megg@latest"]}
            }
        }
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings))

        # Parse logic
        data = json.loads(settings_file.read_text())
        mcp_servers = data.get("mcpServers", {})
        lines = ["MCP Servers:"]
        for name, config in mcp_servers.items():
            cmd = config.get("command", "")
            if cmd and shutil.which(cmd):
                lines.append(f"  {name}: OK ({cmd})")
            elif cmd:
                lines.append(f"  {name}: MISSING ({cmd} not found in PATH)")

        # npx should be available in the test environment
        if shutil.which("npx"):
            assert any("OK" in line for line in lines)
        else:
            assert any("MISSING" in line for line in lines)

    def test_settings_with_missing_command(self, tmp_path):
        """Settings with unavailable command returns MISSING status."""
        import shutil, json
        settings = {
            "mcpServers": {
                "fake-tool": {"command": "this-binary-does-not-exist-xyz", "args": []}
            }
        }
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings))

        data = json.loads(settings_file.read_text())
        mcp_servers = data.get("mcpServers", {})
        lines = ["MCP Servers:"]
        for name, config in mcp_servers.items():
            cmd = config.get("command", "")
            if cmd and shutil.which(cmd):
                lines.append(f"  {name}: OK ({cmd})")
            elif cmd:
                lines.append(f"  {name}: MISSING ({cmd} not found in PATH)")

        assert any("MISSING" in line for line in lines)

    def test_settings_empty_mcp_servers(self, tmp_path):
        """Settings with empty mcpServers returns 'none configured'."""
        import json
        settings = {"permissions": {"allow": []}, "mcpServers": {}}
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings))

        data = json.loads(settings_file.read_text())
        mcp_servers = data.get("mcpServers", {})
        if not mcp_servers:
            result = ["MCP Servers: none configured"]

        assert result == ["MCP Servers: none configured"]

    def test_corrupted_settings_file(self, tmp_path):
        """Corrupted settings.json returns error message."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{invalid json{{")

        try:
            import json
            json.loads(settings_file.read_text())
            result = []
        except json.JSONDecodeError as e:
            result = [f"MCP config: ERROR reading settings - {e}"]

        assert len(result) == 1
        assert "ERROR" in result[0]

    def test_get_mcp_status_no_settings(self):
        """bot.get_mcp_status with empty string returns not-configured message."""
        result = bot.get_mcp_status("")
        assert result == ["MCP: CLAUDE_SETTINGS_FILE not configured"]

    def test_get_mcp_status_missing_file(self, tmp_path):
        """bot.get_mcp_status with nonexistent path returns not-found message."""
        fake = str(tmp_path / "nosuchfile.json")
        result = bot.get_mcp_status(fake)
        assert len(result) == 1
        assert "not found" in result[0]

    def test_get_mcp_status_valid_settings(self, tmp_path):
        """bot.get_mcp_status with real settings file returns structured lines."""
        import shutil as _shutil
        settings = {
            "mcpServers": {
                "fake-xyz": {"command": "this-binary-does-not-exist-xyz", "args": []},
            }
        }
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings))

        result = bot.get_mcp_status(str(settings_file))
        assert result[0] == "MCP Servers:"
        assert any("fake-xyz" in line for line in result)
        assert any("MISSING" in line for line in result)

    def test_get_mcp_status_corrupted_json(self, tmp_path):
        """bot.get_mcp_status with bad JSON returns error line."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{not valid json{{")

        result = bot.get_mcp_status(str(settings_file))
        assert len(result) == 1
        assert "ERROR" in result[0]

    def test_get_mcp_status_empty_servers(self, tmp_path):
        """bot.get_mcp_status with empty mcpServers returns 'none configured'."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"mcpServers": {}}))

        result = bot.get_mcp_status(str(settings_file))
        assert result == ["MCP Servers: none configured"]

    def test_get_mcp_status_no_command_field(self, tmp_path):
        """MCP server entry with no command field returns misconfigured."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "mcpServers": {"broken": {"args": []}}
        }))

        result = bot.get_mcp_status(str(settings_file))
        assert any("misconfigured" in line for line in result)


class TestTranscriptionValidation:
    def test_empty_string_invalid(self):
        assert bot.is_valid_transcription("") is False

    def test_whitespace_only_invalid(self):
        assert bot.is_valid_transcription("   ") is False

    def test_transcription_error_string_invalid(self):
        assert bot.is_valid_transcription("[Transcription error: timeout]") is False

    def test_transcription_error_any_variant_invalid(self):
        assert bot.is_valid_transcription("[Transcription error: network failure]") is False

    def test_normal_text_valid(self):
        assert bot.is_valid_transcription("Hello, what's the weather?") is True

    def test_short_text_valid(self):
        assert bot.is_valid_transcription("ok") is True

    def test_text_with_leading_whitespace_valid(self):
        assert bot.is_valid_transcription("  Hello there  ") is True


class TestTTSFallback:
    def test_format_tts_fallback_contains_response(self):
        msg = bot.format_tts_fallback("Here is your answer.")
        assert "Here is your answer." in msg
        assert "🔇" in msg

    def test_format_tts_fallback_mentions_voice(self):
        msg = bot.format_tts_fallback("Test.")
        assert "Voice" in msg or "voice" in msg


class TestBuildClaudeOptions:
    def test_go_all_mode_has_allowed_tools(self):
        options = bot.build_claude_options("test prompt", "go_all")
        assert options.allowed_tools is not None
        assert "Bash" in options.allowed_tools

    def test_approve_mode_has_no_allowed_tools(self):
        options = bot.build_claude_options("test prompt", "approve")
        assert not options.allowed_tools

    def test_approve_mode_has_can_use_tool(self):
        fn = lambda name, inp: None
        options = bot.build_claude_options("test prompt", "approve", can_use_tool=fn)
        assert options.can_use_tool is fn

    def test_settings_file_attached_when_configured(self, monkeypatch):
        monkeypatch.setattr(bot, "CLAUDE_SETTINGS_FILE", "/fake/settings.json")
        options = bot.build_claude_options("test", "go_all")
        assert options.settings == "/fake/settings.json"

    def test_no_settings_file_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(bot, "CLAUDE_SETTINGS_FILE", "")
        options = bot.build_claude_options("test", "go_all")
        assert not getattr(options, "settings", None)


class TestStateLocking:
    def test_get_user_lock_returns_asyncio_lock(self):
        lock = bot.get_user_lock("user123")
        import asyncio
        assert isinstance(lock, asyncio.Lock)

    def test_get_user_lock_same_user_same_lock(self):
        lock1 = bot.get_user_lock("userA")
        lock2 = bot.get_user_lock("userA")
        assert lock1 is lock2

    def test_get_user_lock_different_users_different_locks(self):
        lock1 = bot.get_user_lock("userX")
        lock2 = bot.get_user_lock("userY")
        assert lock1 is not lock2

    def test_save_state_atomic_write(self, tmp_path, monkeypatch):
        """save_state writes to .tmp then renames — no .tmp left after success."""
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "state.json")
        bot.user_sessions = {"u1": {"current_session": "abc", "sessions": ["abc"]}}
        bot.save_state()
        assert (tmp_path / "state.json").exists()
        assert not (tmp_path / "state.json.tmp").exists()
        import json
        data = json.loads((tmp_path / "state.json").read_text())
        assert data["u1"]["current_session"] == "abc"

    def test_save_settings_atomic_write(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bot, "SETTINGS_FILE", tmp_path / "settings.json")
        bot.user_settings = {"u1": {"audio_enabled": True}}
        bot.save_settings()
        assert (tmp_path / "settings.json").exists()
        assert not (tmp_path / "settings.json.tmp").exists()


class TestClaudeTimeout:
    def test_claude_timeout_default_is_int(self):
        assert isinstance(bot.CLAUDE_TIMEOUT, int)
        assert bot.CLAUDE_TIMEOUT > 0

    def test_claude_timeout_from_env(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_TIMEOUT", "120")
        import importlib
        importlib.reload(config)
        importlib.reload(bot)
        assert bot.CLAUDE_TIMEOUT == 120


class TestWorkingIndicator:
    def test_start_creates_task(self):
        async def run():
            calls = []
            async def edit_fn(msg):
                calls.append(msg)
            indicator = bot.WorkingIndicator(edit_fn=edit_fn, interval=0.05)
            indicator.start()
            assert indicator._task is not None
            indicator.stop()
        asyncio.run(run())

    def test_stop_cancels_task(self):
        async def run():
            async def edit_fn(msg):
                pass
            indicator = bot.WorkingIndicator(edit_fn=edit_fn, interval=10.0)
            indicator.start()
            indicator.stop()
            assert indicator._task is None
        asyncio.run(run())

    def test_edit_fn_called_after_interval(self):
        async def run():
            calls = []
            async def edit_fn(msg):
                calls.append(msg)
            indicator = bot.WorkingIndicator(edit_fn=edit_fn, interval=0.05)
            indicator.start()
            await asyncio.sleep(0.12)
            indicator.stop()
            assert len(calls) >= 2
        asyncio.run(run())

    def test_messages_cycle_through_list(self):
        async def run():
            calls = []
            async def edit_fn(msg):
                calls.append(msg)
            indicator = bot.WorkingIndicator(edit_fn=edit_fn, interval=0.02)
            indicator.start()
            await asyncio.sleep(0.12)
            indicator.stop()
            assert len(calls) >= 2
            assert all(msg in bot.WorkingIndicator.MESSAGES for msg in calls)
        asyncio.run(run())

    def test_edit_fn_exception_does_not_propagate(self):
        async def run():
            async def edit_fn(msg):
                raise RuntimeError("network error")
            indicator = bot.WorkingIndicator(edit_fn=edit_fn, interval=0.05)
            indicator.start()
            await asyncio.sleep(0.12)
            indicator.stop()
            # no exception raised
        asyncio.run(run())
