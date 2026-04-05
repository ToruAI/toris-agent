#!/usr/bin/env python3
"""
Claude Voice Assistant - Telegram Bot
Voice messages -> ElevenLabs Scribe -> Claude Code SDK -> ElevenLabs TTS -> Voice response
"""

import os
import subprocess
import shutil
import json
import asyncio
import logging
from datetime import datetime
from io import BytesIO
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from elevenlabs.client import ElevenLabs
from openai import OpenAI as OpenAIClient

# Claude Agent SDK (official SDK for Claude Code)
from claude_agent_sdk import (
    query as claude_query,
    ClaudeAgentOptions,
    ClaudeSDKClient,
)
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    PermissionResultAllow,
    PermissionResultDeny,
)

load_dotenv()


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


def check_claude_auth() -> tuple[bool, str]:
    """Check if Claude authentication is configured.

    Returns:
        (is_authenticated, auth_method) - auth_method is 'api_key', 'oauth', 'saved_token', or 'none'
    """
    # Method 1: API Key
    if os.getenv("ANTHROPIC_API_KEY"):
        return True, "api_key"

    # Method 2: Saved OAuth token (from /setup)
    if os.getenv("CLAUDE_CODE_OAUTH_TOKEN"):
        return True, "saved_token"

    # Method 3: OAuth credentials file
    credentials_path = Path.home() / ".claude" / ".credentials.json"
    if credentials_path.exists():
        try:
            import time
            creds = json.loads(credentials_path.read_text())
            oauth = creds.get("claudeAiOauth", {})
            if oauth.get("accessToken"):
                # Check if not expired (with 5 min buffer)
                expires_at = oauth.get("expiresAt", 0)
                if expires_at > (time.time() * 1000 + 300000):
                    return True, "oauth"
                # Expired but has refresh token - Claude SDK will handle refresh
                if oauth.get("refreshToken"):
                    return True, "oauth"
        except (json.JSONDecodeError, KeyError):
            pass

    return False, "none"


def validate_environment():
    """Validate required environment variables on startup."""
    required = {
        "TELEGRAM_BOT_TOKEN": "Telegram bot token from @BotFather",
    }

    missing = []
    for var, description in required.items():
        if not os.getenv(var):
            missing.append(f"  - {var}: {description}")

    if missing:
        print("ERROR: Missing required environment variables:")
        print("\n".join(missing))
        print("\nCopy .env.example to .env and fill in the values.")
        exit(1)

    # Require at least one voice provider key
    if not os.getenv("ELEVENLABS_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        print("WARNING: No voice provider key set (ELEVENLABS_API_KEY or OPENAI_API_KEY)")
        print("         Voice features will be disabled until a key is configured via /setup")

    # Validate TELEGRAM_DEFAULT_CHAT_ID is a valid integer
    chat_id = os.getenv("TELEGRAM_DEFAULT_CHAT_ID", "0")
    try:
        int(chat_id)
    except ValueError:
        print(f"ERROR: TELEGRAM_DEFAULT_CHAT_ID must be a number, got: {chat_id}")
        exit(1)

    if chat_id == "0":
        print("WARNING: TELEGRAM_DEFAULT_CHAT_ID is 0 - bot will accept all messages")
        print("         Set this to your chat ID to restrict access")

    # Check Claude authentication (don't exit - can be configured via Telegram)
    is_auth, auth_method = check_claude_auth()
    if not is_auth:
        print("WARNING: Claude authentication not configured - bot will start but Claude won't work")
        print("         Use /setup in Telegram to configure, or set ANTHROPIC_API_KEY in env")
    else:
        print(f"Claude auth: {auth_method}")

    return is_auth, auth_method


# Setup logging with configurable level
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO)
)
logger = logging.getLogger(__name__)

# Config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ALLOWED_CHAT_ID = int(os.getenv("TELEGRAM_DEFAULT_CHAT_ID", "0"))
# Admin user IDs (comma-separated) - required for /setup, /claude_token, etc.
# If empty, falls back to chat-ID check only (backward compat)
_admin_ids_raw = os.getenv("TELEGRAM_ADMIN_USER_IDS", "")
ADMIN_USER_IDS = set(int(uid.strip()) for uid in _admin_ids_raw.split(",") if uid.strip()) if _admin_ids_raw.strip() else set()
TOPIC_ID = os.getenv("TELEGRAM_TOPIC_ID")  # Empty = all topics, set = only this topic
CLAUDE_WORKING_DIR = os.getenv("CLAUDE_WORKING_DIR", os.path.expanduser("~"))
SANDBOX_DIR = os.getenv("CLAUDE_SANDBOX_DIR", os.path.join(os.path.expanduser("~"), "claude-voice-sandbox"))
MAX_VOICE_CHARS = int(os.getenv("MAX_VOICE_RESPONSE_CHARS", "500"))

# Persona config
PERSONA_NAME = os.getenv("PERSONA_NAME", "Assistant")
SYSTEM_PROMPT_FILE = os.getenv("SYSTEM_PROMPT_FILE", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")  # Default: George
CLAUDE_SETTINGS_FILE = os.getenv("CLAUDE_SETTINGS_FILE", "")  # Optional settings.json for permissions
if CLAUDE_SETTINGS_FILE and not os.path.isabs(CLAUDE_SETTINGS_FILE):
    CLAUDE_SETTINGS_FILE = str(Path(__file__).parent / CLAUDE_SETTINGS_FILE)

# Voice provider selection (resolved at startup)
TTS_PROVIDER = resolve_provider("TTS_PROVIDER")  # "elevenlabs", "openai", or "none"
STT_PROVIDER = resolve_provider("STT_PROVIDER")  # "elevenlabs", "openai", or "none"

# OpenAI voice config
OPENAI_VOICE_ID = os.getenv("OPENAI_VOICE_ID", "coral")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_STT_MODEL = os.getenv("OPENAI_STT_MODEL", "whisper-1")
OPENAI_VOICE_INSTRUCTIONS = os.getenv("OPENAI_VOICE_INSTRUCTIONS", "")

# STT language (applies to both providers; empty = auto-detect)
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "")

# OpenAI client (None if no key configured)
openai_client = OpenAIClient(api_key=os.getenv("OPENAI_API_KEY")) if os.getenv("OPENAI_API_KEY") else None

def load_system_prompt() -> str:
    """Load system prompt from file or use default."""
    if SYSTEM_PROMPT_FILE:
        prompt_path = Path(SYSTEM_PROMPT_FILE)
        # If relative, look relative to bot.py
        if not prompt_path.is_absolute():
            prompt_path = Path(__file__).parent / prompt_path
        if prompt_path.exists():
            content = prompt_path.read_text()
            # Replace placeholders
            content = content.replace("{sandbox_dir}", SANDBOX_DIR)
            content = content.replace("{read_dir}", CLAUDE_WORKING_DIR)
            logger.debug(f"Loaded system prompt from {prompt_path} ({len(content)} chars)")
            return content
        else:
            logger.debug(f"WARNING: System prompt file not found: {prompt_path}")

    # Fallback default prompt
    return f"""You are a voice assistant. You're talking to the user.

## CRITICAL - Voice output rules:
- NO markdown formatting (no **, no ##, no ```)
- NO bullet points or numbered lists in speech
- Speak in natural flowing sentences

## Your capabilities:
- You can READ files from anywhere in {CLAUDE_WORKING_DIR}
- You can WRITE and EXECUTE only in {SANDBOX_DIR}
- You have WebSearch for current information

Remember: You're being heard, not read. Speak naturally."""


def should_handle_message(message_thread_id: int | None) -> bool:
    """Check if this bot instance should handle a message based on topic filtering."""
    if not TOPIC_ID:
        # No topic filter set = handle all messages
        return True

    # Convert to int for comparison
    try:
        allowed_topic = int(TOPIC_ID)
    except (ValueError, TypeError):
        logger.debug(f"WARNING: Invalid TOPIC_ID '{TOPIC_ID}', handling all messages")
        return True

    # Check if message is in the allowed topic
    if message_thread_id is None:
        # Message not in any topic (general chat) - don't handle if we have a specific topic
        logger.debug(f"Message not in a topic, but we're filtering for topic {allowed_topic}")
        return False

    return message_thread_id == allowed_topic


def _is_authorized(update) -> bool:
    """Check if the chat is authorized to use this bot."""
    return ALLOWED_CHAT_ID == 0 or update.effective_chat.id == ALLOWED_CHAT_ID


def _is_admin(update) -> bool:
    """Check if user is allowed to run admin commands (token setup, etc.)."""
    if not _is_authorized(update):
        return False
    if not ADMIN_USER_IDS:
        return True  # Backward compat: no admin list = anyone in authorized chat
    return update.effective_user.id in ADMIN_USER_IDS


# Base system prompt (loaded once at startup)
BASE_SYSTEM_PROMPT = load_system_prompt()


def build_dynamic_prompt(user_settings: dict = None) -> str:
    """Build dynamic system prompt with current date/time and user settings."""
    prompt = BASE_SYSTEM_PROMPT

    # Inject current date and time
    now = datetime.now()
    timestamp_info = f"\n\nCurrent date and time: {now.strftime('%Y-%m-%d %H:%M:%S %A')}"
    prompt = prompt + timestamp_info

    # Optionally inject user settings summary
    if user_settings:
        if not user_settings.get("audio_enabled", True):
            prompt = prompt + "\n\nUser settings:\n- Audio responses disabled (text only)"

    return prompt

# Voice settings for expressive delivery
VOICE_SETTINGS = {
    "stability": 0.3,           # More emotional range
    "similarity_boost": 0.75,   # Good voice match
    "style": 0.4,               # Some style exaggeration
    "speed": 1.1,               # Slightly faster (range: 0.7-1.2)
}

# ElevenLabs client
elevenlabs = ElevenLabs(api_key=ELEVENLABS_API_KEY)

# Session state per user
user_sessions = {}  # {user_id: {"current_session": "session_id", "sessions": []}}

# User settings per user
user_settings = {}  # {user_id: {"audio_enabled": bool, "voice_speed": float, "mode": str, "watch_enabled": bool}}

# Rate limiting
RATE_LIMIT_SECONDS = 2  # Minimum seconds between messages per user
RATE_LIMIT_PER_MINUTE = 10  # Max messages per minute per user
user_rate_limits = {}  # {user_id: {"last_message": timestamp, "minute_count": int, "minute_start": timestamp}}
rate_limits = user_rate_limits  # Public alias for testing


def check_rate_limit(user_id: int) -> tuple[bool, str]:
    """
    Check if user is within rate limits.
    Returns (allowed, message) - if not allowed, message explains why.
    """
    import time

    now = time.time()
    user_id_str = str(user_id)

    if user_id_str not in user_rate_limits:
        user_rate_limits[user_id_str] = {
            "last_message": 0,
            "minute_count": 0,
            "minute_start": now,
        }

    limits = user_rate_limits[user_id_str]

    # Check per-message cooldown
    time_since_last = now - limits["last_message"]
    if time_since_last < RATE_LIMIT_SECONDS:
        wait_time = RATE_LIMIT_SECONDS - time_since_last
        return False, f"Please wait {wait_time:.1f}s before sending another message."

    # Check per-minute limit
    if now - limits["minute_start"] > 60:
        # Reset minute counter
        limits["minute_start"] = now
        limits["minute_count"] = 0

    if limits["minute_count"] >= RATE_LIMIT_PER_MINUTE:
        return False, f"Rate limit reached ({RATE_LIMIT_PER_MINUTE}/min). Please wait."

    # Update limits
    limits["last_message"] = now
    limits["minute_count"] += 1

    return True, ""


# Pending tool approvals: {approval_id: {"event": asyncio.Event, "approved": bool, "tool_name": str, "input": dict}}
pending_approvals = {}

# Cancellation events per user — set by /cancel to interrupt active call_claude
cancel_events: dict[int, asyncio.Event] = {}

# State files for persistence
STATE_FILE = Path(__file__).parent / "sessions_state.json"
SETTINGS_FILE = Path(__file__).parent / "user_settings.json"


def load_state():
    """Load session state from file."""
    global user_sessions
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                user_sessions = json.load(f)
            logger.debug(f"Loaded state: {len(user_sessions)} users")
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Could not load state file, starting fresh: {e}")
            user_sessions = {}


def save_state():
    """Save session state to file."""
    with open(STATE_FILE, "w") as f:
        json.dump(user_sessions, f, indent=2)


def load_settings():
    """Load user settings from file."""
    global user_settings
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE) as f:
                user_settings = json.load(f)
            logger.debug(f"Loaded settings: {len(user_settings)} users")
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Could not load settings file, starting fresh: {e}")
            user_settings = {}


def save_settings():
    """Save user settings to file."""
    with open(SETTINGS_FILE, "w") as f:
        json.dump(user_settings, f, indent=2)


# Credentials file for user-provided API keys
CREDENTIALS_FILE = Path(__file__).parent / "credentials.json"


def load_credentials() -> dict:
    """Load saved credentials from file."""
    if CREDENTIALS_FILE.exists():
        try:
            with open(CREDENTIALS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_credentials(creds: dict):
    """Save credentials to file with secure permissions."""
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(creds, f, indent=2)
    # Restrict file permissions (owner read/write only)
    CREDENTIALS_FILE.chmod(0o600)


def apply_saved_credentials():
    """Apply saved credentials on startup."""
    global elevenlabs, ELEVENLABS_API_KEY, openai_client, TTS_PROVIDER, STT_PROVIDER
    creds = load_credentials()

    if creds.get("claude_token"):
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = creds["claude_token"]
        logger.debug("Applied saved Claude token")

    if creds.get("elevenlabs_key"):
        ELEVENLABS_API_KEY = creds["elevenlabs_key"]
        os.environ["ELEVENLABS_API_KEY"] = creds["elevenlabs_key"]
        elevenlabs = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        logger.debug("Applied saved ElevenLabs key")

    if creds.get("openai_key"):
        os.environ["OPENAI_API_KEY"] = creds["openai_key"]
        openai_client = OpenAIClient(api_key=creds["openai_key"])
        logger.debug("Applied saved OpenAI key")

    # Re-resolve providers after credentials are loaded
    TTS_PROVIDER = resolve_provider("TTS_PROVIDER")
    STT_PROVIDER = resolve_provider("STT_PROVIDER")


def get_mcp_status(settings_file: str) -> list[str]:
    """Return list of status lines for MCP servers in settings file.

    Pure function — no I/O side effects beyond reading the settings file.
    """
    if not settings_file:
        return ["MCP: CLAUDE_SETTINGS_FILE not configured"]

    settings_path = Path(settings_file)
    if not settings_path.is_absolute():
        settings_path = Path(__file__).parent / settings_file

    if not settings_path.exists():
        return [f"MCP config: settings file not found ({settings_file})"]

    try:
        settings_data = json.loads(settings_path.read_text())
    except (json.JSONDecodeError, IOError) as e:
        return [f"MCP config: ERROR reading settings - {e}"]

    mcp_servers = settings_data.get("mcpServers", {})
    if not mcp_servers:
        return ["MCP Servers: none configured"]

    lines = ["MCP Servers:"]
    for name, config in mcp_servers.items():
        cmd = config.get("command", "")
        if cmd and shutil.which(cmd):
            lines.append(f"  {name}: OK ({cmd})")
        elif cmd:
            lines.append(f"  {name}: MISSING ({cmd} not found in PATH)")
        else:
            lines.append(f"  {name}: misconfigured (no command)")
    return lines


def load_mcp_servers() -> dict:
    """Read mcpServers from CLAUDE_SETTINGS_FILE for use in ClaudeAgentOptions.mcp_servers."""
    if not CLAUDE_SETTINGS_FILE:
        return {}
    try:
        data = json.loads(Path(CLAUDE_SETTINGS_FILE).read_text())
        return data.get("mcpServers", {})
    except (json.JSONDecodeError, IOError, OSError):
        return {}


def get_user_state(user_id: int) -> dict:
    """Get or create user state."""
    user_id_str = str(user_id)
    if user_id_str not in user_sessions:
        user_sessions[user_id_str] = {"current_session": None, "sessions": []}
    return user_sessions[user_id_str]


def get_user_settings(user_id: int) -> dict:
    """Get or create user settings with defaults."""
    user_id_str = str(user_id)
    if user_id_str not in user_settings:
        user_settings[user_id_str] = {
            "audio_enabled": True,
            "voice_speed": VOICE_SETTINGS["speed"],
            "mode": "go_all",  # "go_all" or "approve"
            "watch_mode": "off",  # "off" | "live" | "debug"
        }
    else:
        s = user_settings[user_id_str]
        if "mode" not in s:
            s["mode"] = "go_all"
        # Migrate watch_enabled / show_activity → watch_mode
        if "watch_mode" not in s:
            if s.pop("watch_enabled", False):
                s["watch_mode"] = "live"
            elif s.pop("show_activity", False):
                s["watch_mode"] = "debug"
            else:
                s["watch_mode"] = "off"
        s.pop("watch_enabled", None)
        s.pop("show_activity", None)
    return user_settings[user_id_str]


async def _transcribe_elevenlabs(voice_bytes: bytes) -> str:
    """Transcribe voice using ElevenLabs Scribe."""
    try:
        transcription = await asyncio.to_thread(
            elevenlabs.speech_to_text.convert,
            file=BytesIO(voice_bytes),
            model_id="scribe_v1",
            language_code=STT_LANGUAGE or None,
        )
        return transcription.text
    except Exception as e:
        logger.error(f"ElevenLabs STT error: {e}")
        raise


async def _transcribe_openai(voice_bytes: bytes) -> str:
    """Transcribe voice using OpenAI Whisper."""
    try:
        lang = STT_LANGUAGE or None
        kwargs = {
            "model": OPENAI_STT_MODEL,
            "file": ("voice.ogg", BytesIO(voice_bytes), "audio/ogg"),
        }
        if lang:
            kwargs["language"] = lang
        result = await asyncio.to_thread(openai_client.audio.transcriptions.create, **kwargs)
        return result.text
    except Exception as e:
        logger.error(f"OpenAI STT error: {e}")
        raise


async def transcribe_voice(voice_bytes: bytes) -> str:
    """Transcribe voice — routes to active STT provider."""
    try:
        if STT_PROVIDER == "openai":
            return await _transcribe_openai(voice_bytes)
        if STT_PROVIDER == "elevenlabs":
            return await _transcribe_elevenlabs(voice_bytes)
        return "[Transcription error: no STT provider configured]"
    except Exception as e:
        return f"[Transcription error: {e}]"


async def _tts_elevenlabs(text: str, speed: float = None) -> BytesIO:
    """Convert text to speech using ElevenLabs Flash v2.5."""
    def _sync_tts():
        kwargs = dict(
            text=text,
            voice_id=ELEVENLABS_VOICE_ID,
            model_id="eleven_flash_v2_5",
            output_format="mp3_44100_128",
        )
        if speed is not None:
            kwargs["voice_settings"] = {"speed": speed}
        audio = elevenlabs.text_to_speech.convert(**kwargs)
        buf = BytesIO()
        for chunk in audio:
            if isinstance(chunk, bytes):
                buf.write(chunk)
        buf.seek(0)
        return buf
    return await asyncio.to_thread(_sync_tts)


async def _tts_openai(text: str, speed: float = None) -> BytesIO:
    """Convert text to speech using OpenAI TTS."""
    def _sync_tts():
        kwargs = dict(model=OPENAI_TTS_MODEL, voice=OPENAI_VOICE_ID, input=text)
        if OPENAI_VOICE_INSTRUCTIONS:
            kwargs["instructions"] = OPENAI_VOICE_INSTRUCTIONS
        if speed is not None:
            kwargs["speed"] = speed
        response = openai_client.audio.speech.create(**kwargs)
        buf = BytesIO()
        for chunk in response.iter_bytes(chunk_size=4096):
            buf.write(chunk)
        buf.seek(0)
        return buf
    return await asyncio.to_thread(_sync_tts)


async def text_to_speech(text: str, speed: float = None) -> BytesIO:
    """Convert text to speech — routes to active TTS provider."""
    try:
        if TTS_PROVIDER == "openai":
            return await _tts_openai(text, speed)
        if TTS_PROVIDER == "elevenlabs":
            return await _tts_elevenlabs(text, speed)
        logger.debug("TTS skipped: no provider configured")
        return None
    except Exception as e:
        logger.error(f"TTS error: {e}")
        return None


async def send_long_message(update: Update, first_msg, text: str, chunk_size: int = 4000):
    """Split long text into multiple Telegram messages.

    If first_msg is None, all chunks are sent as new reply messages.
    """
    if len(text) <= chunk_size:
        if first_msg is None:
            await update.message.reply_text(text)
        else:
            await first_msg.edit_text(text)
        return

    # Split into chunks
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= chunk_size:
            chunks.append(remaining)
            break
        # Find a good break point (newline or space)
        break_point = remaining.rfind('\n', 0, chunk_size)
        if break_point == -1:
            break_point = remaining.rfind(' ', 0, chunk_size)
        if break_point == -1:
            break_point = chunk_size
        chunks.append(remaining[:break_point])
        remaining = remaining[break_point:].lstrip()

    # Send first chunk as edit (or new reply if first_msg is None), rest as new messages
    if first_msg is None:
        await update.message.reply_text(chunks[0] + f"\n\n[1/{len(chunks)}]")
    else:
        await first_msg.edit_text(chunks[0] + f"\n\n[1/{len(chunks)}]")
    for i, chunk in enumerate(chunks[1:], 2):
        await update.message.reply_text(chunk + f"\n\n[{i}/{len(chunks)}]")

    logger.debug(f"Sent {len(chunks)} message chunks")


async def finalize_response(update: Update, processing_msg, response: str):
    """Replace processing_msg with the final response (or send as new message if no processing_msg)."""
    await send_long_message(update, processing_msg, response)


def load_megg_context() -> str:
    """Load megg context like the hook does."""
    try:
        result = subprocess.run(
            ["megg", "context"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=CLAUDE_WORKING_DIR
        )
        if result.returncode == 0:
            logger.debug(f"Loaded megg context: {len(result.stdout)} chars")
            return result.stdout
        else:
            logger.debug(f"Megg context failed: {result.stderr[:50]}")
            return ""
    except Exception as e:
        logger.debug(f"Megg error: {e}")
        return ""


def format_tool_call(tool_name: str, tool_input: dict) -> str:
    """Format a tool call for display in Telegram."""
    # Truncate long inputs
    input_str = json.dumps(tool_input, indent=2)
    if len(input_str) > 500:
        input_str = input_str[:500] + "..."
    return f"Tool: {tool_name}\n```\n{input_str}\n```"


async def call_claude(
    prompt: str,
    session_id: str = None,
    continue_last: bool = False,
    include_megg: bool = True,
    user_settings: dict = None,
    update: Update = None,
    context: ContextTypes.DEFAULT_TYPE = None,
    processing_msg=None,
) -> tuple[str, str, dict]:
    """
    Call Claude Code SDK and return (response, session_id, metadata).
    metadata includes: cost, num_turns, duration

    If update/context provided and watch_enabled, streams tool calls to Telegram.
    If mode == "approve", waits for user approval before each tool.
    """
    settings = user_settings or {}
    watch_mode = settings.get("watch_mode", "off")  # "off" | "live" | "debug"
    mode = settings.get("mode", "go_all")

    # Ensure sandbox exists
    Path(SANDBOX_DIR).mkdir(parents=True, exist_ok=True)

    # Load megg context for new sessions
    full_prompt = prompt
    if include_megg and not continue_last and not session_id:
        megg_ctx = load_megg_context()
        if megg_ctx:
            full_prompt = f"<context>\n{megg_ctx}\n</context>\n\n{prompt}"
            logger.debug("Prepended megg context to prompt")

    # Build dynamic system prompt
    dynamic_persona = build_dynamic_prompt(user_settings)

    logger.debug(f"Calling Claude SDK: prompt={len(prompt)} chars, continue={continue_last}, session={session_id[:8] if session_id else 'new'}...")
    logger.debug(f"Mode: {mode}, Watch: {watch_enabled}")
    logger.debug(f"Working dir: {SANDBOX_DIR} (sandbox)")

    # Track tool approvals for this call
    approval_event = None
    current_approval_id = None

    async def can_use_tool(tool_name: str, tool_input: dict, ctx) -> PermissionResultAllow | PermissionResultDeny:
        """Callback for tool approval in approve mode."""
        nonlocal approval_event, current_approval_id

        logger.debug(f">>> can_use_tool CALLED: {tool_name}")

        if mode != "approve":
            logger.debug(f">>> Mode is {mode}, auto-allowing")
            return PermissionResultAllow()

        if update is None:
            logger.debug(f"No update context for approval, allowing {tool_name}")
            return PermissionResultAllow()

        # Generate unique approval ID
        import uuid
        current_approval_id = str(uuid.uuid4())[:8]
        approval_event = asyncio.Event()

        # Store pending approval with requesting user_id
        pending_approvals[current_approval_id] = {
            "user_id": update.effective_user.id,
            "event": approval_event,
            "approved": None,
            "tool_name": tool_name,
            "input": tool_input,
        }

        # Send approval request to Telegram
        keyboard = [
            [
                InlineKeyboardButton("Approve", callback_data=f"approve_{current_approval_id}"),
                InlineKeyboardButton("Reject", callback_data=f"reject_{current_approval_id}"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message_text = f"Tool Request:\n{format_tool_call(tool_name, tool_input)}"
        await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode="Markdown")

        logger.debug(f">>> Waiting for approval: {current_approval_id} ({tool_name}) - pending_approvals keys: {list(pending_approvals.keys())}")

        # Wait for user response (with timeout)
        try:
            logger.debug(f">>> Starting event.wait() for {current_approval_id}")
            await asyncio.wait_for(approval_event.wait(), timeout=300)  # 5 min timeout
            logger.debug(f">>> Event.wait() completed for {current_approval_id}")
        except asyncio.TimeoutError:
            logger.debug(f">>> Approval timeout for {current_approval_id}")
            del pending_approvals[current_approval_id]
            return PermissionResultDeny(message="Approval timed out")

        # Check result
        logger.debug(f">>> Checking result for {current_approval_id}")
        approval_data = pending_approvals.pop(current_approval_id, {})
        if approval_data.get("approved"):
            logger.debug(f">>> Tool approved: {tool_name}")
            return PermissionResultAllow()
        else:
            logger.debug(f">>> Tool rejected: {tool_name}")
            return PermissionResultDeny(message="User rejected tool")

    # Build SDK options
    # In approve mode: don't pre-allow tools - let can_use_tool callback handle each one
    # In go_all mode: pre-allow all tools for no prompts
    if mode == "approve":
        logger.debug(f">>> APPROVE MODE: Setting up can_use_tool callback")
        options = ClaudeAgentOptions(
            system_prompt=dynamic_persona,
            cwd=SANDBOX_DIR,
            can_use_tool=can_use_tool,
            permission_mode="default",
            add_dirs=[CLAUDE_WORKING_DIR],
        )
        if CLAUDE_SETTINGS_FILE:
            options.settings = CLAUDE_SETTINGS_FILE
        logger.debug(f">>> Options: can_use_tool={options.can_use_tool is not None}, permission_mode={options.permission_mode}")
    else:
        logger.debug(f">>> GO_ALL MODE: Pre-allowing all tools")
        options = ClaudeAgentOptions(
            system_prompt=dynamic_persona,
            allowed_tools=["Read", "Grep", "Glob", "WebSearch", "WebFetch", "Task", "Bash", "Edit", "Write", "Skill"],
            cwd=SANDBOX_DIR,
            add_dirs=[CLAUDE_WORKING_DIR],
        )
        if CLAUDE_SETTINGS_FILE:
            options.settings = CLAUDE_SETTINGS_FILE

    # Handle session continuation
    if continue_last:
        options.continue_conversation = True
    elif session_id:
        options.resume = session_id

    result_text = ""
    new_session_id = session_id
    metadata = {}
    tool_count = 0
    tool_log: list[str] = []  # Running list of tool names used

    # Create debug message (debug mode only) — separate persistent message for tool log
    debug_msg = None
    if watch_mode == "debug" and update:
        try:
            debug_msg = await update.message.reply_text("🔧 Running...")
        except Exception:
            pass

    # Set up cancellation tracking for this user
    user_id_for_cancel = update.effective_user.id if update else None
    if user_id_for_cancel is not None:
        if user_id_for_cancel not in cancel_events:
            cancel_events[user_id_for_cancel] = asyncio.Event()
        cancel_events[user_id_for_cancel].clear()  # Reset at start of each call

    try:
        logger.debug(f">>> Starting ClaudeSDKClient with prompt: {len(full_prompt)} chars")
        async with ClaudeSDKClient(options=options) as client:
            await client.query(full_prompt)
            async for message in client.receive_response():
                # Check for user cancellation
                if user_id_for_cancel is not None and cancel_events.get(user_id_for_cancel, asyncio.Event()).is_set():
                    logger.debug(f"Call cancelled by user {user_id_for_cancel}")
                    result_text = (result_text + "\n\n[Cancelled]").strip()
                    break

                # Handle different message types
                logger.debug(f">>> SDK message type: {type(message).__name__}")
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            result_text += block.text
                        elif isinstance(block, ToolUseBlock):
                            tool_count += 1
                            # Build label for this tool
                            tool_input = block.input or {}
                            if block.name == "Bash" and "command" in tool_input:
                                label = f"Bash: {tool_input['command']}"
                            elif block.name in ("Read", "Edit", "Write") and "file_path" in tool_input:
                                label = f"{block.name}: {tool_input['file_path']}"
                            elif block.name == "Grep" and "pattern" in tool_input:
                                label = f"Grep: /{tool_input['pattern']}/"
                            elif block.name == "Glob" and "pattern" in tool_input:
                                label = f"Glob: {tool_input['pattern']}"
                            elif block.name.startswith("mcp__"):
                                label = block.name.replace("mcp__", "")
                            else:
                                label = block.name
                            tool_log.append(f"⚙ {label}")

                            if watch_mode == "live" and processing_msg is not None:
                                try:
                                    await processing_msg.edit_text("Asking Claude...\n" + "\n".join(tool_log))
                                except Exception:
                                    pass
                            elif watch_mode == "debug" and debug_msg is not None:
                                try:
                                    await debug_msg.edit_text("🔧 Tools:\n" + "\n".join(tool_log))
                                except Exception:
                                    pass

                elif isinstance(message, ResultMessage):
                    # Extract final result and metadata
                    if hasattr(message, "result") and message.result:
                        result_text = message.result
                    if hasattr(message, "session_id") and message.session_id:
                        new_session_id = message.session_id
                    if hasattr(message, "total_cost_usd"):
                        metadata["cost"] = message.total_cost_usd
                    if hasattr(message, "num_turns"):
                        metadata["num_turns"] = message.num_turns
                    if hasattr(message, "duration_ms"):
                        metadata["duration_ms"] = message.duration_ms

        logger.debug(f"Claude SDK responded: {len(result_text)} chars, {tool_count} tools used")
        metadata["tool_log"] = tool_log
        return result_text, new_session_id, metadata

    except Exception as e:
        logger.error(f"Claude SDK error: {e}")
        return f"Error calling Claude: {e}", session_id, {}


# ============ Command Handlers ============

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_authorized(update):
        return

    await update.message.reply_text(
        "Claude Voice Assistant\n\n"
        "Send me a voice message and I'll process it with Claude.\n\n"
        "Commands:\n"
        "/setup - Configure API credentials\n"
        "/new [name] - Start new session\n"
        "/continue - Resume last session\n"
        "/sessions - List all sessions\n"
        "/switch <name> - Switch to session\n"
        "/status - Current session info\n"
        "/settings - Configure audio and voice speed"
    )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /new command - start new session."""
    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_authorized(update):
        return

    user_id = update.effective_user.id
    state = get_user_state(user_id)

    session_name = " ".join(context.args) if context.args else None
    state["current_session"] = None  # Will be set on first message

    if session_name:
        await update.message.reply_text(f"New session started: {session_name}")
    else:
        await update.message.reply_text("New session started. Send a voice message to begin.")

    save_state()


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cancel command — interrupt active Claude request."""
    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_authorized(update):
        return

    user_id = update.effective_user.id
    event = cancel_events.get(user_id)
    if event is not None and not event.is_set():
        event.set()
        await update.message.reply_text("Cancelling...")
    else:
        await update.message.reply_text("No active request to cancel.")


async def cmd_compact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /compact command — summarize current session and start fresh."""
    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_authorized(update):
        return

    user_id = update.effective_user.id
    state = get_user_state(user_id)
    settings = get_user_settings(user_id)

    if not state.get("current_session"):
        await update.message.reply_text("No active session to compact. Start a conversation first.")
        return

    processing_msg = await update.message.reply_text("Compacting session...")

    try:
        summary, _, _ = await call_claude(
            "Summarize this entire conversation concisely but completely. Include: key topics, decisions, important files/code mentioned, and any ongoing work. Preserve all context needed to continue seamlessly.",
            session_id=state["current_session"],
            continue_last=True,
            include_megg=False,
            user_settings=settings,
            update=update,
            context=context,
        )

        # Save summary as pending context for next message, start fresh session
        state["compact_summary"] = summary
        state["current_session"] = None
        save_state()

        preview = summary[:400] + "..." if len(summary) > 400 else summary
        await processing_msg.edit_text(
            f"Session compacted. Summary:\n\n{preview}\n\nSend your next message to continue with this context."
        )

    except Exception as e:
        logger.error(f"Error in cmd_compact: {e}")
        await processing_msg.edit_text(f"Error compacting session: {e}")


async def cmd_continue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /continue command - resume last session."""
    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_authorized(update):
        return

    user_id = update.effective_user.id
    state = get_user_state(user_id)

    if state["current_session"]:
        await update.message.reply_text(f"Continuing session: {state['current_session'][:8]}...")
    else:
        await update.message.reply_text("No previous session. Send a voice message to start.")


async def cmd_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sessions command - list all sessions."""
    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_authorized(update):
        return

    user_id = update.effective_user.id
    state = get_user_state(user_id)

    if not state["sessions"]:
        await update.message.reply_text("No sessions yet.")
        return

    msg = "Sessions:\n"
    for i, sess in enumerate(state["sessions"][-10:], 1):  # Last 10
        current = " (current)" if sess == state["current_session"] else ""
        msg += f"{i}. {sess[:8]}...{current}\n"

    await update.message.reply_text(msg)


async def cmd_switch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /switch command - switch to specific session."""
    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_authorized(update):
        return

    if not context.args:
        await update.message.reply_text("Usage: /switch <session_id>")
        return

    user_id = update.effective_user.id
    state = get_user_state(user_id)
    session_id = context.args[0]

    # Find matching session
    matches = [s for s in state["sessions"] if s.startswith(session_id)]

    if len(matches) == 1:
        state["current_session"] = matches[0]
        save_state()
        await update.message.reply_text(f"Switched to session: {matches[0][:8]}...")
    elif len(matches) > 1:
        await update.message.reply_text(f"Multiple matches. Be more specific.")
    else:
        await update.message.reply_text(f"Session not found: {session_id}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command - show current session info."""
    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_authorized(update):
        return

    logger.debug(f"STATUS command from user {update.effective_user.id}")
    user_id = update.effective_user.id
    state = get_user_state(user_id)

    if state["current_session"]:
        await update.message.reply_text(
            f"Current session: {state['current_session'][:8]}...\n"
            f"Total sessions: {len(state['sessions'])}"
        )
    else:
        await update.message.reply_text("No active session. Send a voice message or /new to start.")


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /health command - check all systems."""
    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_authorized(update):
        return

    logger.debug(f"HEALTH command from user {update.effective_user.id}, chat {update.effective_chat.id}, topic {update.message.message_thread_id}")

    status = []
    status.append("=== Health Check ===\n")

    # TTS provider check
    status.append(f"TTS Provider: {TTS_PROVIDER}")
    if TTS_PROVIDER == "elevenlabs":
        try:
            test_audio = elevenlabs.text_to_speech.convert(
                text="test",
                voice_id=ELEVENLABS_VOICE_ID,
                model_id="eleven_turbo_v2_5",
            )
            size = sum(len(c) for c in test_audio if isinstance(c, bytes))
            status.append(f"ElevenLabs TTS: OK ({size} bytes, turbo_v2_5, voice={ELEVENLABS_VOICE_ID[:8]}...)")
        except Exception as e:
            status.append(f"ElevenLabs TTS: FAILED - {e}")
    elif TTS_PROVIDER == "openai":
        try:
            test_audio = openai_client.audio.speech.create(
                model=OPENAI_TTS_MODEL,
                voice=OPENAI_VOICE_ID,
                input="test",
            )
            size = len(b"".join(test_audio.iter_bytes()))
            status.append(f"OpenAI TTS: OK ({size} bytes, {OPENAI_TTS_MODEL}, voice={OPENAI_VOICE_ID})")
        except Exception as e:
            status.append(f"OpenAI TTS: FAILED - {e}")
    else:
        status.append("TTS: No provider configured")

    status.append(f"STT Provider: {STT_PROVIDER}")

    # Check Claude
    try:
        result = subprocess.run(
            ["claude", "-p", "Say OK", "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=CLAUDE_WORKING_DIR
        )
        if result.returncode == 0:
            status.append("Claude Code: OK")
        else:
            status.append(f"Claude Code: FAILED - {result.stderr[:50]}")
    except Exception as e:
        status.append(f"Claude Code: FAILED - {e}")

    # Session info
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    status.append(f"\nSessions: {len(state['sessions'])}")
    status.append(f"Current: {state['current_session'][:8] if state['current_session'] else 'None'}...")

    # MCP servers status
    status.extend(get_mcp_status(CLAUDE_SETTINGS_FILE))

    # Sandbox info
    status.append(f"\nSandbox: {SANDBOX_DIR}")
    status.append(f"Sandbox exists: {Path(SANDBOX_DIR).exists()}")

    # Chat info
    status.append(f"\nChat ID: {update.effective_chat.id}")
    status.append(f"Topic ID: {update.message.message_thread_id or 'None'}")
    status.append(f"User ID: {update.effective_user.id}")

    await update.message.reply_text("\n".join(status))


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /settings command - show settings menu."""
    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_authorized(update):
        return

    user_id = update.effective_user.id
    settings = get_user_settings(user_id)

    # Build settings message
    audio_status = "ON" if settings["audio_enabled"] else "OFF"
    speed = settings["voice_speed"]
    mode = settings.get("mode", "go_all")
    mode_display = "Go All" if mode == "go_all" else "Approve"
    watch_mode_val = settings.get("watch_mode", "off").upper()

    message = (
        f"Settings:\n\n"
        f"Mode: {mode_display}\n"
        f"Watch: {watch_mode_val}\n"
        f"Audio: {audio_status}\n"
        f"Voice Speed: {speed}x"
    )

    # Build inline keyboard
    keyboard = [
        [
            InlineKeyboardButton(f"Mode: {mode_display}", callback_data="setting_mode_toggle"),
            InlineKeyboardButton(f"Watch: {watch_mode_val}", callback_data="setting_watch_cycle"),
        ],
        [InlineKeyboardButton(f"Audio: {audio_status}", callback_data="setting_audio_toggle")],
        [
            InlineKeyboardButton("0.8x", callback_data="setting_speed_0.8"),
            InlineKeyboardButton("0.9x", callback_data="setting_speed_0.9"),
            InlineKeyboardButton("1.0x", callback_data="setting_speed_1.0"),
            InlineKeyboardButton("1.1x", callback_data="setting_speed_1.1"),
            InlineKeyboardButton("1.2x", callback_data="setting_speed_1.2"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(message, reply_markup=reply_markup)


# ============ Token Configuration Commands ============

async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /setup command - show API credentials status."""
    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_admin(update):
        return

    creds = load_credentials()

    # Check what's configured (saved creds or env vars)
    claude_set = bool(creds.get("claude_token") or os.getenv("ANTHROPIC_API_KEY"))
    elevenlabs_set = bool(creds.get("elevenlabs_key") or os.getenv("ELEVENLABS_API_KEY"))
    openai_set = bool(creds.get("openai_key") or os.getenv("OPENAI_API_KEY"))

    claude_status = "✓ Set" if claude_set else "✗ Not set"
    elevenlabs_status = "✓ Set" if elevenlabs_set else "✗ Not set (optional)"
    openai_status = "✓ Set" if openai_set else "✗ Not set (optional)"

    await update.message.reply_text(
        f"**API Credentials**\n\n"
        f"Claude: {claude_status}\n"
        f"ElevenLabs: {elevenlabs_status}\n"
        f"OpenAI: {openai_status}\n\n"
        f"**Active providers:**\n"
        f"TTS: `{TTS_PROVIDER}`"
        + (f" ({OPENAI_TTS_MODEL} / {OPENAI_VOICE_ID})" if TTS_PROVIDER == "openai" else f" ({ELEVENLABS_VOICE_ID[:8]}...)" if TTS_PROVIDER == "elevenlabs" else "") + "\n"
        f"STT: `{STT_PROVIDER}`"
        + (f" ({OPENAI_STT_MODEL})" if STT_PROVIDER == "openai" else " (scribe_v1)" if STT_PROVIDER == "elevenlabs" else "") + "\n\n"
        f"**To configure:**\n"
        f"`/claude_token <key>` - Set Anthropic API key\n"
        f"`/elevenlabs_key <key>` - Set ElevenLabs key\n"
        f"`/openai_key <key>` - Set OpenAI key\n\n"
        f"_Messages with keys are deleted immediately for security._",
        parse_mode="Markdown"
    )


async def cmd_claude_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /claude_token command - set Claude OAuth token."""
    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_admin(update):
        return

    # Delete the message immediately (contains sensitive token)
    thread_id = update.message.message_thread_id
    try:
        await update.message.delete()
    except Exception as e:
        logger.debug(f"Could not delete token message: {e}")

    # Get token from args
    if not context.args:
        await update.effective_chat.send_message(
            "Usage: `/claude_token <token>`\n\n"
            "Get token by running `claude setup-token` in your terminal.",
            message_thread_id=thread_id,
            parse_mode="Markdown"
        )
        return

    token = " ".join(context.args).strip()

    if not token.startswith("sk-ant-"):
        await update.effective_chat.send_message(
            "❌ Invalid token format. Token should start with `sk-ant-`",
            message_thread_id=thread_id,
            parse_mode="Markdown"
        )
        return

    # Save token
    creds = load_credentials()
    creds["claude_token"] = token
    save_credentials(creds)

    # Apply immediately
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token

    await update.effective_chat.send_message(
        "✓ Claude token saved and applied!",
        message_thread_id=thread_id
    )


async def cmd_elevenlabs_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /elevenlabs_key command - set ElevenLabs API key."""
    global elevenlabs, ELEVENLABS_API_KEY

    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_admin(update):
        return

    # Delete the message immediately (contains sensitive key)
    thread_id = update.message.message_thread_id
    try:
        await update.message.delete()
    except Exception as e:
        logger.debug(f"Could not delete key message: {e}")

    # Get key from args
    if not context.args:
        await update.effective_chat.send_message(
            "Usage: `/elevenlabs_key <key>`\n\n"
            "Get key from elevenlabs.io/app/settings/api-keys",
            message_thread_id=thread_id,
            parse_mode="Markdown"
        )
        return

    key = " ".join(context.args).strip()

    if len(key) < 20:
        await update.effective_chat.send_message(
            "❌ Invalid key format. Key seems too short.",
            message_thread_id=thread_id
        )
        return

    # Save key
    creds = load_credentials()
    creds["elevenlabs_key"] = key
    save_credentials(creds)

    # Apply immediately
    ELEVENLABS_API_KEY = key
    elevenlabs = ElevenLabs(api_key=key)

    await update.effective_chat.send_message(
        "✓ ElevenLabs API key saved and applied!",
        message_thread_id=thread_id
    )


async def cmd_openai_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /openai_key command - set OpenAI API key."""
    global openai_client, TTS_PROVIDER, STT_PROVIDER

    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_admin(update):
        return

    # Delete the message immediately (contains sensitive key)
    thread_id = update.message.message_thread_id
    try:
        await update.message.delete()
    except Exception as e:
        logger.debug(f"Could not delete key message: {e}")

    if not context.args:
        await update.effective_chat.send_message(
            "Usage: `/openai_key <key>`\n\n"
            "Get key from platform.openai.com/api-keys",
            message_thread_id=thread_id,
            parse_mode="Markdown"
        )
        return

    key = " ".join(context.args).strip()

    if not key.startswith("sk-"):
        await update.effective_chat.send_message(
            "❌ Invalid key format. OpenAI keys start with `sk-`",
            message_thread_id=thread_id,
            parse_mode="Markdown"
        )
        return

    # Save key
    creds = load_credentials()
    creds["openai_key"] = key
    save_credentials(creds)

    # Apply immediately
    os.environ["OPENAI_API_KEY"] = key
    openai_client = OpenAIClient(api_key=key)
    TTS_PROVIDER = resolve_provider("TTS_PROVIDER")
    STT_PROVIDER = resolve_provider("STT_PROVIDER")

    await update.effective_chat.send_message(
        f"✓ OpenAI API key saved and applied!\n"
        f"TTS: `{TTS_PROVIDER}` | STT: `{STT_PROVIDER}`",
        message_thread_id=thread_id,
        parse_mode="Markdown"
    )


async def handle_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle settings button callbacks."""
    query = update.callback_query
    logger.debug(f"SETTINGS CALLBACK received: {query.data} from user {update.effective_user.id}")

    user_id = update.effective_user.id
    settings = get_user_settings(user_id)
    callback_data = query.data

    if callback_data == "setting_audio_toggle":
        settings["audio_enabled"] = not settings["audio_enabled"]
        save_settings()
        logger.debug(f"Audio toggled to: {settings['audio_enabled']}")

    elif callback_data == "setting_mode_toggle":
        current_mode = settings.get("mode", "go_all")
        settings["mode"] = "approve" if current_mode == "go_all" else "go_all"
        save_settings()
        logger.debug(f"Mode toggled to: {settings['mode']}")

    elif callback_data == "setting_watch_cycle":
        cycle = {"off": "live", "live": "debug", "debug": "off"}
        settings["watch_mode"] = cycle.get(settings.get("watch_mode", "off"), "off")
        save_settings()
        logger.debug(f"Watch mode cycled to: {settings['watch_mode']}")

    elif callback_data.startswith("setting_speed_"):
        try:
            speed = float(callback_data.replace("setting_speed_", ""))
            if not 0.7 <= speed <= 1.2:
                await query.answer("Invalid speed range")
                return
        except ValueError:
            await query.answer("Invalid speed value")
            return

        settings["voice_speed"] = speed
        save_settings()
        logger.debug(f"Speed set to: {speed}")

    # Build updated settings menu
    audio_status = "ON" if settings["audio_enabled"] else "OFF"
    speed = settings["voice_speed"]
    mode = settings.get("mode", "go_all")
    mode_display = "Go All" if mode == "go_all" else "Approve"
    watch_mode_val = settings.get("watch_mode", "off").upper()

    message = f"Settings:\n\nMode: {mode_display}\nWatch: {watch_mode_val}\nAudio: {audio_status}\nVoice Speed: {speed}x"

    keyboard = [
        [
            InlineKeyboardButton(f"Mode: {mode_display}", callback_data="setting_mode_toggle"),
            InlineKeyboardButton(f"Watch: {watch_mode_val}", callback_data="setting_watch_cycle"),
        ],
        [InlineKeyboardButton(f"Audio: {audio_status}", callback_data="setting_audio_toggle")],
        [
            InlineKeyboardButton("0.8x", callback_data="setting_speed_0.8"),
            InlineKeyboardButton("0.9x", callback_data="setting_speed_0.9"),
            InlineKeyboardButton("1.0x", callback_data="setting_speed_1.0"),
            InlineKeyboardButton("1.1x", callback_data="setting_speed_1.1"),
            InlineKeyboardButton("1.2x", callback_data="setting_speed_1.2"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.edit_message_text(message, reply_markup=reply_markup)
    except Exception as e:
        logger.debug(f"Error updating settings menu: {e}")

    await query.answer()


async def handle_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle approval/rejection button callbacks."""
    query = update.callback_query
    callback_data = query.data

    logger.debug(f">>> APPROVAL CALLBACK received: {callback_data}")

    # Answer the callback immediately to prevent Telegram timeout
    await query.answer()

    if callback_data.startswith("approve_"):
        approval_id = callback_data.replace("approve_", "")
        logger.debug(f">>> Looking for approval_id: {approval_id} in {list(pending_approvals.keys())}")
        if approval_id in pending_approvals:
            # Verify that the user clicking is the one who requested
            if update.effective_user.id != pending_approvals[approval_id].get("user_id"):
                await query.answer("Only the requester can approve this")
                return

            tool_name = pending_approvals[approval_id]["tool_name"]
            pending_approvals[approval_id]["approved"] = True
            logger.debug(f">>> Setting event for {approval_id}")
            pending_approvals[approval_id]["event"].set()
            logger.debug(f">>> Event set, updating message")
            await query.edit_message_text(f"✓ Approved: {tool_name}")
        else:
            logger.debug(f">>> Approval {approval_id} not found (expired)")
            await query.edit_message_text("Approval expired")

    elif callback_data.startswith("reject_"):
        approval_id = callback_data.replace("reject_", "")
        logger.debug(f">>> Looking for approval_id: {approval_id} in {list(pending_approvals.keys())}")
        if approval_id in pending_approvals:
            # Verify that the user clicking is the one who requested
            if update.effective_user.id != pending_approvals[approval_id].get("user_id"):
                await query.answer("Only the requester can reject this")
                return

            tool_name = pending_approvals[approval_id]["tool_name"]
            pending_approvals[approval_id]["approved"] = False
            logger.debug(f">>> Setting event for {approval_id} (reject)")
            pending_approvals[approval_id]["event"].set()
            logger.debug(f">>> Event set, updating message")
            await query.edit_message_text(f"✗ Rejected: {tool_name}")
        else:
            logger.debug(f">>> Approval {approval_id} not found (expired)")
            await query.edit_message_text("Approval expired")


# ============ Voice Handler ============

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming voice messages."""
    # Ignore messages from bots (including ourselves)
    if update.effective_user.is_bot is True:
        return

    logger.debug(f"VOICE received from user {update.effective_user.id}, chat {update.effective_chat.id}, topic {update.message.message_thread_id}")

    # Topic filtering - ignore messages not in our topic
    if not should_handle_message(update.message.message_thread_id):
        logger.debug(f"Ignoring voice message - not in our topic (configured: {TOPIC_ID})")
        return

    if not _is_authorized(update):
        return

    user_id = update.effective_user.id

    # Rate limiting
    allowed, rate_msg = check_rate_limit(user_id)
    if not allowed:
        await update.message.reply_text(rate_msg)
        return

    state = get_user_state(user_id)
    settings = get_user_settings(user_id)

    # Acknowledge receipt
    processing_msg = await update.message.reply_text("Processing voice message...")
    logger.debug("Sent processing acknowledgement")

    try:
        # Download voice
        voice = await update.message.voice.get_file()
        voice_bytes = await voice.download_as_bytearray()

        # Transcribe
        await processing_msg.edit_text("Transcribing...")
        text = await transcribe_voice(bytes(voice_bytes))

        if text.startswith("[Transcription error"):
            await processing_msg.edit_text(text)
            return

        # Prepend compact summary if pending from /compact
        compact_summary = state.pop("compact_summary", None)
        if compact_summary:
            text = f"<previous_session_summary>\n{compact_summary}\n</previous_session_summary>\n\n{text}"
            save_state()

        # Show what was heard
        await processing_msg.edit_text(f"Heard: {text[:100]}{'...' if len(text) > 100 else ''}\n\nAsking Claude...")

        # Call Claude with user settings
        continue_last = state["current_session"] is not None
        response, new_session_id, metadata = await call_claude(
            text,
            session_id=state["current_session"],
            continue_last=continue_last,
            user_settings=settings,
            update=update,
            context=context,
            processing_msg=processing_msg,
        )

        # Update session state
        if new_session_id and new_session_id != state["current_session"]:
            state["current_session"] = new_session_id
            if new_session_id not in state["sessions"]:
                state["sessions"].append(new_session_id)
            save_state()

        # Send text response (split if too long)
        tool_log = metadata.get("tool_log", [])
        await finalize_response(update, processing_msg, response)

        # Generate and send voice response if audio enabled
        if settings["audio_enabled"]:
            tts_text = response[:MAX_VOICE_CHARS] if len(response) > MAX_VOICE_CHARS else response
            audio = await text_to_speech(tts_text, speed=settings["voice_speed"])
            if audio:
                await update.message.reply_voice(voice=audio)

    except Exception as e:
        logger.error(f"Error in handle_voice: {e}")
        await processing_msg.edit_text(f"Error: {e}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages (same flow as voice, skip transcription)."""
    # Ignore messages from bots (including ourselves)
    if update.effective_user.is_bot is True:
        return

    logger.debug(f"TEXT received: '{update.message.text[:50]}' from user {update.effective_user.id}, chat {update.effective_chat.id}, topic {update.message.message_thread_id}")

    # Topic filtering - ignore messages not in our topic
    if not should_handle_message(update.message.message_thread_id):
        logger.debug(f"Ignoring text message - not in our topic (configured: {TOPIC_ID})")
        return

    if not _is_authorized(update):
        return

    user_id = update.effective_user.id

    # Rate limiting
    allowed, rate_msg = check_rate_limit(user_id)
    if not allowed:
        await update.message.reply_text(rate_msg)
        return

    state = get_user_state(user_id)
    settings = get_user_settings(user_id)
    text = update.message.text

    processing_msg = await update.message.reply_text("Asking Claude...")
    logger.debug("Sent processing acknowledgement")

    # Prepend compact summary if pending from /compact
    compact_summary = state.pop("compact_summary", None)
    if compact_summary:
        text = f"<previous_session_summary>\n{compact_summary}\n</previous_session_summary>\n\n{text}"
        save_state()

    try:
        continue_last = state["current_session"] is not None
        response, new_session_id, metadata = await call_claude(
            text,
            session_id=state["current_session"],
            continue_last=continue_last,
            user_settings=settings,
            update=update,
            context=context,
            processing_msg=processing_msg,
        )

        if new_session_id and new_session_id != state["current_session"]:
            state["current_session"] = new_session_id
            if new_session_id not in state["sessions"]:
                state["sessions"].append(new_session_id)
            save_state()

        # Send text response (split if too long)
        tool_log = metadata.get("tool_log", [])
        await finalize_response(update, processing_msg, response)

        # Send voice response if audio enabled
        if settings["audio_enabled"]:
            tts_text = response[:MAX_VOICE_CHARS] if len(response) > MAX_VOICE_CHARS else response
            audio = await text_to_speech(tts_text, speed=settings["voice_speed"])
            if audio:
                await update.message.reply_voice(voice=audio)

    except Exception as e:
        logger.error(f"Error in handle_text: {e}")
        await processing_msg.edit_text(f"Error: {e}")


# ============ Photo Handler ============

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming photo messages — save to sandbox and let Claude view them."""
    if update.effective_user.is_bot is True:
        return

    logger.debug(f"PHOTO received from user {update.effective_user.id}, chat {update.effective_chat.id}")

    if not should_handle_message(update.message.message_thread_id):
        logger.debug(f"Ignoring photo - not in our topic")
        return

    if not _is_authorized(update):
        return

    user_id = update.effective_user.id

    allowed, rate_msg = check_rate_limit(user_id)
    if not allowed:
        await update.message.reply_text(rate_msg)
        return

    state = get_user_state(user_id)
    settings = get_user_settings(user_id)

    processing_msg = await update.message.reply_text("Processing photo...")

    try:
        # Get highest resolution photo
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()

        # Save to sandbox
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        photo_path = Path(SANDBOX_DIR) / f"photo_{timestamp}.jpg"
        await photo_file.download_to_drive(str(photo_path))

        # Build prompt
        caption = update.message.caption or ""
        if caption:
            prompt = f"I sent you a photo. It's saved at: {photo_path}\n\nMy message: {caption}"
        else:
            prompt = f"I sent you a photo. It's saved at: {photo_path}\n\nPlease look at it and describe what you see, or help me with whatever is shown."

        # Prepend compact summary if pending from /compact
        compact_summary = state.pop("compact_summary", None)
        if compact_summary:
            prompt = f"<previous_session_summary>\n{compact_summary}\n</previous_session_summary>\n\n{prompt}"
            save_state()

        await processing_msg.edit_text("Asking Claude...")

        continue_last = state["current_session"] is not None
        response, new_session_id, metadata = await call_claude(
            prompt,
            session_id=state["current_session"],
            continue_last=continue_last,
            user_settings=settings,
            update=update,
            context=context,
            processing_msg=processing_msg,
        )

        if new_session_id and new_session_id != state["current_session"]:
            state["current_session"] = new_session_id
            if new_session_id not in state["sessions"]:
                state["sessions"].append(new_session_id)
            save_state()

        tool_log = metadata.get("tool_log", [])
        await finalize_response(update, processing_msg, response)

        if settings["audio_enabled"]:
            tts_text = response[:MAX_VOICE_CHARS] if len(response) > MAX_VOICE_CHARS else response
            audio = await text_to_speech(tts_text, speed=settings["voice_speed"])
            if audio:
                await update.message.reply_voice(voice=audio)

    except Exception as e:
        logger.error(f"Error in handle_photo: {e}")
        await processing_msg.edit_text(f"Error: {e}")


def main():
    """Main entry point."""
    # Apply any saved credentials first (from previous /setup)
    apply_saved_credentials()

    # Now validate environment (will check if auth is configured)
    validate_environment()
    load_state()
    load_settings()

    # Enable concurrent_updates to allow callback handlers to run while message handlers await
    # This is CRITICAL for approve mode - the approval callback needs to run while call_claude waits
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).concurrent_updates(True).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("compact", cmd_compact))
    app.add_handler(CommandHandler("continue", cmd_continue))
    app.add_handler(CommandHandler("sessions", cmd_sessions))
    app.add_handler(CommandHandler("switch", cmd_switch))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("setup", cmd_setup))
    app.add_handler(CommandHandler("claude_token", cmd_claude_token))
    app.add_handler(CommandHandler("elevenlabs_key", cmd_elevenlabs_key))
    app.add_handler(CommandHandler("openai_key", cmd_openai_key))

    # Callback handlers for inline keyboards
    app.add_handler(CallbackQueryHandler(handle_settings_callback, pattern="^setting_"))
    app.add_handler(CallbackQueryHandler(handle_approval_callback, pattern="^(approve_|reject_)"))

    # Messages
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Ensure sandbox exists at startup
    Path(SANDBOX_DIR).mkdir(parents=True, exist_ok=True)

    # Register commands in Telegram menu (the "/" autocomplete list)
    async def post_init(application):
        await application.bot.set_my_commands([
            BotCommand("new",      "Start a new session"),
            BotCommand("cancel",   "Cancel current request"),
            BotCommand("compact",  "Summarize & compress current session"),
            BotCommand("continue", "Continue last session"),
            BotCommand("sessions", "List recent sessions"),
            BotCommand("switch",   "Switch to a session by ID"),
            BotCommand("status",   "Current session info"),
            BotCommand("settings", "Voice, mode & speed settings"),
            BotCommand("health",   "Check bot & API status"),
            BotCommand("setup",    "Configure API tokens"),
            BotCommand("start",    "Show help"),
        ])
    app.post_init = post_init

    logger.debug("Bot starting...")
    logger.debug(f"Persona: {PERSONA_NAME}")
    logger.debug(f"TTS: {TTS_PROVIDER}" + (f" ({OPENAI_TTS_MODEL} / {OPENAI_VOICE_ID})" if TTS_PROVIDER == "openai" else f" (eleven_turbo_v2_5 / {ELEVENLABS_VOICE_ID})" if TTS_PROVIDER == "elevenlabs" else " (none)"))
    logger.debug(f"STT: {STT_PROVIDER}" + (f" ({OPENAI_STT_MODEL})" if STT_PROVIDER == "openai" else " (scribe_v1)" if STT_PROVIDER == "elevenlabs" else " (none)"))
    logger.debug(f"Sandbox: {SANDBOX_DIR}")
    logger.debug(f"Read access: {CLAUDE_WORKING_DIR}")
    logger.debug(f"Chat ID: {ALLOWED_CHAT_ID}")
    logger.debug(f"Topic ID: {TOPIC_ID or 'ALL (no filter)'}")
    logger.debug(f"System prompt: {SYSTEM_PROMPT_FILE or 'default'}")
    print(f"{PERSONA_NAME} is ready. Waiting for messages...")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"]
    )


if __name__ == "__main__":
    main()
