"""Tests for automations.py — cron helpers and UI builders."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import automations
from automations import cron_to_human, build_automations_list, build_automation_card


class TestCronToHuman:
    def test_daily_at_time(self):
        assert cron_to_human("30 9 * * *") == "Codziennie 09:30"

    def test_weekdays(self):
        assert cron_to_human("0 8 * * 1-5") == "Pn-Pt 08:00"

    def test_specific_day(self):
        assert cron_to_human("0 10 * * 1") == "Pn 10:00"

    def test_every_hour(self):
        assert cron_to_human("0 * * * *") == "Co godzinę"

    def test_passthrough_unknown(self):
        # cron with dom/month set → return as-is
        assert cron_to_human("0 8 1 * *") == "0 8 1 * *"

    def test_passthrough_wrong_length(self):
        assert cron_to_human("* *") == "* *"


class TestBuildAutomationsList:
    def test_empty_triggers(self):
        text, markup = build_automations_list([])
        assert "Nie masz" in text
        # Keyboard should have one row with a create button
        assert len(markup.inline_keyboard) == 1

    def test_trigger_appears_in_text(self):
        triggers = [{"id": "abc", "name": "Daily report", "enabled": True}]
        text, markup = build_automations_list(triggers)
        assert "Daily report" in text
        assert "1" in text  # count

    def test_disabled_trigger_shows_circle(self):
        triggers = [{"id": "abc", "name": "Task", "enabled": False}]
        text, _ = build_automations_list(triggers)
        assert "○" in text

    def test_keyboard_has_row_per_trigger(self):
        triggers = [
            {"id": "a", "name": "A", "enabled": True},
            {"id": "b", "name": "B", "enabled": False},
        ]
        _, markup = build_automations_list(triggers)
        # 2 trigger rows + 1 footer row
        assert len(markup.inline_keyboard) == 3

    def test_keyboard_contains_trigger_ids(self):
        triggers = [
            {"id": "trig_1", "name": "A", "enabled": True},
            {"id": "trig_2", "name": "B", "enabled": False},
        ]
        _, markup = build_automations_list(triggers)
        all_callbacks = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
        assert any("trig_1" in cb for cb in all_callbacks)
        assert any("trig_2" in cb for cb in all_callbacks)


class TestBuildAutomationCard:
    def test_full_style_contains_schedule(self):
        trigger = {"id": "abc", "name": "Morning", "enabled": True, "cron_expression": "0 9 * * *"}
        text, markup = build_automation_card(trigger, style="full")
        assert "Morning" in text
        assert "HARMONOGRAM" in text

    def test_compact_style_single_line(self):
        trigger = {"id": "abc", "name": "Morning", "enabled": True, "cron_expression": "0 9 * * *"}
        text, markup = build_automation_card(trigger, style="compact")
        assert "Morning" in text
        assert "HARMONOGRAM" not in text

    def test_disabled_shows_resume(self):
        trigger = {"id": "abc", "name": "T", "enabled": False, "cron_expression": "0 9 * * *"}
        _, markup = build_automation_card(trigger)
        buttons = [b.text for row in markup.inline_keyboard for b in row]
        assert any("Resume" in b for b in buttons)

    def test_enabled_shows_pause(self):
        trigger = {"id": "abc", "name": "T", "enabled": True, "cron_expression": "0 9 * * *"}
        _, markup = build_automation_card(trigger)
        buttons = [b.text for row in markup.inline_keyboard for b in row]
        assert any("Pause" in b for b in buttons)


class TestRunRemoteTrigger:
    def test_list_returns_triggers(self):
        import asyncio
        from unittest.mock import patch, MagicMock
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"result": "[{\\"id\\": \\"abc\\", \\"name\\": \\"Test\\", \\"cron_expression\\": \\"0 9 * * *\\", \\"enabled\\": true}]"}'
        with patch("automations.subprocess.run", return_value=mock_result):
            triggers = asyncio.run(automations.run_remote_trigger_list())
        assert len(triggers) == 1
        assert triggers[0]["name"] == "Test"

    def test_list_returns_empty_on_error(self):
        import asyncio
        from unittest.mock import patch, MagicMock
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"
        with patch("automations.subprocess.run", return_value=mock_result):
            triggers = asyncio.run(automations.run_remote_trigger_list())
        assert triggers == []

    def test_run_trigger_returns_true_on_success(self):
        import asyncio
        from unittest.mock import patch, MagicMock
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("automations.subprocess.run", return_value=mock_result):
            result = asyncio.run(automations.run_remote_trigger_run("abc123"))
        assert result is True

    def test_run_trigger_returns_false_on_error(self):
        import asyncio
        from unittest.mock import patch, MagicMock
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("automations.subprocess.run", return_value=mock_result):
            result = asyncio.run(automations.run_remote_trigger_run("abc123"))
        assert result is False

    def test_toggle_trigger_command_contains_remote_trigger(self):
        import asyncio
        from unittest.mock import patch, MagicMock
        mock_result = MagicMock()
        mock_result.returncode = 0
        captured_cmd = []
        def capture(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return mock_result
        with patch("automations.subprocess.run", side_effect=capture):
            asyncio.run(automations.run_remote_trigger_toggle("abc123", True))
        assert "RemoteTrigger" in " ".join(captured_cmd)
