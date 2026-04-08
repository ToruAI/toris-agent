"""
Centralized configuration for TORIS bot.
All environment variable reads happen here — nowhere else in the codebase.
"""
import os
from pathlib import Path


def resolve_provider(explicit_env: str) -> str:
    """Resolve voice provider: explicit > elevenlabs (if key) > openai (if key) > none."""
    explicit = os.getenv(explicit_env, "").lower()
    if explicit in ("openai", "elevenlabs"):
        return explicit
    if os.getenv("ELEVENLABS_API_KEY"):
        return "elevenlabs"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "none"


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _path_or_empty(key: str) -> str:
    val = os.getenv(key, "")
    if val and not os.path.isabs(val):
        val = str(Path(__file__).parent / val)
    return val


# ── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_ID: int = _int("TELEGRAM_DEFAULT_CHAT_ID", 0)
_admin_raw = os.getenv("TELEGRAM_ADMIN_USER_IDS", "")
ADMIN_USER_IDS: set[int] = (
    set(int(uid.strip()) for uid in _admin_raw.split(",") if uid.strip().isdigit())
    if _admin_raw.strip() else set()
)
TOPIC_ID: str | None = os.getenv("TELEGRAM_TOPIC_ID") or None

# ── Claude ────────────────────────────────────────────────────────────────────
CLAUDE_WORKING_DIR: str = os.getenv("CLAUDE_WORKING_DIR", os.path.expanduser("~"))
SANDBOX_DIR: str = os.getenv("CLAUDE_SANDBOX_DIR", os.path.join(os.path.expanduser("~"), "claude-voice-sandbox"))
CLAUDE_SETTINGS_FILE: str = _path_or_empty("CLAUDE_SETTINGS_FILE")
CLAUDE_TIMEOUT: int = _int("CLAUDE_TIMEOUT", 300)

# ── Persona ───────────────────────────────────────────────────────────────────
PERSONA_NAME: str = os.getenv("PERSONA_NAME", "Assistant")
SYSTEM_PROMPT_FILE: str = _path_or_empty("SYSTEM_PROMPT_FILE")

# ── Voice / ElevenLabs ────────────────────────────────────────────────────────
ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID: str = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")

# ── Voice / OpenAI ────────────────────────────────────────────────────────────
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_VOICE_ID: str = os.getenv("OPENAI_VOICE_ID", "coral")
OPENAI_TTS_MODEL: str = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_STT_MODEL: str = os.getenv("OPENAI_STT_MODEL", "whisper-1")
OPENAI_VOICE_INSTRUCTIONS: str = os.getenv("OPENAI_VOICE_INSTRUCTIONS", "")

# ── Voice (shared) ────────────────────────────────────────────────────────────
TTS_PROVIDER: str = resolve_provider("TTS_PROVIDER")   # "elevenlabs" | "openai" | "none"
STT_PROVIDER: str = resolve_provider("STT_PROVIDER")   # "elevenlabs" | "openai" | "none"
STT_LANGUAGE: str = os.getenv("STT_LANGUAGE", "")      # empty = auto-detect

# ── Limits ────────────────────────────────────────────────────────────────────
MAX_VOICE_CHARS: int = _int("MAX_VOICE_RESPONSE_CHARS", 500)
RATE_LIMIT_SECONDS: int = _int("RATE_LIMIT_SECONDS", 2)
RATE_LIMIT_PER_MINUTE: int = _int("RATE_LIMIT_PER_MINUTE", 10)

# ── State files ───────────────────────────────────────────────────────────────
STATE_FILE: Path = Path(__file__).parent / "sessions_state.json"
SETTINGS_FILE: Path = Path(__file__).parent / "user_settings.json"
CREDENTIALS_FILE: Path = Path(__file__).parent / "credentials.json"


def validate() -> list[str]:
    """Return validation warnings. Empty list = all required vars are set."""
    warnings = []
    if not TELEGRAM_BOT_TOKEN:
        warnings.append("TELEGRAM_BOT_TOKEN is required")
    if ALLOWED_CHAT_ID == 0:
        warnings.append("TELEGRAM_DEFAULT_CHAT_ID=0 — bot responds to ALL chats (set a specific chat ID)")
    if TTS_PROVIDER == "none":
        warnings.append("No TTS provider key configured — voice output disabled")
    if STT_PROVIDER == "none":
        warnings.append("No STT provider key configured — voice input disabled")
    return warnings
