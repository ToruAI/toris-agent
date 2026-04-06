# Automations UI Design — TORIS Telegram Bot

**Date:** 2026-04-06
**Status:** Approved

---

## Summary

Add a `/automations` command and inline UI to TORIS Telegram bot for managing Claude Code cloud scheduled tasks (CCR triggers). Users can list, run, pause, and create automations directly from Telegram — using voice or text. TORIS agent is fully aware of RemoteTrigger and handles creation conversationally.

---

## Architecture

### Two code paths

**Path 1 — Simple operations (list, run, toggle)**
`claude -p "use RemoteTrigger action=list, return JSON" --allowedTools RemoteTrigger --output-format json`
bot.py parses JSON → renders native Telegram inline buttons.
Fast, cheap, no full Agent SDK session overhead.

**Path 2 — Creation (conversational)**
Full Agent SDK session via existing `call_claude`. TORIS loops conversationally until aligned with user, then shows preview card with `[✓ Stwórz]` `[✗ Zmień]`. On confirm → RemoteTrigger create.

### New components in bot.py

| Component | Description |
|-----------|-------------|
| `cmd_automations` | Command handler for `/automations` |
| `handle_automations_callback` | Callback handler — patterns: `auto_card_*`, `auto_run_*`, `auto_toggle_*`, `auto_back`, `auto_new` |
| `run_remote_trigger(action, **kwargs)` | Helper: `claude -p` + JSON parse for simple ops |

### Settings

| Setting | Values | Default |
|---------|--------|---------|
| `automation_card_style` | `"compact"` \| `"full"` | `"full"` |

---

## UI: `/automations` — List → Card Navigation

### State 1: List view (single editable message)

```
🤖 Twoje automacje (3)

[● Daily Standup]  [▶]  [⏸]
[● PR Review]      [▶]  [⏸]
[○ Dep Audit]      [▶]  [▶ Resume]

[+ Nowa automacja]  [🔄]
```

- `●` = active (green), `○` = paused (red)
- Tap on name → edits same message to show full card
- `▶` = Run now, `⏸` = Pause, `▶ Resume` = re-enable
- `🔄` = refresh (re-fetches list, edits same message)
- Empty state: "Nie masz jeszcze żadnych automacji" + `[+ Stwórz pierwszą]`

### State 2: Card view (same message, tap name to open)

**Full card (default setting):**
```
🤖 Daily Standup

HARMONOGRAM
Codziennie o 08:00 (Warsaw)

STATUS
● Aktywna

OSTATNI RUN
Dziś 08:02 · ✓ Sukces

NASTĘPNY RUN
Jutro 08:00

[▶ Run now]  [⏸ Pause]
[✎ Edit prompt]  [✕ Usuń →]
[← Wróć do listy]
```

**Compact card (optional setting):**
```
🤖 Daily Standup
● Aktywna · Codziennie 08:00 (Warsaw)
Last: dziś 08:02 ✓ · Next: jutro 08:00

[▶ Run now]  [⏸ Pause]  [✎ Edit]  [✕]
[← Wróć]
```

**Notes:**
- `✕ Usuń →` links to `claude.ai/code/scheduled` (API doesn't support delete)
- `✎ Edit prompt` → TORIS asks conversationally "co zmienić?"
- Back button → edits message back to list view

---

## Creation Flow

5-step conversational loop:

1. **User initiates** (voice or text): "stwórz automation daily standup o 8"
2. **TORIS asks** missing info one question at a time: repo? godzina? co sprawdzać?
3. **Alignment loop**: TORIS re-confirms understanding, loops until on the same page
4. **Preview card + confirm**:
   ```
   🤖 Daily Standup
   Harmonogram: Codziennie 08:00 (Warsaw)
   Repo: toris-claude-voice-assistant
   Zadanie: Sprawdza PR-y, CI, komentarze → wyniki do Telegrama

   [✓ Stwórz]  [✗ Zmień]
   ```
5. **Created**: TORIS confirms "Gotowe. Jutro o 08:00 pierwsze uruchomienie." + full active card

Creation is handled entirely by the TORIS Agent SDK session (existing `call_claude`). TORIS uses RemoteTrigger tool directly. No special command needed — user just says it naturally.

---

## CCR Agent Result Notifications

CCR agents send results back via Telegram Bot API (`curl -X POST .../sendMessage`). Format:

**Success:**
```
✓ Daily Standup · 08:02
Automation run zakończony

Otwarte PR-y (2)
• feat/telegram-token-setup — 3 nowe komentarze, CI ✓
• fix/typing-indicator — czeka na review, CI ✓

CI Status: Wszystkie checks przeszły ✓

Nowe komentarze: @tako zostawił komentarz na PR #12

[▶ Run again]  [⚙ Automacja]
```

- `▶ Run again` → triggers `RemoteTrigger run` for that trigger_id (embedded in the curl payload by CCR agent)
- `⚙ Automacja` → sends `/automations` and opens the card for that trigger directly

**Error:**
```
✗ Daily Standup · 08:02
Automation run nie powiódł się

Błąd: GitHub App nie ma dostępu do repo.
Zainstaluj na: claude.ai/code/onboarding

[▶ Spróbuj znów]  [⚙ Automacja]
```

"Start" notification (optional — only if bot is online when CCR fires) is skipped by default since CCR runs independently in the cloud.

---

## prompts/toris.md Changes (already done)

TORIS already has RemoteTrigger knowledge added:
- How to list, create, update, run triggers
- CCR environment ID: `env_01LQ699o5DsWgFTALuk1pumX` (Toru)
- How to embed Telegram token + chat_id in CCR prompt for result delivery
- Confirmation loop: always show preview card before creating

---

## bot.py Changes Needed

1. Add `cmd_automations` command handler
2. Add `handle_automations_callback` callback handler
3. Add `run_remote_trigger(action, **kwargs) -> dict` helper (async, uses `claude -p`)
4. Register `/automations` in `setMyCommands` list
5. Add `auto_*` pattern to `app.add_handler(CallbackQueryHandler(...))`
6. Support `automation_card_style` in `load_settings` / `cmd_settings`

---

## Telegram API Constraints

- Only the **latest message with inline keyboard** has active buttons — older messages' buttons stop working after edit
- Solution: always edit the **same message** (list ↔ card navigation)
- Max inline keyboard rows: ~10 (Telegram limit)
- Max button label: 64 chars

---

## CCR Prompt Template (embedded by TORIS when creating)

```
[Task description from user]

When done, send results to Telegram:
curl -s -X POST "https://api.telegram.org/bot<TOKEN>/sendMessage" \
  -d "chat_id=<CHAT_ID>&text=<formatted summary>"
```

TORIS gets TOKEN and CHAT_ID via `echo $TELEGRAM_BOT_TOKEN` and `echo $TELEGRAM_DEFAULT_CHAT_ID` using Bash tool.
