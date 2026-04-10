---
id: FEA-004
type: feature
status: active
severity: low
validated: 2026-04-07
---

# Watch Mode
Streams Claude tool activity to Telegram during processing.
Three levels: off (silent), live (tool names), debug (tool names + inputs).

## Invariants
- watch_mode=off → user sees only the final response
- watch_mode=live → each tool call sends a Telegram message with the tool name
- watch_mode=debug → each tool call sends name + truncated input JSON (≤500 chars)
- Watch messages are sent as separate messages, never replacing the "processing…" message
- Tool input truncation uses `format_tool_call()` which appends `...` at 500 chars
- WorkingIndicator fires every 5s regardless of watch_mode, editing the processing message

## Test
- watch_mode=off → long Claude call with tools → only final reply visible
- watch_mode=live → tool call → separate message appears with tool name
- watch_mode=debug → tool call with long input → input truncated at 500 chars with "..."
- WorkingIndicator updates "⏳ Still working..." every 5s during long call

## Changelog
- 2026-04-07: WorkingIndicator class extracted to claude_service.py
