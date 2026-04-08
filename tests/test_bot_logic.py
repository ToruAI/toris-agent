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
from pathlib import Path
from unittest.mock import patch

# We need to set required env vars BEFORE importing bot
# so it doesn't crash on missing TELEGRAM_BOT_TOKEN
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:token")
os.environ.setdefault("TELEGRAM_DEFAULT_CHAT_ID", "0")

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import bot
import auth
import config
from handlers.session import parse_session_name, format_sessions_list
from handlers.messages import error_message


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
        assert isinstance(config.MAX_VOICE_CHARS, int)
        assert config.MAX_VOICE_CHARS > 0


# ─────────────────────────────────────────────
# TestAdminUserIds
# ─────────────────────────────────────────────

class TestAdminUserIds:
    def _make_cfg(self, allowed_chat_id=0, admin_user_ids=None):
        return type("C", (), {
            "ALLOWED_CHAT_ID": allowed_chat_id,
            "ADMIN_USER_IDS": admin_user_ids if admin_user_ids is not None else set(),
        })()

    def test_is_authorized_with_zero_chat_id(self, monkeypatch):
        """ALLOWED_CHAT_ID=0 means all chats allowed."""
        monkeypatch.setattr(auth, "_cfg", self._make_cfg(allowed_chat_id=0))

        class FakeUpdate:
            class effective_chat:
                id = 12345

        assert auth._is_authorized(FakeUpdate()) is True

    def test_is_authorized_matching_chat_id(self, monkeypatch):
        monkeypatch.setattr(auth, "_cfg", self._make_cfg(allowed_chat_id=99999))

        class FakeUpdate:
            class effective_chat:
                id = 99999

        assert auth._is_authorized(FakeUpdate()) is True

    def test_is_authorized_wrong_chat_id(self, monkeypatch):
        monkeypatch.setattr(auth, "_cfg", self._make_cfg(allowed_chat_id=99999))

        class FakeUpdate:
            class effective_chat:
                id = 11111

        assert auth._is_authorized(FakeUpdate()) is False

    def test_is_admin_empty_admin_ids_allows_authorized(self, monkeypatch):
        """No ADMIN_USER_IDS configured → anyone in authorized chat is admin."""
        monkeypatch.setattr(auth, "_cfg", self._make_cfg(allowed_chat_id=0, admin_user_ids=set()))

        class FakeUpdate:
            class effective_chat:
                id = 12345
            class effective_user:
                id = 99999

        assert auth._is_admin(FakeUpdate()) is True

    def test_is_admin_with_admin_ids_matching(self, monkeypatch):
        monkeypatch.setattr(auth, "_cfg", self._make_cfg(allowed_chat_id=0, admin_user_ids={111, 222}))

        class FakeUpdate:
            class effective_chat:
                id = 12345
            class effective_user:
                id = 111

        assert auth._is_admin(FakeUpdate()) is True

    def test_is_admin_with_admin_ids_not_matching(self, monkeypatch):
        monkeypatch.setattr(auth, "_cfg", self._make_cfg(allowed_chat_id=0, admin_user_ids={111, 222}))

        class FakeUpdate:
            class effective_chat:
                id = 12345
            class effective_user:
                id = 999  # not in admin list

        assert auth._is_admin(FakeUpdate()) is False


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



# ─────────────────────────────────────────────
# TestMcpStatus
# ─────────────────────────────────────────────

class TestMcpStatus:
    """Test MCP status helper function."""

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


class TestSessionNaming:
    def test_parse_session_name_empty_args(self):
        assert parse_session_name([]) is None

    def test_parse_session_name_single_word(self):
        assert parse_session_name(["analysis"]) == "analysis"

    def test_parse_session_name_multiple_words(self):
        assert parse_session_name(["my", "project"]) == "my project"

    def test_format_sessions_list_empty(self):
        assert format_sessions_list([]) == "No sessions yet."

    def test_format_sessions_list_shows_name(self):
        sessions = [{"id": "abc123de", "name": "project analysis"}]
        text = format_sessions_list(sessions)
        assert "project analysis" in text
        assert "abc123d" in text

    def test_format_sessions_list_unnamed_shows_placeholder(self):
        sessions = [{"id": "def456gh", "name": None}]
        text = format_sessions_list(sessions)
        assert "(unnamed)" in text
        assert "def456g" in text

    def test_format_sessions_list_multiple(self):
        sessions = [
            {"id": "abc123de", "name": "project analysis"},
            {"id": "def456gh", "name": None},
        ]
        text = format_sessions_list(sessions)
        assert "project analysis" in text
        assert "(unnamed)" in text


class TestErrorMessages:
    def test_rate_limit_error(self):
        msg = error_message("Claude call", Exception("rate limit exceeded 429"))
        assert "Rate limit" in msg
        assert "❌" in msg

    def test_timeout_error(self):
        msg = error_message("STT", Exception("request timeout"))
        assert "Timed out" in msg
        assert "❌" in msg

    def test_auth_error(self):
        msg = error_message("TTS", Exception("401 Unauthorized"))
        assert "Authentication" in msg

    def test_network_error(self):
        msg = error_message("Voice", Exception("network connection refused"))
        assert "Network" in msg

    def test_generic_error_includes_context(self):
        msg = error_message("TTS", Exception("Something broke"))
        assert "TTS" in msg
        assert "❌" in msg

    def test_generic_error_truncated(self):
        long_exc = Exception("x" * 300)
        msg = error_message("ctx", long_exc)
        assert len(msg) < 200


class TestVoiceServiceImport:
    def test_voice_service_is_importable(self):
        import voice_service
        assert callable(voice_service.transcribe_voice)
        assert callable(voice_service.text_to_speech)
        assert callable(voice_service.is_valid_transcription)
        assert callable(voice_service.format_tts_fallback)
        assert callable(voice_service.reconfigure)


class TestClaudeServiceImport:
    def test_claude_service_is_importable(self):
        import claude_service
        assert callable(claude_service.call_claude)
        assert callable(claude_service.build_claude_options)
        assert callable(claude_service.build_dynamic_prompt)
        assert hasattr(claude_service, "WorkingIndicator")


class TestSessionHandlersImport:
    def test_session_helpers_importable_from_handlers(self):
        from handlers.session import parse_session_name, format_sessions_list
        assert parse_session_name(["my", "project"]) == "my project"
        assert parse_session_name([]) is None
        sessions = [{"id": "abc123de", "name": "test"}]
        text = format_sessions_list(sessions)
        assert "abc123d" in text
        assert "test" in text


class TestAdminHandlersImport:
    def test_admin_handlers_importable(self):
        from handlers.admin import handle_settings_callback, handle_approval_callback
        assert callable(handle_settings_callback)
        assert callable(handle_approval_callback)


class TestMessageHandlersImport:
    def test_message_handlers_importable(self):
        from handlers.messages import handle_voice, handle_text, handle_photo
        assert callable(handle_voice)
        assert callable(handle_text)
        assert callable(handle_photo)
