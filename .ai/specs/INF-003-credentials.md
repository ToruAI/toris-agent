---
id: INF-003
type: infra
status: active
severity: critical
validated: 2026-04-07
---

# Credential Management
API keys stored in `credentials.json` (0o600 permissions) and applied at runtime.
Credentials can be set via Telegram commands — messages are deleted immediately after receipt.

## Invariants
- The message containing the token/key is deleted before any reply is sent
- Token must start with `sk-ant-` — other formats are rejected with an error
- OpenAI key must start with `sk-` — other formats rejected
- ElevenLabs key must be ≥20 chars — shorter rejected
- Saved credentials are applied immediately (env var + live client reconfigured)
- `credentials.json` permissions are set to 0o600 after every write
- Corrupt or missing `credentials.json` returns `{}` without crashing
- /setup shows current status (✓ Set / ✗ Not set) for all three providers

## Test
- /claude_token sk-ant-abc → message deleted, "✓ Claude token saved"
- /claude_token bad-format → "Invalid token format"
- /claude_token (no args) → usage instructions
- /elevenlabs_key short → "key seems too short"
- credentials.json created with 0o600 after /claude_token
- Bot restart → saved credentials auto-applied

## Changelog
- 2026-04-07: Extracted to handlers/admin.py; test coverage added
