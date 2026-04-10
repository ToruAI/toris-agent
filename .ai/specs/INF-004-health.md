---
id: INF-004
type: infra
status: active
severity: low
validated: 2026-04-07
---

# Health Check
/health reports live status of TTS, STT, Claude CLI, MCP servers, sandbox, and session state.
Performs real API calls — not a mock check.

## Invariants
- TTS check makes a real API call with text="test" to the active provider
- Claude check runs `claude -p "Say OK" --output-format json` as a subprocess
- MCP servers are read from `CLAUDE_SETTINGS_FILE` if set; each command is checked via `shutil.which`
- Missing or empty `CLAUDE_SETTINGS_FILE` → "CLAUDE_SETTINGS_FILE not configured"
- MCP server with `command` not in PATH → "MISSING"
- Response always includes: chat ID, topic ID, user ID, sandbox path, session count

## Test
- /health with working TTS → "OpenAI TTS: OK (N bytes, …)"
- /health with bad API key → "OpenAI TTS: FAILED - …"
- /health with npx in PATH → MCP server shows "OK"
- /health with missing binary → MCP server shows "MISSING"
- /health → always shows Chat ID and User ID

## Changelog
- 2026-04-07: Spec created; get_mcp_status remains in bot.py (called from cmd_health)
