---
id: SVC-001
type: service
status: active
severity: critical
validated: 2026-04-07
---

# Voice Pipeline
Telegram voice note → STT transcription → Claude → TTS audio response.
NOT a text chat wrapper — audio in, audio out is the primary contract.

## Invariants
- Voice message always triggers STT first; Claude never receives raw audio
- If transcription is empty, whitespace-only, or starts with `[Transcription error` → request is dropped silently (no error sent to user)
- Claude response is converted to TTS and sent as a voice note when `audio_enabled = true`
- If TTS fails silently (returns None) → text fallback with 🔇 prefix is sent instead
- Response exceeding `MAX_VOICE_RESPONSE_CHARS` is truncated before TTS
- Typing indicator fires every 4s during Claude processing
- Session ID is created or continued transparently — user never manages it here

## Test
- Send voice note "Hello" → receive voice note reply
- Send voice note in Polish → reply is in Polish
- Send voice note that transcribes to empty string → no reply sent
- Disable Audio → send voice note → receive text reply (no audio)
- TTS provider = none → voice note input still answers in text

## Changelog
- 2026-04-07: Extracted from bot.py into handlers/messages.py
