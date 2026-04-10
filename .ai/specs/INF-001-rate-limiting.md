---
id: INF-001
type: infra
status: active
severity: medium
validated: 2026-04-07
---

# Rate Limiting
Per-user in-memory rate limiting: 2s cooldown between messages, 10 messages/minute max.
State lives in `auth._rate_limits` — not persisted, resets on bot restart.

## Invariants
- First message from any user is always allowed
- Subsequent message within 2s → rejected with "Please wait Xs"
- More than 10 messages in a 60s window → rejected with "Rate limit reached (10/min)"
- Per-minute counter resets when 60s have elapsed since `minute_start`
- Rate limits are per user_id — different users are independent
- Rejection message is sent to the user; Claude is never called on rejected messages

## Test
- Send message → allowed
- Send another immediately → "please wait"
- Wait >2s → allowed again
- Send 10 messages with small gaps → 11th rejected with "limit"
- Wait 60s → 11th attempt allowed (counter reset)
- User A rate-limited → User B unaffected

## Changelog
- 2026-04-07: Extracted to auth.py; added test coverage for reset path
