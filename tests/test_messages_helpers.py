"""
Tests for pure helper functions in handlers/messages.py.
No Telegram API calls — uses AsyncMock for awaitable targets.
"""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:token")
os.environ.setdefault("TELEGRAM_DEFAULT_CHAT_ID", "0")
sys.path.insert(0, str(Path(__file__).parent.parent))

from handlers.messages import send_long_message, _update_session_state


# ── send_long_message ─────────────────────────────────────────────────────────

def _make_update():
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


class TestSendLongMessage:
    def test_short_text_edits_first_msg(self):
        """Text within chunk_size is sent via first_msg.edit_text."""
        update = _make_update()
        first_msg = AsyncMock()
        asyncio.run(send_long_message(update, first_msg, "Hello world"))
        assert first_msg.edit_text.call_count == 1
        assert first_msg.edit_text.call_args.args[0] == "Hello world"
        update.message.reply_text.assert_not_called()

    def test_short_text_replies_when_no_first_msg(self):
        """When first_msg is None, short text is sent as a new reply."""
        update = _make_update()
        asyncio.run(send_long_message(update, None, "Hello"))
        assert update.message.reply_text.call_count == 1
        assert update.message.reply_text.call_args.args[0] == "Hello"

    def test_long_text_edits_first_chunk_then_replies(self):
        """Long text: first chunk goes to first_msg.edit_text, rest to reply_text."""
        update = _make_update()
        first_msg = AsyncMock()
        # 5 lines of 30 chars each = 150 chars, chunk_size=60 → multiple chunks
        text = ("A" * 28 + "\n") * 5
        asyncio.run(send_long_message(update, first_msg, text, chunk_size=60))
        assert first_msg.edit_text.call_count == 1
        assert "[1/" in first_msg.edit_text.call_args[0][0]
        assert update.message.reply_text.call_count >= 1

    def test_long_text_all_replies_when_no_first_msg(self):
        """When first_msg is None, all chunks go to reply_text."""
        update = _make_update()
        text = ("B" * 28 + "\n") * 5
        asyncio.run(send_long_message(update, None, text, chunk_size=60))
        assert update.message.reply_text.call_count >= 2
        first_call_text = update.message.reply_text.call_args_list[0][0][0]
        assert "[1/" in first_call_text

    def test_chunk_numbering_in_all_messages(self):
        """Every chunk is labelled with its position out of total."""
        update = _make_update()
        first_msg = AsyncMock()
        text = ("C" * 28 + "\n") * 9   # forces multiple chunks at chunk_size=60
        asyncio.run(send_long_message(update, first_msg, text, chunk_size=60))
        all_calls = [first_msg.edit_text.call_args_list[0][0][0]] + [
            c[0][0] for c in update.message.reply_text.call_args_list
        ]
        total = len(all_calls)
        for i, call_text in enumerate(all_calls, 1):
            assert f"[{i}/{total}]" in call_text

    def test_hard_split_when_no_whitespace(self):
        """Text with no newlines or spaces is split at chunk_size boundary."""
        update = _make_update()
        first_msg = AsyncMock()
        text = "X" * 150
        asyncio.run(send_long_message(update, first_msg, text, chunk_size=60))
        # Should split — first chunk should contain 60 chars from original
        first_text = first_msg.edit_text.call_args[0][0]
        assert first_text.startswith("X" * 60)


# ── _update_session_state ─────────────────────────────────────────────────────

class TestUpdateSessionState:
    def test_sets_current_session(self):
        state = {"current_session": None, "sessions": [], "session_names": {}}
        mgr = MagicMock()
        _update_session_state(state, "sess-abc", mgr)
        assert state["current_session"] == "sess-abc"

    def test_appends_session_id_to_sessions_list(self):
        state = {"current_session": None, "sessions": [], "session_names": {}}
        mgr = MagicMock()
        _update_session_state(state, "sess-abc", mgr)
        assert "sess-abc" in state["sessions"]

    def test_does_not_duplicate_session(self):
        state = {"current_session": "sess-abc", "sessions": ["sess-abc"], "session_names": {}}
        mgr = MagicMock()
        _update_session_state(state, "sess-abc", mgr)
        assert state["sessions"].count("sess-abc") == 1

    def test_consumes_pending_session_name(self):
        state = {
            "current_session": None,
            "sessions": [],
            "session_names": {},
            "pending_session_name": "my-feature",
        }
        mgr = MagicMock()
        _update_session_state(state, "sess-xyz", mgr)
        assert "pending_session_name" not in state
        assert state["session_names"]["sess-xyz"] == "my-feature"

    def test_calls_save_state(self):
        state = {"current_session": None, "sessions": [], "session_names": {}}
        mgr = MagicMock()
        _update_session_state(state, "sess-abc", mgr)
        mgr.save_state.assert_called_once()
