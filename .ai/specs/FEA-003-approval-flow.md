---
id: FEA-003
type: feature
status: active
severity: high
validated: 2026-04-07
---

# Tool Approval Flow
When mode=approve, each Claude tool call requires explicit user confirmation before execution.
NOT a security boundary — it is a UX control for the owner to stay in the loop.

## Invariants
- Approval request is shown only in mode=approve; mode=go_all executes tools immediately
- Each approval has a unique ID stored in `shared_state.pending_approvals`
- Only the user who sent the original message can approve or reject
- Clicking Approve/Reject from a different user → toast "Only the requester can approve this", no state change
- Approved → `event.set()` unblocks Claude SDK; message updated to "✓ Approved: <tool>"
- Rejected → `event.set()` with approved=False; message updated to "✗ Rejected: <tool>"
- Expired/missing approval ID → "Approval expired" message (no crash)
- Exactly one `query.answer()` per callback branch (Telegram rejects double-answer)

## Test
- mode=approve → send prompt that triggers Bash → Approve/Reject buttons appear
- Tap Approve as requester → "✓ Approved: Bash", Claude continues
- Tap Reject as requester → "✗ Rejected: Bash", Claude receives rejection
- Tap Approve as different user → toast, buttons unchanged
- Tap button on old expired approval → "Approval expired"

## Changelog
- 2026-04-07: Fixed double query.answer() bug; extracted to handlers/admin.py
