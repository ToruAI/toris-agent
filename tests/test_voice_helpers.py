"""Tests for pure helper functions in voice_service.py."""
import os
import sys
from pathlib import Path

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:token")
os.environ.setdefault("TELEGRAM_DEFAULT_CHAT_ID", "0")
sys.path.insert(0, str(Path(__file__).parent.parent))

import voice_service


class TestIsValidTranscription:
    def test_empty_string_invalid(self):
        assert voice_service.is_valid_transcription("") is False

    def test_whitespace_only_invalid(self):
        assert voice_service.is_valid_transcription("   ") is False

    def test_transcription_error_prefix_invalid(self):
        assert voice_service.is_valid_transcription("[Transcription error: timeout]") is False

    def test_transcription_error_variant_invalid(self):
        assert voice_service.is_valid_transcription("[Transcription error: network failure]") is False

    def test_normal_text_valid(self):
        assert voice_service.is_valid_transcription("Hello, what's the weather?") is True

    def test_short_word_valid(self):
        assert voice_service.is_valid_transcription("hi") is True

    def test_leading_whitespace_stripped_before_check(self):
        """Leading spaces don't make error prefix miss."""
        assert voice_service.is_valid_transcription("  [Transcription error: x]") is False

    def test_does_not_start_with_bracket_but_valid(self):
        """Square-bracket text that isn't an error prefix is valid."""
        assert voice_service.is_valid_transcription("[Note: please check this]") is True


class TestFormatTtsFallback:
    def test_includes_original_text(self):
        result = voice_service.format_tts_fallback("Hello there")
        assert "Hello there" in result

    def test_includes_voice_failure_notice(self):
        result = voice_service.format_tts_fallback("anything")
        assert "Voice generation failed" in result or "🔇" in result

    def test_empty_text(self):
        result = voice_service.format_tts_fallback("")
        assert isinstance(result, str)
        assert len(result) > 0
