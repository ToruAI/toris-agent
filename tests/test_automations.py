import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from bot import cron_to_human

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
