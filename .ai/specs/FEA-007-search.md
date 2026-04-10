---
id: FEA-007
type: feature
status: active
severity: medium
validated: 2026-04-08
---

# Session Search
/search <query> uses Claude to semantically rank sessions by relevance and returns clickable switch buttons.

## Invariants
- Query is passed to `claude -p --output-format json` with session metadata (id, name, up to 6 user messages each)
- SANDBOX_DIR is resolved (symlinks expanded) before constructing JSONL paths
- Claude `result` field is coerced to str before JSON extraction
- Direct `json.loads(result)` attempted first; falls back to finding `[...]` slice on failure
- Matched IDs are validated against user's own session list before displaying buttons
- Button label shows session name if set, else first 45 chars of first message, else short ID
- Excerpt displayed under session ID has `_`, `*`, `` ` `` escaped before Markdown wrapping
- Clicking a button calls handle_session_switch_callback → sets state["current_session"] → save_state()
- query.answer() is called before any state mutation in the callback

## Test
- /search with no args → usage hint
- /search with no sessions → "No sessions yet."
- Claude returns UUIDs not in user's sessions → not shown in results
- Claude returns malformed JSON → "No sessions found."
- Session name set → button label is the name
- Session unnamed → button label is first 45 chars of first message
- Excerpt contains underscores → escaped before `_excerpt_` Markdown wrap
- Progress message updated to "🔍 Asking Claude to search sessions... (up to 45s)" before subprocess call
