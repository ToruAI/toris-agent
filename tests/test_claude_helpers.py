"""Tests for pure helper functions in claude_service.py."""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:token")
os.environ.setdefault("TELEGRAM_DEFAULT_CHAT_ID", "0")
sys.path.insert(0, str(Path(__file__).parent.parent))

import importlib

import config
from claude_service import format_tool_call, WorkingIndicator, build_claude_options
import claude_service


class TestFormatToolCall:
    def test_basic_output_contains_tool_name(self):
        result = format_tool_call("Read", {"file_path": "/foo/bar.py"})
        assert "Read" in result

    def test_output_contains_input_json(self):
        result = format_tool_call("Bash", {"command": "ls -la"})
        assert "ls -la" in result

    def test_long_input_is_truncated(self):
        """Inputs over 500 chars get truncated with '...'."""
        big_input = {"data": "x" * 600}
        result = format_tool_call("Write", big_input)
        assert "..." in result
        # Total input section <= 500 + len("...") = 503
        assert len(result) < 700

    def test_short_input_not_truncated(self):
        result = format_tool_call("Grep", {"pattern": "hello"})
        assert "..." not in result

    def test_code_fence_wraps_input(self):
        result = format_tool_call("Edit", {"file_path": "/x.py"})
        assert "```" in result


class TestWorkingIndicator:
    def test_start_creates_task(self):
        async def run():
            ind = WorkingIndicator(edit_fn=AsyncMock(), interval=10.0)
            ind.start()
            assert ind._task is not None
            ind.stop()
        asyncio.run(run())

    def test_stop_cancels_task(self):
        async def run():
            ind = WorkingIndicator(edit_fn=AsyncMock(), interval=10.0)
            ind.start()
            ind.stop()
            assert ind._task is None
        asyncio.run(run())

    def test_stop_without_start_is_safe(self):
        """Calling stop before start must not raise."""
        ind = WorkingIndicator(edit_fn=AsyncMock(), interval=10.0)
        ind.stop()  # should not raise

    def test_messages_list_nonempty(self):
        assert len(WorkingIndicator.MESSAGES) > 0

    def test_messages_cycle_by_count(self):
        """_count wraps around via modulo over MESSAGES list."""
        n = len(WorkingIndicator.MESSAGES)
        ind = WorkingIndicator(edit_fn=AsyncMock(), interval=10.0)
        ind._count = n  # exactly one full cycle
        msg = WorkingIndicator.MESSAGES[ind._count % n]
        assert msg == WorkingIndicator.MESSAGES[0]

    def test_indicator_calls_edit_fn_periodically(self):
        """After starting, the indicator calls edit_fn at least once within interval."""
        calls = []

        async def fake_edit(msg):
            calls.append(msg)

        async def run():
            ind = WorkingIndicator(edit_fn=fake_edit, interval=0.05)
            ind.start()
            await asyncio.sleep(0.18)  # 3 × interval
            ind.stop()
            return len(calls)

        count = asyncio.run(run())
        assert count >= 2


class TestBuildClaudeOptions:
    def test_go_all_mode_has_allowed_tools(self):
        options = build_claude_options("test prompt", "go_all")
        assert options.allowed_tools is not None
        assert "Bash" in options.allowed_tools

    def test_approve_mode_has_no_allowed_tools(self):
        options = build_claude_options("test prompt", "approve")
        assert not options.allowed_tools

    def test_approve_mode_has_can_use_tool(self):
        fn = lambda name, inp: None
        options = build_claude_options("test prompt", "approve", can_use_tool=fn)
        assert options.can_use_tool is fn

    def test_settings_file_attached_when_configured(self, monkeypatch):
        monkeypatch.setattr(claude_service._cfg, "CLAUDE_SETTINGS_FILE", "/fake/settings.json")
        options = build_claude_options("test", "go_all")
        assert options.settings == "/fake/settings.json"

    def test_no_settings_file_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(claude_service._cfg, "CLAUDE_SETTINGS_FILE", "")
        options = build_claude_options("test", "go_all")
        assert not getattr(options, "settings", None)


class TestClaudeTimeout:
    def test_claude_timeout_default_is_int(self):
        assert isinstance(config.CLAUDE_TIMEOUT, int)
        assert config.CLAUDE_TIMEOUT > 0

    def test_claude_timeout_from_env(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_TIMEOUT", "120")
        importlib.reload(config)
        assert config.CLAUDE_TIMEOUT == 120

    def teardown_method(self):
        importlib.reload(config)
