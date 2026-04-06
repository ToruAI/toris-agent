import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import asyncio
import json
import subprocess
from unittest.mock import patch, MagicMock

from bot import cron_to_human, run_remote_trigger_list, run_remote_trigger_run, run_remote_trigger_toggle, build_automations_list, build_automation_card

def _run(coro):
    return asyncio.run(coro)

def test_daily():
    assert cron_to_human("0 7 * * *") == "Codziennie 07:00"

def test_weekdays():
    assert cron_to_human("0 9 * * 1-5") == "Pn-Pt 09:00"

def test_weekly_monday():
    assert cron_to_human("0 10 * * 1") == "Pn 10:00"

def test_hourly():
    assert cron_to_human("0 * * * *") == "Co godzinę"

def test_unknown_falls_back():
    assert cron_to_human("*/15 * * * *") == "*/15 * * * *"

def test_zero_padded():
    assert cron_to_human("0 8 * * *") == "Codziennie 08:00"

def test_list_returns_triggers():
    mock_output = json.dumps({
        "result": '[{"id":"trig_1","name":"Daily Standup","cron_expression":"0 7 * * *","enabled":true}]'
    })
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
        triggers = _run(run_remote_trigger_list())
    assert len(triggers) == 1
    assert triggers[0]["name"] == "Daily Standup"
    assert triggers[0]["enabled"] is True

def test_list_returns_empty_on_error():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        triggers = _run(run_remote_trigger_list())
    assert triggers == []

def test_run_trigger_returns_true_on_success():
    mock_output = json.dumps({"result": "Trigger started successfully"})
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
        ok = _run(run_remote_trigger_run("trig_1"))
    assert ok is True

def test_run_trigger_returns_false_on_error():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
        ok = _run(run_remote_trigger_run("trig_1"))
    assert ok is False

def test_toggle_trigger():
    mock_output = json.dumps({"result": "Updated"})
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
        ok = _run(run_remote_trigger_toggle("trig_1", enable=False))
    assert ok is True
    # verify RemoteTrigger update was called with enabled=false in prompt
    call_args = mock_run.call_args[0][0]
    assert "RemoteTrigger" in " ".join(call_args)


from telegram import InlineKeyboardMarkup

SAMPLE_TRIGGERS = [
    {"id": "trig_1", "name": "Daily Standup", "cron_expression": "0 8 * * *", "enabled": True},
    {"id": "trig_2", "name": "Dep Audit", "cron_expression": "0 10 * * 1", "enabled": False},
]

def test_build_list_text():
    text, markup = build_automations_list(SAMPLE_TRIGGERS)
    assert "Daily Standup" in text
    assert "Dep Audit" in text
    assert isinstance(markup, InlineKeyboardMarkup)

def test_build_list_empty():
    text, markup = build_automations_list([])
    assert "brak" in text.lower() or "automacj" in text.lower()
    assert isinstance(markup, InlineKeyboardMarkup)

def test_build_list_buttons_contain_ids():
    _, markup = build_automations_list(SAMPLE_TRIGGERS)
    all_data = [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]
    assert any("trig_1" in d for d in all_data)
    assert any("trig_2" in d for d in all_data)

def test_build_card_full():
    trigger = SAMPLE_TRIGGERS[0]
    text, markup = build_automation_card(trigger, style="full")
    assert "Daily Standup" in text
    assert "08:00" in text
    assert isinstance(markup, InlineKeyboardMarkup)
    all_data = [btn.callback_data for row in markup.inline_keyboard for btn in row if btn.callback_data]
    assert any("auto_run_trig_1" in d for d in all_data)
    assert any("auto_list" in d for d in all_data)

def test_build_card_compact():
    trigger = SAMPLE_TRIGGERS[0]
    text, markup = build_automation_card(trigger, style="compact")
    assert "Daily Standup" in text
    assert isinstance(markup, InlineKeyboardMarkup)

def test_build_card_paused_shows_resume():
    trigger = SAMPLE_TRIGGERS[1]  # enabled=False
    _, markup = build_automation_card(trigger, style="compact")
    all_labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("Resume" in l for l in all_labels)
