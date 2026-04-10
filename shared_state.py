"""
In-memory cross-module shared state.

These dicts are mutated by both claude_service.call_claude() and the approval/cancel handlers.
Import this module — do not copy the dicts.
All dict mutations must be wrapped in `async with state_lock:`.
"""
import asyncio

# {approval_id: {"user_id": int, "event": asyncio.Event, "approved": bool|None, "tool_name": str, "input": dict}}
pending_approvals: dict = {}

# {user_id: asyncio.Event}  — set by /cancel to interrupt active call_claude
cancel_events: dict = {}

# Protects both dicts above under concurrent_updates=True
state_lock = asyncio.Lock()
