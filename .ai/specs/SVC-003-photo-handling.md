---
id: SVC-003
type: service
status: active
severity: medium
validated: 2026-04-07
---

# Photo Message Handling
Photo → saved to sandbox → path sent to Claude as prompt context.
Claude receives the file path, NOT raw image bytes.

## Invariants
- Photo is saved to `SANDBOX_DIR/photo_<timestamp>.jpg` before Claude is called
- Prompt includes the saved path and (if present) the caption
- Without caption: prompt asks Claude to describe what it sees
- With caption: prompt includes "My message: <caption>"
- Same session/TTS flow applies as SVC-002

## Test
- Send photo without caption → Claude describes the image path
- Send photo with caption "what color is this?" → caption appears in Claude's context
- Filename format is `photo_YYYYMMDD_HHMMSS.jpg`

## Changelog
- 2026-04-07: Extracted from bot.py into handlers/messages.py
