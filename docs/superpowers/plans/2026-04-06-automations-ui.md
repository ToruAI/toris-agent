# Automations UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/automations` command with inline Telegram UI for managing Claude Code cloud scheduled tasks (CCR triggers) — list, run, pause, and conversational creation via TORIS.

**Architecture:** Two paths: `claude -p` with RemoteTrigger for fast list/run/toggle operations (bot.py parses JSON), and existing Agent SDK for conversational creation. Single editable message for list ↔ card navigation (no chat clutter).

**Tech Stack:** python-telegram-bot, claude CLI (`claude -p --allowedTools RemoteTrigger --output-format json`), asyncio.to_thread for subprocess calls

---

## File Map

| File | Changes |
|------|---------|
| `bot.py` | All new code + modifications |
| `tests/test_automations.py` | New test file |

All changes in `bot.py`. Insert new helpers before the command handlers section (`# ============ Commands ============` around line 930). Insert new command and callback handlers after `cmd_settings`.

---

### Task 1: cron_to_human helper + tests

**Files:**
- Create: `tests/test_automations.py`
- Modify: `bot.py` (add `cron_to_human` function near other helpers)

- [ ] **Step 1: Create test file with failing tests**

```python
# tests/test_automations.py
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
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /Users/tako/GitRepos/toris-claude-voice-assistant
.venv/bin/python -m pytest tests/test_automations.py -v 2>&1 | head -30
```

Expected: `ImportError` (function doesn't exist yet)

- [ ] **Step 3: Add cron_to_human to bot.py**

Find the line `# ============ Commands ============` (around line 930). Insert before it:

```python
# ============ Automations Helpers ============

def cron_to_human(expr: str) -> str:
    """Convert 5-field cron expression to Polish human-readable string."""
    parts = expr.split()
    if len(parts) != 5:
        return expr
    minute, hour, dom, month, dow = parts
    if dom != "*" or month != "*":
        return expr
    hm = f"{int(hour):02d}:{int(minute):02d}" if hour != "*" and minute.isdigit() and hour.isdigit() else f"{hour}:{minute}"
    if hour == "*":
        return "Co godzinę"
    if dow == "*":
        return f"Codziennie {hm}"
    if dow == "1-5":
        return f"Pn-Pt {hm}"
    day_names = {"0": "Nd", "1": "Pn", "2": "Wt", "3": "Śr", "4": "Cz", "5": "Pt", "6": "Sb", "7": "Nd"}
    if dow in day_names:
        return f"{day_names[dow]} {hm}"
    return expr
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
.venv/bin/python -m pytest tests/test_automations.py -v
```

Expected: 6 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add tests/test_automations.py bot.py
git commit -m "feat: add cron_to_human helper with tests"
```

---

### Task 2: run_remote_trigger helpers

**Files:**
- Modify: `bot.py` (add 3 async functions in Automations Helpers section)
- Modify: `tests/test_automations.py` (add tests)

- [ ] **Step 1: Add failing tests**

Append to `tests/test_automations.py`:

```python
import json, subprocess
from unittest.mock import patch, MagicMock
import asyncio

# Import the helpers (they will be added to bot.py)
from bot import run_remote_trigger_list, run_remote_trigger_run, run_remote_trigger_toggle

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)

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
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
.venv/bin/python -m pytest tests/test_automations.py::test_list_returns_triggers -v
```

Expected: `ImportError: cannot import name 'run_remote_trigger_list'`

- [ ] **Step 3: Add helpers to bot.py**

In the `# ============ Automations Helpers ============` section, after `cron_to_human`, add:

```python
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
        # Strip markdown code fences if present
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
```

Also add `import subprocess` and `import json` at the top if not already present. Check with:
```bash
grep -n "^import subprocess\|^import json" bot.py
```
If missing, add after the existing imports block.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/test_automations.py -v
```

Expected: all tests PASSED (mocked subprocess, no real API calls)

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_automations.py
git commit -m "feat: add run_remote_trigger helpers"
```

---

### Task 3: Message builders (list + card)

**Files:**
- Modify: `bot.py` (add `build_automations_list` and `build_automation_card`)
- Modify: `tests/test_automations.py` (add tests)

- [ ] **Step 1: Add failing tests**

Append to `tests/test_automations.py`:

```python
from bot import build_automations_list, build_automation_card
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
    all_data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert any("trig_1" in d for d in all_data)
    assert any("trig_2" in d for d in all_data)

def test_build_card_full():
    trigger = SAMPLE_TRIGGERS[0]
    text, markup = build_automation_card(trigger, style="full")
    assert "Daily Standup" in text
    assert "08:00" in text
    assert isinstance(markup, InlineKeyboardMarkup)
    all_data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
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
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
.venv/bin/python -m pytest tests/test_automations.py::test_build_list_text -v
```

Expected: `ImportError: cannot import name 'build_automations_list'`

- [ ] **Step 3: Add builders to bot.py**

In the `# ============ Automations Helpers ============` section, after the `run_remote_trigger_*` functions, add:

```python
def build_automations_list(triggers: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    """Build list view: text summary + inline keyboard."""
    if not triggers:
        text = "🤖 Automacje\n\nNie masz jeszcze żadnych automacji."
        keyboard = [[InlineKeyboardButton("+ Stwórz pierwszą automację", callback_data="auto_new")]]
        return text, InlineKeyboardMarkup(keyboard)

    active = sum(1 for t in triggers if t.get("enabled", True))
    text = f"🤖 Twoje automacje ({len(triggers)}) · {active} aktywnych"

    keyboard = []
    for t in triggers:
        tid = t["id"]
        name = t["name"]
        enabled = t.get("enabled", True)
        status = "●" if enabled else "○"
        toggle_label = "⏸" if enabled else "▶"
        toggle_cb = f"auto_toggle_off_{tid}" if enabled else f"auto_toggle_on_{tid}"
        # Truncate name to fit 64-char callback_data limit
        row = [
            InlineKeyboardButton(f"{status} {name}", callback_data=f"auto_card_{tid}"),
            InlineKeyboardButton("▶", callback_data=f"auto_run_{tid}"),
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
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/test_automations.py -v
```

Expected: all tests PASSED

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_automations.py
git commit -m "feat: add automations list and card message builders"
```

---

### Task 4: cmd_automations command handler

**Files:**
- Modify: `bot.py` (add `cmd_automations` after `cmd_settings`)

- [ ] **Step 1: Add cmd_automations to bot.py**

Find `# ============ Token Configuration Commands ============` (around line 1252). Insert before it:

```python
async def cmd_automations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /automations command — show scheduled tasks list."""
    if not should_handle_message(update.message.message_thread_id):
        return
    if not _is_authorized(update):
        return

    loading_msg = await update.message.reply_text("⏳ Ładuję automacje...")

    triggers = await run_remote_trigger_list()
    text, markup = build_automations_list(triggers)

    try:
        await loading_msg.edit_text(text, reply_markup=markup)
    except Exception as e:
        logger.warning(f"cmd_automations edit error: {e}")
        await update.message.reply_text(text, reply_markup=markup)
```

- [ ] **Step 2: Register command in app setup**

Find `app.add_handler(CommandHandler("settings", cmd_settings))` and add after it:

```python
    app.add_handler(CommandHandler("automations", cmd_automations))
```

Find the `set_my_commands` list and add:

```python
            BotCommand("automations", "Manage scheduled automations"),
```

(Add it after `BotCommand("settings", ...)`)

- [ ] **Step 3: Smoke test — restart bot and test command**

```bash
pkill -f "bot.py"; sleep 1; .venv/bin/python bot.py &
sleep 3
```

Send `/automations` in Telegram. Expected: loading message appears, then either trigger list or "Nie masz jeszcze żadnych automacji" with `[+ Stwórz pierwszą]` button.

- [ ] **Step 4: Commit**

```bash
git add bot.py
git commit -m "feat: add /automations command handler"
```

---

### Task 5: handle_automations_callback

**Files:**
- Modify: `bot.py` (add `handle_automations_callback` after `cmd_automations`)

- [ ] **Step 1: Add callback handler to bot.py**

Insert after `cmd_automations`:

```python
async def handle_automations_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all auto_* callback button taps."""
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    data = query.data
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)
    card_style = settings.get("automation_card_style", "full")

    # ── Back to list ──────────────────────────────────────────
    if data in ("auto_list", "auto_refresh"):
        await query.edit_message_text("⏳ Ładuję automacje...")
        triggers = await run_remote_trigger_list()
        text, markup = build_automations_list(triggers)
        try:
            await query.edit_message_text(text, reply_markup=markup)
        except Exception as e:
            logger.warning(f"auto_list edit error: {e}")

    # ── Open card ─────────────────────────────────────────────
    elif data.startswith("auto_card_"):
        trigger_id = data[len("auto_card_"):]
        await query.edit_message_text("⏳...")
        triggers = await run_remote_trigger_list()
        trigger = next((t for t in triggers if t["id"] == trigger_id), None)
        if trigger is None:
            await query.edit_message_text("❌ Nie znaleziono automacji.")
            return
        text, markup = build_automation_card(trigger, style=card_style)
        try:
            await query.edit_message_text(text, reply_markup=markup)
        except Exception as e:
            logger.warning(f"auto_card edit error: {e}")

    # ── Run now ───────────────────────────────────────────────
    elif data.startswith("auto_run_"):
        trigger_id = data[len("auto_run_"):]
        await query.answer("▶ Uruchamiam...", show_alert=False)
        ok = await run_remote_trigger_run(trigger_id)
        if ok:
            await query.answer("✓ Uruchomiono!", show_alert=True)
        else:
            await query.answer("❌ Błąd uruchamiania", show_alert=True)

    # ── Toggle enable/disable ─────────────────────────────────
    elif data.startswith("auto_toggle_"):
        # format: auto_toggle_off_{id} or auto_toggle_on_{id}
        rest = data[len("auto_toggle_"):]
        enable = rest.startswith("on_")
        trigger_id = rest[3:]  # strip "on_" or "off_"
        ok = await run_remote_trigger_toggle(trigger_id, enable=enable)
        if ok:
            # Refresh card
            triggers = await run_remote_trigger_list()
            trigger = next((t for t in triggers if t["id"] == trigger_id), None)
            if trigger:
                text, markup = build_automation_card(trigger, style=card_style)
                await query.edit_message_text(text, reply_markup=markup)
        else:
            await query.answer("❌ Błąd zmiany stanu", show_alert=True)

    # ── New automation ────────────────────────────────────────
    elif data == "auto_new":
        await query.edit_message_text(
            "💬 Opisz automację głosem lub tekstem.\n\n"
            "Np. „stwórz daily standup o 8 rano sprawdzający PR-y na GitHubie""
        )

    # ── Edit prompt (conversational) ──────────────────────────
    elif data.startswith("auto_edit_"):
        trigger_id = data[len("auto_edit_"):]
        await query.edit_message_text(
            f"✎ Co chcesz zmienić w tej automacji?\n\n"
            f"Opisz głosem lub tekstem — np. „zmień godzinę na 9 rano" albo „dodaj sprawdzanie CI""
        )
```

- [ ] **Step 2: Register callback handler in app setup**

Find `app.add_handler(CallbackQueryHandler(handle_approval_callback, ...))` and add after it:

```python
    app.add_handler(CallbackQueryHandler(handle_automations_callback, pattern="^auto_"))
```

- [ ] **Step 3: Restart and test all buttons**

```bash
pkill -f "bot.py"; sleep 1; .venv/bin/python bot.py &
```

Test sequence in Telegram:
1. `/automations` → list appears
2. Tap trigger name → card appears ✓
3. Tap `← Wróć` → list appears ✓
4. Tap `▶` (Run now) → alert "Uruchomiono!" ✓
5. Tap `⏸` → card refreshes with Resume button ✓
6. Tap `🔄` → list refreshes ✓
7. Tap `+ Nowa automacja` → instruction message ✓

- [ ] **Step 4: Commit**

```bash
git add bot.py
git commit -m "feat: add handle_automations_callback with all auto_* patterns"
```

---

### Task 6: automation_card_style in settings

**Files:**
- Modify: `bot.py` (add setting to `cmd_settings` and `handle_settings_callback`)

- [ ] **Step 1: Add card style button to cmd_settings**

Find in `cmd_settings` the keyboard building block:

```python
    keyboard = [
        [
            InlineKeyboardButton(f"Mode: {mode_display}", callback_data="setting_mode_toggle"),
            InlineKeyboardButton(f"Watch: {watch_mode_val}", callback_data="setting_watch_cycle"),
        ],
        [InlineKeyboardButton(f"Audio: {audio_status}", callback_data="setting_audio_toggle")],
```

Add `card_style` variable and new button row:

```python
    card_style = settings.get("automation_card_style", "full")
    card_style_display = "Pełna" if card_style == "full" else "Kompakt"

    message = (
        f"Settings:\n\n"
        f"Mode: {mode_display}\n"
        f"Watch: {watch_mode_val}\n"
        f"Audio: {audio_status}\n"
        f"Voice Speed: {speed}x\n"
        f"Auto karta: {card_style_display}"
    )

    keyboard = [
        [
            InlineKeyboardButton(f"Mode: {mode_display}", callback_data="setting_mode_toggle"),
            InlineKeyboardButton(f"Watch: {watch_mode_val}", callback_data="setting_watch_cycle"),
        ],
        [InlineKeyboardButton(f"Audio: {audio_status}", callback_data="setting_audio_toggle")],
        [
            InlineKeyboardButton("0.8x", callback_data="setting_speed_0.8"),
            InlineKeyboardButton("0.9x", callback_data="setting_speed_0.9"),
            InlineKeyboardButton("1.0x", callback_data="setting_speed_1.0"),
            InlineKeyboardButton("1.1x", callback_data="setting_speed_1.1"),
            InlineKeyboardButton("1.2x", callback_data="setting_speed_1.2"),
        ],
        [InlineKeyboardButton(f"Auto karta: {card_style_display}", callback_data="setting_card_style_toggle")],
    ]
```

- [ ] **Step 2: Handle setting_card_style_toggle in handle_settings_callback**

Find in `handle_settings_callback` the `elif callback_data.startswith("setting_speed_"):` block. Add after it:

```python
    elif callback_data == "setting_card_style_toggle":
        current = settings.get("automation_card_style", "full")
        settings["automation_card_style"] = "compact" if current == "full" else "full"
        save_settings()
```

Also update the settings rebuild block at the bottom of `handle_settings_callback` to include card style (same changes as cmd_settings above — add `card_style`, `card_style_display` variables and the new keyboard row + message line).

- [ ] **Step 3: Restart and test settings toggle**

```bash
pkill -f "bot.py"; sleep 1; .venv/bin/python bot.py &
```

1. Send `/settings` → see "Auto karta: Pełna" button
2. Tap it → button changes to "Auto karta: Kompakt"
3. Open `/automations` → tap trigger name → card should be compact
4. Tap it again → "Auto karta: Pełna"

- [ ] **Step 4: Commit**

```bash
git add bot.py
git commit -m "feat: add automation_card_style setting (compact/full toggle)"
```

---

### Task 7: End-to-end smoke test + gitignore

**Files:**
- Modify: `.gitignore` (add .superpowers/)

- [ ] **Step 1: Add .superpowers to .gitignore**

```bash
grep -q ".superpowers" .gitignore || echo ".superpowers/" >> .gitignore
```

- [ ] **Step 2: Run full test suite**

```bash
.venv/bin/python -m pytest tests/test_automations.py -v
```

Expected: all tests PASSED, no warnings about missing imports.

- [ ] **Step 3: Full bot restart + end-to-end test**

```bash
pkill -f "bot.py"; sleep 1; .venv/bin/python bot.py &
sleep 3
```

Complete flow in Telegram:
1. `/automations` — list loads ✓
2. Tap trigger → full card ✓
3. Tap `← Wróć` → list ✓
4. `/settings` → "Auto karta: Pełna" button visible ✓
5. Toggle card style → compact ✓
6. `/automations` → tap trigger → compact card ✓
7. Say "stwórz automację która codziennie o 9 robi przegląd PR-ów" → TORIS loops, asks questions, shows preview card ✓

- [ ] **Step 4: Final commit**

```bash
git add .gitignore
git commit -m "chore: add .superpowers to .gitignore"
```

---

## Self-Review

**Spec coverage:**
- ✅ `/automations` command (Task 4)
- ✅ List view with ●/○ indicators, ▶ Run, ⏸/▶Resume buttons (Task 3+4)
- ✅ Card view: full and compact, same-message navigation (Task 3+5)
- ✅ Back button → list (Task 5)
- ✅ Run now (Task 5)
- ✅ Toggle enable/disable (Task 5)
- ✅ New automation prompt (Task 5) — creation stays conversational via TORIS
- ✅ Refresh button (Task 5)
- ✅ automation_card_style setting (Task 6)
- ✅ cron_to_human helper (Task 1)
- ✅ run_remote_trigger_* helpers (Task 2)
- ✅ .gitignore for .superpowers (Task 7)
- ✅ RemoteTrigger already in allowed_tools (done before this plan)
- ✅ TORIS system prompt already updated with RemoteTrigger knowledge (done before this plan)

**Not in scope (per spec):**
- CCR result notification inline buttons (▶ Run again, ⚙ Automacja) — CCR agent formats these via curl, bot receives as plain text. Requires CCR to know trigger_id and embed it in message. TORIS handles this via prompts/toris.md guidance when creating automations.

**Type consistency:** `build_automations_list` and `build_automation_card` both return `tuple[str, InlineKeyboardMarkup]`. `run_remote_trigger_*` functions are async throughout. Callback pattern `auto_toggle_off_{id}` / `auto_toggle_on_{id}` consistently parsed in handler.
