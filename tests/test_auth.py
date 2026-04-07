"""Tests for auth.py — message filtering, authorization, rate limiting."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:token")
os.environ.setdefault("TELEGRAM_DEFAULT_CHAT_ID", "0")

import pytest
import auth


class TestShouldHandleMessage:
    def test_no_topic_filter_handles_all(self, monkeypatch):
        monkeypatch.setattr(auth, "_cfg", type("C", (), {"TOPIC_ID": None})())
        assert auth.should_handle_message(None) is True
        assert auth.should_handle_message(123) is True

    def test_with_topic_filter_accepts_matching(self, monkeypatch):
        monkeypatch.setattr(auth, "_cfg", type("C", (), {"TOPIC_ID": "42"})())
        assert auth.should_handle_message(42) is True

    def test_with_topic_filter_rejects_other(self, monkeypatch):
        monkeypatch.setattr(auth, "_cfg", type("C", (), {"TOPIC_ID": "42"})())
        assert auth.should_handle_message(99) is False

    def test_with_topic_filter_rejects_none_thread(self, monkeypatch):
        monkeypatch.setattr(auth, "_cfg", type("C", (), {"TOPIC_ID": "42"})())
        assert auth.should_handle_message(None) is False


class TestCheckRateLimit:
    def setup_method(self):
        auth._rate_limits.clear()

    def test_first_message_allowed(self):
        allowed, msg = auth.check_rate_limit(1)
        assert allowed is True
        assert msg == ""

    def test_too_fast_rejected(self):
        auth.check_rate_limit(1)
        allowed, msg = auth.check_rate_limit(1)
        assert allowed is False
        assert "wait" in msg.lower()

    def test_different_users_independent(self):
        auth.check_rate_limit(1)
        allowed, _ = auth.check_rate_limit(2)
        assert allowed is True

    def test_per_minute_limit(self):
        auth._rate_limits["99"] = {
            "last_message": 0,
            "minute_count": 10,
            "minute_start": time.time(),
        }
        allowed, msg = auth.check_rate_limit(99)
        assert allowed is False
        assert "limit" in msg.lower()

    def test_rate_limit_resets_after_minute(self):
        """Per-minute counter resets when > 60s have elapsed."""
        auth._rate_limits["77"] = {
            "last_message": 0,
            "minute_count": 9,          # one below the limit
            "minute_start": time.time() - 61,  # started over a minute ago
        }
        # First call: minute has expired → counter resets, message allowed
        allowed, msg = auth.check_rate_limit(77)
        assert allowed is True
        # Counter was reset, so now minute_count == 1
        assert auth._rate_limits["77"]["minute_count"] == 1


class TestShouldHandleMessageInvalidTopicId:
    def test_invalid_topic_id_warns_and_handles_all(self, monkeypatch):
        """Non-integer TOPIC_ID logs a warning and accepts all messages."""
        monkeypatch.setattr(auth, "_cfg", type("C", (), {"TOPIC_ID": "not-a-number"})())
        assert auth.should_handle_message(42) is True
        assert auth.should_handle_message(None) is True
