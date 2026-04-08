---
id: FEA-002
type: feature
status: active
severity: medium
validated: 2026-04-07
---

# User Settings & Persistence
Per-user preferences stored in `settings.json` and exposed via /settings inline keyboard.
Defaults applied on first access; legacy field names migrated transparently.

## Invariants
- Default values: audio_enabled=true, voice_speed=1.1, mode=go_all, watch_mode=off, automation_card_style=full
- Every toggle/change updates the dict in-place and calls `save_settings()` immediately
- Legacy fields `watch_enabled` / `show_activity` are migrated → `watch_mode` on first read and never written back
- Speed is validated to range [0.7, 1.2] before saving; out-of-range returns error toast
- `/settings` shows current values in button labels — no stale display after toggle
- Settings survive bot restart (loaded from disk on startup)

## Test
- /settings → shows correct defaults for new user
- Tap Audio button → label flips, next voice reply has no audio
- Tap Watch button three times → cycles OFF→LIVE→DEBUG→OFF
- Tap 0.8x → subsequent /settings shows 0.8x selected
- Restart bot → /settings shows same values as before restart
- User with legacy watch_enabled=true in settings.json → watch_mode=live after first /settings

## Changelog
- 2026-04-07: DEFAULT_USER_SETTINGS constant added; migration moved before defaults backfill
