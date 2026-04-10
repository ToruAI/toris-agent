# Changelog

## [Unreleased] — 2026-04-10

First public release. Feature snapshot:

### Core
- Voice-first Telegram bot with full Claude Agent SDK integration
- Session management: `/new`, `/continue`, `/switch`, `/search`, `/compact`
- Approval modes: auto ("Go All") or per-tool confirmation
- Watch mode: live stream of tool calls — Off / Live / Debug
- Sandboxed writes and command execution

### Voice
- ElevenLabs TTS + STT (Scribe)
- OpenAI TTS + STT (Whisper, gpt-4o-mini-tts, gpt-4o-transcribe)
- Per-user provider preference via `/settings`
- Provider auto-detection from available keys

### Setup
- Conversational `/setup` via Telegram — no SSH needed
- Runtime token verification against the live API before saving
- API Key *or* OAuth subscription tokens supported
- Back button on every onboarding step
- Token hygiene: refuses to save if the chat message can't be deleted

### Ops
- Docker + systemd deployment recipes
- Per-user allowlists + admin gating
- Atomic state persistence (unique tmp files, safe under concurrency)
- Rate limiting (2s cooldown + 10/min per user)
- Health checks for all providers
- Cancel events for long-running requests

### Architecture
- Modular handlers (`session` / `admin` / `messages`)
- Single-source config + thread-safe `StateManager`
- 171 unit tests passing

### Security
- Anonymous / channel posts denied when `ALLOWED_USER_IDS` is set
- Tokens routed to the correct env var (`ANTHROPIC_API_KEY` vs `CLAUDE_CODE_OAUTH_TOKEN`) based on prefix
- Sandbox isolation for all write / exec operations
- No secrets committed to repo; comprehensive `.gitignore`
