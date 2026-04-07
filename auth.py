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

_RATE_LIMIT_SECONDS = 2
_RATE_LIMIT_PER_MINUTE = 10


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
    """Return True if the chat is authorized to use this bot."""
    return _cfg.ALLOWED_CHAT_ID == 0 or update.effective_chat.id == _cfg.ALLOWED_CHAT_ID


def _is_admin(update) -> bool:
    """Return True if the user can run admin commands (token setup etc.)."""
    if not _is_authorized(update):
        return False
    if not _cfg.ADMIN_USER_IDS:
        return True
    return update.effective_user.id in _cfg.ADMIN_USER_IDS


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
