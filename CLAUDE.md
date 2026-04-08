# TORIS Claude Voice Assistant — Project Instructions

## Specs

Behavioral specs live in `.ai/specs/`. Read the relevant spec before touching any feature.
Update `validated` date and spec body whenever behavior changes.
See `.ai/specs/AGENTS.md` for the full spec maintenance protocol.

## Git commits

Do NOT add "Co-Authored-By" lines. Keep messages clean.

## Test convention

Tests use `asyncio.run()` — NOT `@pytest.mark.asyncio`.
Run with: `source venv/bin/activate && pytest tests/ -v`

## Architecture

- `bot.py` — handler registration + startup only (~470 lines)
- `handlers/session.py` — /start /new /cancel /compact /continue /sessions /switch /status /search
- `handlers/admin.py` — /setup /claude_token /elevenlabs_key /openai_key + settings + approval callbacks
- `handlers/messages.py` — voice / text / photo / automations callbacks
- `auth.py` — guards: should_handle_message, _is_authorized, _is_admin, check_rate_limit
- `state_manager.py` — StateManager singleton, user sessions + settings
- `shared_state.py` — pending_approvals, cancel_events (cross-module)
- `claude_service.py` — call_claude, WorkingIndicator, format_tool_call
- `voice_service.py` — TTS, STT, is_valid_transcription
- `automations.py` — RemoteTrigger list/toggle/run

## Import rule

Handlers import from `state_manager`, `auth`, `shared_state`, `claude_service`, `voice_service`, `config`.
Never import from `bot.py` — prevents circular imports.
