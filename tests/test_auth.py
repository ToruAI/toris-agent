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


class TestAllowedUserIds:
    def _make_cfg(self, allowed_chat_id=0, allowed_user_ids=None):
        return type("C", (), {
            "ALLOWED_CHAT_ID": allowed_chat_id,
            "ALLOWED_USER_IDS": allowed_user_ids if allowed_user_ids is not None else set(),
        })()

    def _fake_update(self, chat_id=1, user_id=100):
        class FakeUpdate:
            class effective_chat:
                id = chat_id
            class effective_user:
                id = user_id
        FakeUpdate.effective_chat.id = chat_id
        FakeUpdate.effective_user.id = user_id
        return FakeUpdate()

    def test_empty_allowed_users_permits_all(self, monkeypatch):
        monkeypatch.setattr(auth, "_cfg", self._make_cfg(allowed_user_ids=set()))
        assert auth._is_authorized(self._fake_update(user_id=999)) is True

    def test_allowed_user_id_matches(self, monkeypatch):
        monkeypatch.setattr(auth, "_cfg", self._make_cfg(allowed_user_ids={100, 200}))
        assert auth._is_authorized(self._fake_update(user_id=100)) is True

    def test_user_id_not_in_allowed_rejected(self, monkeypatch):
        monkeypatch.setattr(auth, "_cfg", self._make_cfg(allowed_user_ids={100, 200}))
        assert auth._is_authorized(self._fake_update(user_id=999)) is False

    def test_chat_id_and_user_id_both_checked(self, monkeypatch):
        monkeypatch.setattr(auth, "_cfg", self._make_cfg(allowed_chat_id=42, allowed_user_ids={100}))
        # Right user, wrong chat
        assert auth._is_authorized(self._fake_update(chat_id=99, user_id=100)) is False
        # Right chat, wrong user
        assert auth._is_authorized(self._fake_update(chat_id=42, user_id=999)) is False
        # Both right
        assert auth._is_authorized(self._fake_update(chat_id=42, user_id=100)) is True

    def test_no_effective_user_still_passes_when_empty(self, monkeypatch):
        """Bot-level messages (no user) should pass when no user filter is set."""
        monkeypatch.setattr(auth, "_cfg", self._make_cfg(allowed_user_ids=set()))
        class FakeUpdate:
            class effective_chat:
                id = 1
            effective_user = None
        assert auth._is_authorized(FakeUpdate()) is True


class TestShouldHandleMessageInvalidTopicId:
    def test_invalid_topic_id_warns_and_handles_all(self, monkeypatch):
        """Non-integer TOPIC_ID logs a warning and accepts all messages."""
        monkeypatch.setattr(auth, "_cfg", type("C", (), {"TOPIC_ID": "not-a-number"})())
        assert auth.should_handle_message(42) is True
        assert auth.should_handle_message(None) is True
