---
id: SVC-002
type: service
status: active
severity: high
validated: 2026-04-07
---

# Text Message Handling
Plain text messages → Claude → text reply (+ optional TTS).
Does NOT apply to commands (filtered by `~filters.COMMAND`).

## Invariants
- All non-command text is forwarded to Claude in the current session context
- If `compact_summary` exists in state → it is prepended as `<previous_session_summary>` and then removed
- Response >4000 chars is split into numbered chunks `[1/N]`, `[2/N]`, …
- First chunk replaces the "processing…" message; subsequent chunks are new replies
- TTS is sent if `audio_enabled = true` and TTS provider is configured
- Rate limit is checked before processing; rejected messages get an explanation

## Test
- Send "hello" → Claude replies
- Send same message twice fast → second gets "please wait Xs"
- Send message after /compact → compact_summary injected, then cleared from state
- Send 5000-char message → Claude replies in multiple chunks labelled [1/N]

## Changelog
- 2026-04-07: Extracted from bot.py into handlers/messages.py
