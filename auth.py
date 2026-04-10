"""
Auth guards and rate limiting.

All handler modules import from here instead of bot.py.
"""
import logging
import time

import config as _cfg

logger = logging.getLogger(__name__)

# In-memory rate limiting state (not persisted)
_rate_limits: dict = {}

# Public alias for testing
rate_limits = _rate_limits

_RATE_LIMIT_SECONDS = _cfg.RATE_LIMIT_SECONDS
_RATE_LIMIT_PER_MINUTE = _cfg.RATE_LIMIT_PER_MINUTE


def should_handle_message(message_thread_id: "int | None") -> bool:
    """Return True if this bot instance should handle the message (topic filtering)."""
    if not _cfg.TOPIC_ID:
        return True
    try:
        allowed_topic = int(_cfg.TOPIC_ID)
    except (ValueError, TypeError):
        logger.warning("Invalid TOPIC_ID %r, handling all messages", _cfg.TOPIC_ID)
        return True
    if message_thread_id is None:
        return False
    return message_thread_id == allowed_topic


def _is_authorized(update) -> bool:
    """Return True if the chat AND user are authorized to use this bot."""
    if _cfg.ALLOWED_CHAT_ID != 0 and update.effective_chat.id != _cfg.ALLOWED_CHAT_ID:
        return False
    if _cfg.ALLOWED_USER_IDS:
        if not update.effective_user or update.effective_user.id not in _cfg.ALLOWED_USER_IDS:
            return False
    return True


def _is_admin(update) -> bool:
    """Return True if the user can run admin commands (token setup etc.)."""
    if not _is_authorized(update):
        return False
    if not _cfg.ADMIN_USER_IDS:
        return True
    return update.effective_user.id in _cfg.ADMIN_USER_IDS


def has_claude_auth() -> bool:
    """Quick check if any Claude auth method is configured."""
    import os
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    creds_file = _cfg.CREDENTIALS_FILE
    if creds_file.exists():
        try:
            import json
            data = json.loads(creds_file.read_text())
            return bool(data.get("claude_token"))
        except (ValueError, IOError):
            pass
    return False


def check_rate_limit(user_id: int) -> "tuple[bool, str]":
    """
    Check if user is within rate limits.
    Returns (allowed, message) — if not allowed, message explains why.
    """
    now = time.time()
    key = str(user_id)
    if key not in _rate_limits:
        _rate_limits[key] = {"last_message": 0, "minute_count": 0, "minute_start": now}

    limits = _rate_limits[key]

    time_since_last = now - limits["last_message"]
    if time_since_last < _RATE_LIMIT_SECONDS:
        wait_time = _RATE_LIMIT_SECONDS - time_since_last
        return False, f"Please wait {wait_time:.1f}s before sending another message."

    if now - limits["minute_start"] > 60:
        limits["minute_start"] = now
        limits["minute_count"] = 0

    if limits["minute_count"] >= _RATE_LIMIT_PER_MINUTE:
        return False, f"Rate limit reached ({_RATE_LIMIT_PER_MINUTE}/min). Please wait."

    limits["last_message"] = now
    limits["minute_count"] += 1
    return True, ""
