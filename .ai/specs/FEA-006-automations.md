---
id: FEA-006
type: feature
status: active
severity: low
validated: 2026-04-07
---

# Automations
/automations lists RemoteTrigger scheduled tasks. Each can be toggled on/off or run immediately.
Built on the Claude Code RemoteTrigger API — bot is a UI layer only.

## Invariants
- /automations fetches the trigger list asynchronously; shows "⏳ Ładuję automacje..." while loading
- Empty trigger list → shows "Brak automacji" (or equivalent)
- Each trigger card renders: name, schedule, enabled status, last run
- Card style controlled by `automation_card_style` setting: full vs compact
- Toggle button flips enabled state via RemoteTrigger API; card refreshes
- Run Now button triggers immediate execution
- Only authorized users can interact with automation buttons

## Test
- /automations → list appears (or "brak" if none configured)
- Tap toggle on automation → enabled state flips, card updates
- automation_card_style=compact → cards show minimal info
- automation_card_style=full → cards show full detail

## Changelog
- 2026-04-07: Spec created; automation_card_style setting added
