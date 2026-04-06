"""
Automation management helpers.

cron parsing, RemoteTrigger subprocess calls, and Telegram UI builders.
No bot globals — pure functions + subprocess calls.
"""
import asyncio
import json
import logging
import subprocess

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


def cron_to_human(expr: str) -> str:
    """Convert 5-field cron expression to Polish human-readable string."""
    parts = expr.split()
    if len(parts) != 5:
        return expr
    minute, hour, dom, month, dow = parts
    if dom != "*" or month != "*":
        return expr
    hm = (
        f"{int(hour):02d}:{int(minute):02d}"
        if hour != "*" and minute.isdigit() and hour.isdigit()
        else f"{hour}:{minute}"
    )
    if hour == "*" and minute == "0":
        return "Co godzinę"
    if hour == "*":
        return expr
    if dow == "*":
        return f"Codziennie {hm}"
    if dow == "1-5":
        return f"Pn-Pt {hm}"
    day_names = {"0": "Nd", "1": "Pn", "2": "Wt", "3": "Śr", "4": "Cz", "5": "Pt", "6": "Sb", "7": "Nd"}
    if dow in day_names:
        return f"{day_names[dow]} {hm}"
    return expr


async def run_remote_trigger_list() -> list[dict]:
    """Fetch all scheduled triggers via claude -p. Returns list of trigger dicts."""
    prompt = (
        "List all my scheduled remote triggers using RemoteTrigger tool with action='list'. "
        "Return ONLY a JSON array where each item has: id (string), name (string), "
        "cron_expression (string), enabled (boolean). No other text."
    )
    cmd = ["claude", "-p", prompt, "--allowedTools", "RemoteTrigger", "--output-format", "json"]
    try:
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            logger.warning(f"run_remote_trigger_list failed: {result.stderr[:200]}")
            return []
        data = json.loads(result.stdout)
        raw = data.get("result", "[]")
        raw = raw.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
            raw = raw.rstrip("`").strip()
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"run_remote_trigger_list exception: {e}")
        return []


async def run_remote_trigger_run(trigger_id: str) -> bool:
    """Trigger a scheduled task to run immediately via claude -p."""
    prompt = f"Run the scheduled remote trigger with ID '{trigger_id}' immediately using RemoteTrigger tool with action='run'."
    cmd = ["claude", "-p", prompt, "--allowedTools", "RemoteTrigger", "--output-format", "json"]
    try:
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0
    except Exception as e:
        logger.warning(f"run_remote_trigger_run exception: {e}")
        return False


async def run_remote_trigger_toggle(trigger_id: str, enable: bool) -> bool:
    """Enable or disable a scheduled trigger via claude -p."""
    state = "enabled" if enable else "disabled"
    prompt = (
        f"Update the scheduled remote trigger with ID '{trigger_id}' using RemoteTrigger tool "
        f"with action='update'. Set enabled={str(enable).lower()}. "
        f"The trigger should be {state} after this call."
    )
    cmd = ["claude", "-p", prompt, "--allowedTools", "RemoteTrigger", "--output-format", "json"]
    try:
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0
    except Exception as e:
        logger.warning(f"run_remote_trigger_toggle exception: {e}")
        return False


def build_automations_list(triggers: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    """Build list view: text summary + inline keyboard."""
    if not triggers:
        text = "🤖 Automacje\n\nNie masz jeszcze żadnych automacji."
        keyboard = [[InlineKeyboardButton("+ Stwórz pierwszą automację", callback_data="auto_new")]]
        return text, InlineKeyboardMarkup(keyboard)

    active = sum(1 for t in triggers if t.get("enabled", True))
    names = "\n".join(
        f"{'●' if t.get('enabled', True) else '○'} {t['name']}" for t in triggers
    )
    text = f"🤖 Twoje automacje ({len(triggers)}) · {active} aktywnych\n\n{names}"

    keyboard = []
    for t in triggers:
        tid = t["id"]
        name = t["name"]
        enabled = t.get("enabled", True)
        status = "●" if enabled else "○"
        toggle_label = "⏸" if enabled else "▶"
        toggle_cb = f"auto_toggle_off_{tid}" if enabled else f"auto_toggle_on_{tid}"
        row = [
            InlineKeyboardButton(f"{status} {name}", callback_data=f"auto_card_{tid}"),
            InlineKeyboardButton("▶ Run", callback_data=f"auto_run_{tid}"),
            InlineKeyboardButton(toggle_label, callback_data=toggle_cb),
        ]
        keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton("+ Nowa automacja", callback_data="auto_new"),
        InlineKeyboardButton("🔄", callback_data="auto_refresh"),
    ])
    return text, InlineKeyboardMarkup(keyboard)


def build_automation_card(trigger: dict, style: str = "full") -> tuple[str, InlineKeyboardMarkup]:
    """Build card view for a single trigger."""
    tid = trigger["id"]
    name = trigger["name"]
    enabled = trigger.get("enabled", True)
    cron = trigger.get("cron_expression", "")
    schedule_human = cron_to_human(cron)
    status_icon = "●" if enabled else "○"
    status_text = "Aktywna" if enabled else "Wstrzymana"
    toggle_label = "⏸ Pause" if enabled else "▶ Resume"
    toggle_cb = f"auto_toggle_off_{tid}" if enabled else f"auto_toggle_on_{tid}"

    if style == "compact":
        text = (
            f"🤖 {name}\n"
            f"{status_icon} {status_text} · {schedule_human}"
        )
        keyboard = [
            [
                InlineKeyboardButton("▶ Run now", callback_data=f"auto_run_{tid}"),
                InlineKeyboardButton(toggle_label, callback_data=toggle_cb),
                InlineKeyboardButton("✎ Edit", callback_data=f"auto_edit_{tid}"),
                InlineKeyboardButton("✕", url="https://claude.ai/code/scheduled"),
            ],
            [InlineKeyboardButton("← Wróć", callback_data="auto_list")],
        ]
    else:  # full
        text = (
            f"🤖 {name}\n\n"
            f"HARMONOGRAM\n{schedule_human}\n\n"
            f"STATUS\n{status_icon} {status_text}"
        )
        keyboard = [
            [
                InlineKeyboardButton("▶ Run now", callback_data=f"auto_run_{tid}"),
                InlineKeyboardButton(toggle_label, callback_data=toggle_cb),
            ],
            [
                InlineKeyboardButton("✎ Edit prompt", callback_data=f"auto_edit_{tid}"),
                InlineKeyboardButton("✕ Usuń →", url="https://claude.ai/code/scheduled"),
            ],
            [InlineKeyboardButton("← Wróć do listy", callback_data="auto_list")],
        ]

    return text, InlineKeyboardMarkup(keyboard)
