"""Tests for config.py — centralized env var loading."""
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:token")
os.environ.setdefault("TELEGRAM_DEFAULT_CHAT_ID", "0")

import config


class TestResolveProviderConfig:
    def setup_method(self):
        for var in ("TTS_PROVIDER", "ELEVENLABS_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(var, None)

    def test_explicit_openai_wins(self):
        os.environ["TTS_PROVIDER"] = "openai"
        os.environ["ELEVENLABS_API_KEY"] = "sk_test"
        assert config.resolve_provider("TTS_PROVIDER") == "openai"

    def test_fallback_to_none(self):
        assert config.resolve_provider("TTS_PROVIDER") == "none"

    def teardown_method(self):
        for var in ("TTS_PROVIDER", "ELEVENLABS_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(var, None)


class TestConfigDefaults:
    def test_claude_timeout_is_positive_int(self):
        assert isinstance(config.CLAUDE_TIMEOUT, int)
        assert config.CLAUDE_TIMEOUT > 0

    def test_max_voice_chars_is_positive_int(self):
        assert isinstance(config.MAX_VOICE_CHARS, int)
        assert config.MAX_VOICE_CHARS > 0

    def test_state_file_is_path(self):
        assert isinstance(config.STATE_FILE, Path)
        assert config.STATE_FILE.name == "sessions_state.json"

    def test_settings_file_is_path(self):
        assert isinstance(config.SETTINGS_FILE, Path)

    def test_credentials_file_is_path(self):
        assert isinstance(config.CREDENTIALS_FILE, Path)


class TestConfigValidate:
    def test_validate_warns_on_zero_chat_id(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "0")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "12345:ABC")
        importlib.reload(config)
        warnings = config.validate()
        assert any("ALL chats" in w for w in warnings)

    def test_validate_empty_on_valid_config(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "12345:ABC")
        monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "999")
        monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")
        importlib.reload(config)
        warnings = config.validate()
        # No critical warnings
        assert not any("required" in w for w in warnings)

    def teardown_method(self):
        importlib.reload(config)
