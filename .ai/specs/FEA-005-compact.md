---
id: FEA-005
type: feature
status: active
severity: medium
validated: 2026-04-07
---

# Compact Session
/compact summarizes the current session via Claude, then resets to a fresh session
while injecting the summary into the next message as context.

## Invariants
- /compact requires an active session; fails with message if `current_session` is None
- Summary is obtained by calling Claude with the full session context
- After summary: `compact_summary` stored in state, `current_session` set to None
- On next user message: `compact_summary` is popped from state and prepended as `<previous_session_summary>…</previous_session_summary>`
- Summary is injected exactly once — removed from state after use
- Preview shown to user is truncated to 400 chars with "…"

## Test
- /compact with no session → "No active session to compact."
- /compact with session → processing message → summary preview shown
- Send message after compact → `<previous_session_summary>` in Claude's context, then gone on next message
- Second message after compact → no summary injected (already consumed)

## Changelog
- 2026-04-07: Spec created; implementation in handlers/session.py
