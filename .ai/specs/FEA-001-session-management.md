---
id: FEA-001
type: feature
status: active
severity: high
validated: 2026-04-07
---

# Session Management
Per-user Claude session lifecycle: create, continue, list, switch.
Session IDs come from Claude SDK — the bot only stores and routes them.

## Invariants
- Each user has exactly one `current_session` at a time (can be None)
- `/new` clears `current_session`; next message creates a fresh Claude session
- `/new <name>` stores `pending_session_name`; consumed when session is created
- Session IDs are appended to `sessions[]` on first use; never duplicated
- `/switch <prefix>` matches by prefix — fails if 0 or 2+ matches
- State is persisted atomically after every mutation (`.tmp` then rename)
- `/compact` summarizes the current session, stores summary as `compact_summary`, resets `current_session`

## Test
- Fresh start → /status → "No active session"
- /new → message → /status → shows session prefix
- /new again → message → /sessions → two sessions listed
- /switch <first 4 chars> → /status → switched back
- /compact → summary shown → next message injects summary then clears it

## Changelog
- 2026-04-07: Extracted to handlers/session.py; state managed by StateManager
