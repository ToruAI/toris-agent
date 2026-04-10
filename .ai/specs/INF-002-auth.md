---
id: INF-002
type: infra
status: active
severity: high
validated: 2026-04-07
---

# Authorization
Two-level access control: chat authorization and admin authorization.
Topic filtering is an independent third gate for group/forum bots.

## Invariants
- `ALLOWED_CHAT_ID=0` disables chat restriction (accepts all chats)
- `ALLOWED_CHAT_ID≠0` → only messages from that chat ID are processed
- `ADMIN_USER_IDS=[]` → all authorized users are admins
- `ADMIN_USER_IDS≠[]` → only listed user IDs can run admin commands (/setup, /claude_token, etc.)
- `TOPIC_ID` set → messages from other threads are silently ignored
- `TOPIC_ID` not a valid integer → warning logged, all topics accepted
- Unauthorized users get no response (not even an error)

## Test
- ALLOWED_CHAT_ID=0 → any chat can talk to the bot
- ALLOWED_CHAT_ID=123 → message from chat 456 → no reply
- ADMIN_USER_IDS=[99] → user 100 sends /setup → no reply
- TOPIC_ID=42 → message from thread 99 → silently ignored
- TOPIC_ID="bad" → warning in logs, message handled

## Changelog
- 2026-04-07: Extracted to auth.py; should_handle_message fixed to log warning for invalid TOPIC_ID
