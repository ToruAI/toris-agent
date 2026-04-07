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
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ChatAction
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
from automations import (
    cron_to_human,
    run_remote_trigger_list,
    run_remote_trigger_run,
    run_remote_trigger_toggle,
    build_automations_list,
    build_automation_card,
)
import shared_state as _shared
from state_manager import StateManager, get_manager

load_dotenv()



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

# Config — loaded from config.py (single source of truth for all env vars)
import config as _cfg
from auth import should_handle_message, _is_authorized, _is_admin, check_rate_limit, rate_limits
from handlers.session import (
    cmd_start, cmd_new, cmd_cancel, cmd_compact, cmd_continue,
    cmd_sessions, cmd_switch, cmd_status,
    parse_session_name, format_sessions_list,
)
import voice_service
from voice_service import (
    transcribe_voice,
    is_valid_transcription,
    text_to_speech,
    format_tts_fallback,
)
import claude_service
from claude_service import (
    call_claude,
    build_claude_options,
    build_dynamic_prompt,
    load_megg_context,
    format_tool_call,
    WorkingIndicator,
)
TELEGRAM_BOT_TOKEN = _cfg.TELEGRAM_BOT_TOKEN
ELEVENLABS_API_KEY = _cfg.ELEVENLABS_API_KEY
ALLOWED_CHAT_ID = _cfg.ALLOWED_CHAT_ID
ADMIN_USER_IDS = _cfg.ADMIN_USER_IDS
TOPIC_ID = _cfg.TOPIC_ID
CLAUDE_WORKING_DIR = _cfg.CLAUDE_WORKING_DIR
SANDBOX_DIR = _cfg.SANDBOX_DIR
MAX_VOICE_CHARS = _cfg.MAX_VOICE_CHARS
CLAUDE_TIMEOUT = _cfg.CLAUDE_TIMEOUT
PERSONA_NAME = _cfg.PERSONA_NAME
SYSTEM_PROMPT_FILE = _cfg.SYSTEM_PROMPT_FILE
ELEVENLABS_VOICE_ID = _cfg.ELEVENLABS_VOICE_ID
CLAUDE_SETTINGS_FILE = _cfg.CLAUDE_SETTINGS_FILE
TTS_PROVIDER = _cfg.TTS_PROVIDER
STT_PROVIDER = _cfg.STT_PROVIDER
OPENAI_VOICE_ID = _cfg.OPENAI_VOICE_ID
OPENAI_TTS_MODEL = _cfg.OPENAI_TTS_MODEL
OPENAI_STT_MODEL = _cfg.OPENAI_STT_MODEL
OPENAI_VOICE_INSTRUCTIONS = _cfg.OPENAI_VOICE_INSTRUCTIONS
STT_LANGUAGE = _cfg.STT_LANGUAGE

# OpenAI client (None if no key configured)
openai_client = OpenAIClient(api_key=_cfg.OPENAI_API_KEY) if _cfg.OPENAI_API_KEY else None

# Voice settings for expressive delivery
VOICE_SETTINGS = {
    "stability": 0.3,           # More emotional range
    "similarity_boost": 0.75,   # Good voice match
    "style": 0.4,               # Some style exaggeration
    "speed": 1.1,               # Slightly faster (range: 0.7-1.2)
}

# ElevenLabs client
elevenlabs = ElevenLabs(api_key=ELEVENLABS_API_KEY)

# Credentials file for user-provided API keys
CREDENTIALS_FILE = _cfg.CREDENTIALS_FILE


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

    new_elevenlabs_key = None
    new_openai_key = None

    if creds.get("elevenlabs_key"):
        ELEVENLABS_API_KEY = creds["elevenlabs_key"]
        os.environ["ELEVENLABS_API_KEY"] = creds["elevenlabs_key"]
        elevenlabs = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        new_elevenlabs_key = creds["elevenlabs_key"]
        logger.debug("Applied saved ElevenLabs key")

    if creds.get("openai_key"):
        os.environ["OPENAI_API_KEY"] = creds["openai_key"]
        openai_client = OpenAIClient(api_key=creds["openai_key"])
        new_openai_key = creds["openai_key"]
        logger.debug("Applied saved OpenAI key")

    # Re-resolve providers after credentials are loaded
    TTS_PROVIDER = _cfg.resolve_provider("TTS_PROVIDER")
    STT_PROVIDER = _cfg.resolve_provider("STT_PROVIDER")

    # Sync voice_service clients
    voice_service.reconfigure(
        elevenlabs_key=new_elevenlabs_key,
        openai_key=new_openai_key,
        tts_provider=TTS_PROVIDER,
        stt_provider=STT_PROVIDER,
    )


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


def error_message(context: str, exc: Exception) -> str:
    """Return a user-friendly error string with just enough context."""
    exc_str = str(exc)
    if "rate" in exc_str.lower() or "429" in exc_str:
        return f"❌ {context}: Rate limit hit. Wait a moment and try again."
    if "timeout" in exc_str.lower():
        return f"❌ {context}: Timed out. The service may be slow — try again."
    if "auth" in exc_str.lower() or "401" in exc_str or "403" in exc_str:
        return f"❌ {context}: Authentication failed. Check API keys."
    if "connect" in exc_str.lower() or "network" in exc_str.lower():
        return f"❌ {context}: Network error. Check your connection."
    return f"❌ {context}: {exc_str[:120]}"


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



# ============ Helpers ============

async def typing_loop(update: Update, context: ContextTypes.DEFAULT_TYPE, stop_event: asyncio.Event):
    """Send typing indicator every 4s until stop_event is set (Telegram typing expires after 5s)."""
    while not stop_event.is_set():
        try:
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id,
                action=ChatAction.TYPING,
            )
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            pass



# ============ Command Handlers ============

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
        result = await asyncio.to_thread(
            subprocess.run,
            ["claude", "-p", "Say OK", "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=CLAUDE_WORKING_DIR,
        )
        if result.returncode == 0:
            status.append("Claude Code: OK")
        else:
            status.append(f"Claude Code: FAILED - {result.stderr[:50]}")
    except Exception as e:
        status.append(f"Claude Code: FAILED - {e}")

    # Session info
    user_id = update.effective_user.id
    state = get_manager().get_user_state(user_id)
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
    settings = get_manager().get_user_settings(user_id)

    # Build settings message
    audio_status = "ON" if settings["audio_enabled"] else "OFF"
    speed = settings["voice_speed"]
    mode = settings.get("mode", "go_all")
    mode_display = "Go All" if mode == "go_all" else "Approve"
    watch_mode_val = settings.get("watch_mode", "off").upper()
    card_style = settings.get("automation_card_style", "full")
    card_style_display = "Pełna" if card_style == "full" else "Kompakt"

    message = (
        f"Settings:\n\n"
        f"Mode: {mode_display}\n"
        f"Watch: {watch_mode_val}\n"
        f"Audio: {audio_status}\n"
        f"Voice Speed: {speed}x\n"
        f"Auto karta: {card_style_display}"
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
        [InlineKeyboardButton(f"Auto karta: {card_style_display}", callback_data="setting_card_style_toggle")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(message, reply_markup=reply_markup)


async def cmd_automations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /automations command — show scheduled tasks list."""
    if not should_handle_message(update.message.message_thread_id):
        return
    if not _is_authorized(update):
        return

    loading_msg = await update.message.reply_text("⏳ Ładuję automacje...")

    triggers = await run_remote_trigger_list()
    text, markup = build_automations_list(triggers)

    try:
        await loading_msg.edit_text(text, reply_markup=markup)
    except Exception as e:
        logger.warning(f"cmd_automations edit error: {e}")
        await update.message.reply_text(text, reply_markup=markup)


async def handle_automations_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all auto_* callback button taps."""
    query = update.callback_query

    if not _is_authorized(update):
        await query.answer()
        return

    data = query.data
    user_id = update.effective_user.id
    settings = get_manager().get_user_settings(user_id)
    card_style = settings.get("automation_card_style", "full")

    # ── Back to list ──────────────────────────────────────────
    if data in ("auto_list", "auto_refresh"):
        await query.answer()
        await query.edit_message_text("⏳ Ładuję automacje...")
        triggers = await run_remote_trigger_list()
        text, markup = build_automations_list(triggers)
        try:
            await query.edit_message_text(text, reply_markup=markup)
        except Exception as e:
            logger.warning(f"auto_list edit error: {e}")

    # ── Open card ─────────────────────────────────────────────
    elif data.startswith("auto_card_"):
        await query.answer()
        trigger_id = data[len("auto_card_"):]
        await query.edit_message_text("⏳...")
        triggers = await run_remote_trigger_list()
        trigger = next((t for t in triggers if t["id"] == trigger_id), None)
        if trigger is None:
            await query.edit_message_text("❌ Nie znaleziono automacji.")
            return
        text, markup = build_automation_card(trigger, style=card_style)
        try:
            await query.edit_message_text(text, reply_markup=markup)
        except Exception as e:
            logger.warning(f"auto_card edit error: {e}")

    # ── Run now ───────────────────────────────────────────────
    elif data.startswith("auto_run_"):
        trigger_id = data[len("auto_run_"):]
        await query.answer("▶ Uruchamiam...")
        ok = await run_remote_trigger_run(trigger_id)
        status = "✓ Uruchomiono!" if ok else "❌ Błąd uruchamiania"
        try:
            await query.edit_message_text(query.message.text + f"\n\n{status}", reply_markup=query.message.reply_markup)
        except Exception:
            pass

    # ── Toggle enable/disable ─────────────────────────────────
    elif data.startswith("auto_toggle_"):
        # format: auto_toggle_off_{id} or auto_toggle_on_{id}
        rest = data[len("auto_toggle_"):]
        enable = rest.startswith("on_")
        trigger_id = rest[len("on_"):] if enable else rest[len("off_"):]
        await query.answer()
        ok = await run_remote_trigger_toggle(trigger_id, enable=enable)
        if ok:
            # Refresh card
            triggers = await run_remote_trigger_list()
            trigger = next((t for t in triggers if t["id"] == trigger_id), None)
            if trigger:
                text, markup = build_automation_card(trigger, style=card_style)
                await query.edit_message_text(text, reply_markup=markup)
            else:
                # Trigger disappeared after toggle — show list instead
                triggers2 = await run_remote_trigger_list()
                text2, markup2 = build_automations_list(triggers2)
                await query.edit_message_text(text2, reply_markup=markup2)
        else:
            try:
                await query.edit_message_text("❌ Błąd zmiany stanu automacji.", reply_markup=query.message.reply_markup)
            except Exception:
                pass

    # ── New automation ────────────────────────────────────────
    elif data == "auto_new":
        await query.answer()
        await query.edit_message_text(
            '💬 Opisz automację głosem lub tekstem.\n\n'
            'Np. „stwórz daily standup o 8 rano sprawdzający PR-y na GitHubie"'
        )

    # ── Edit prompt (conversational) ──────────────────────────
    elif data.startswith("auto_edit_"):
        await query.answer()
        trigger_id = data[len("auto_edit_"):]
        await query.edit_message_text(
            '✎ Co chcesz zmienić w tej automacji?\n\n'
            'Opisz głosem lub tekstem — np. „zmień godzinę na 9 rano" albo „dodaj sprawdzanie CI"'
        )


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
    voice_service.reconfigure(elevenlabs_key=key, tts_provider=TTS_PROVIDER)

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
    TTS_PROVIDER = _cfg.resolve_provider("TTS_PROVIDER")
    STT_PROVIDER = _cfg.resolve_provider("STT_PROVIDER")
    voice_service.reconfigure(openai_key=key, tts_provider=TTS_PROVIDER, stt_provider=STT_PROVIDER)

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
    settings = get_manager().get_user_settings(user_id)
    callback_data = query.data

    if callback_data == "setting_audio_toggle":
        settings["audio_enabled"] = not settings["audio_enabled"]
        get_manager().save_settings()
        logger.debug(f"Audio toggled to: {settings['audio_enabled']}")

    elif callback_data == "setting_mode_toggle":
        current_mode = settings.get("mode", "go_all")
        settings["mode"] = "approve" if current_mode == "go_all" else "go_all"
        get_manager().save_settings()
        logger.debug(f"Mode toggled to: {settings['mode']}")

    elif callback_data == "setting_watch_cycle":
        cycle = {"off": "live", "live": "debug", "debug": "off"}
        settings["watch_mode"] = cycle.get(settings.get("watch_mode", "off"), "off")
        get_manager().save_settings()
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
        get_manager().save_settings()
        logger.debug(f"Speed set to: {speed}")

    elif callback_data == "setting_card_style_toggle":
        current = settings.get("automation_card_style", "full")
        settings["automation_card_style"] = "compact" if current == "full" else "full"
        get_manager().save_settings()
        logger.debug(f"Card style toggled to: {settings['automation_card_style']}")

    # Build updated settings menu
    audio_status = "ON" if settings["audio_enabled"] else "OFF"
    speed = settings["voice_speed"]
    mode = settings.get("mode", "go_all")
    mode_display = "Go All" if mode == "go_all" else "Approve"
    watch_mode_val = settings.get("watch_mode", "off").upper()
    card_style = settings.get("automation_card_style", "full")
    card_style_display = "Pełna" if card_style == "full" else "Kompakt"

    message = f"Settings:\n\nMode: {mode_display}\nWatch: {watch_mode_val}\nAudio: {audio_status}\nVoice Speed: {speed}x\nAuto karta: {card_style_display}"

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
        [InlineKeyboardButton(f"Auto karta: {card_style_display}", callback_data="setting_card_style_toggle")],
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
        logger.debug(f">>> Looking for approval_id: {approval_id} in {list(_shared.pending_approvals.keys())}")
        if approval_id in _shared.pending_approvals:
            # Verify that the user clicking is the one who requested
            if update.effective_user.id != _shared.pending_approvals[approval_id].get("user_id"):
                await query.answer("Only the requester can approve this")
                return

            tool_name = _shared.pending_approvals[approval_id]["tool_name"]
            _shared.pending_approvals[approval_id]["approved"] = True
            logger.debug(f">>> Setting event for {approval_id}")
            _shared.pending_approvals[approval_id]["event"].set()
            logger.debug(f">>> Event set, updating message")
            await query.edit_message_text(f"✓ Approved: {tool_name}")
        else:
            logger.debug(f">>> Approval {approval_id} not found (expired)")
            await query.edit_message_text("Approval expired")

    elif callback_data.startswith("reject_"):
        approval_id = callback_data.replace("reject_", "")
        logger.debug(f">>> Looking for approval_id: {approval_id} in {list(_shared.pending_approvals.keys())}")
        if approval_id in _shared.pending_approvals:
            # Verify that the user clicking is the one who requested
            if update.effective_user.id != _shared.pending_approvals[approval_id].get("user_id"):
                await query.answer("Only the requester can reject this")
                return

            tool_name = _shared.pending_approvals[approval_id]["tool_name"]
            _shared.pending_approvals[approval_id]["approved"] = False
            logger.debug(f">>> Setting event for {approval_id} (reject)")
            _shared.pending_approvals[approval_id]["event"].set()
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

    state = get_manager().get_user_state(user_id)
    settings = get_manager().get_user_settings(user_id)

    # Typing indicator first — signals immediately that bot is alive
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    typing_stop = asyncio.Event()
    asyncio.ensure_future(typing_loop(update, context, typing_stop))
    processing_msg = await update.message.reply_text("Processing voice message...")
    logger.debug("Sent processing acknowledgement")

    try:
        # Download voice
        voice = await update.message.voice.get_file()
        voice_bytes = await voice.download_as_bytearray()

        # Transcribe
        await processing_msg.edit_text("Transcribing...")
        text = await transcribe_voice(bytes(voice_bytes))

        if not is_valid_transcription(text):
            if text.startswith("[Transcription error"):
                await processing_msg.edit_text(f"❌ Couldn't transcribe audio.\n{text}")
            else:
                await processing_msg.edit_text("❌ Couldn't hear anything. Try speaking more clearly.")
            return

        # Prepend compact summary if pending from /compact
        compact_summary = state.pop("compact_summary", None)
        if compact_summary:
            text = f"<previous_session_summary>\n{compact_summary}\n</previous_session_summary>\n\n{text}"
            get_manager().save_state()

        # Show what was heard
        await processing_msg.edit_text(f"Heard: {text[:100]}{'...' if len(text) > 100 else ''}\n\nToris thinking...")

        # Call Claude with user settings — WorkingIndicator gives periodic updates
        continue_last = state["current_session"] is not None
        indicator = WorkingIndicator(edit_fn=processing_msg.edit_text, interval=5.0)
        indicator.start()
        try:
            response, new_session_id, metadata = await call_claude(
                text,
                session_id=state["current_session"],
                continue_last=continue_last,
                user_settings=settings,
                update=update,
                context=context,
                processing_msg=processing_msg,
            )
        finally:
            indicator.stop()

        # Update session state
        async with get_manager().get_lock(user_id):
            if new_session_id and new_session_id != state["current_session"]:
                state["current_session"] = new_session_id
                name = state.pop("pending_session_name", None)
                state.setdefault("session_names", {})[new_session_id] = name
                if new_session_id not in state["sessions"]:
                    state["sessions"].append(new_session_id)
                get_manager().save_state()

        # Send text response (split if too long)
        await finalize_response(update, processing_msg, response)

        # Generate and send voice response if audio enabled
        if settings["audio_enabled"]:
            tts_text = response[:MAX_VOICE_CHARS] if len(response) > MAX_VOICE_CHARS else response
            audio = await text_to_speech(tts_text, speed=settings["voice_speed"])
            if audio:
                await update.message.reply_voice(voice=audio)
            else:
                await update.message.reply_text(format_tts_fallback(tts_text))

    except Exception as e:
        logger.exception("Error in handle_voice")
        await processing_msg.edit_text(error_message("Voice processing failed", e))
    finally:
        typing_stop.set()


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

    state = get_manager().get_user_state(user_id)
    settings = get_manager().get_user_settings(user_id)
    text = update.message.text

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    typing_stop = asyncio.Event()
    asyncio.ensure_future(typing_loop(update, context, typing_stop))
    processing_msg = await update.message.reply_text("Toris thinking...")

    # Prepend compact summary if pending from /compact
    compact_summary = state.pop("compact_summary", None)
    if compact_summary:
        text = f"<previous_session_summary>\n{compact_summary}\n</previous_session_summary>\n\n{text}"
        get_manager().save_state()

    try:
        continue_last = state["current_session"] is not None
        indicator = WorkingIndicator(edit_fn=processing_msg.edit_text, interval=5.0)
        indicator.start()
        try:
            response, new_session_id, metadata = await call_claude(
                text,
                session_id=state["current_session"],
                continue_last=continue_last,
                user_settings=settings,
                update=update,
                context=context,
                processing_msg=processing_msg,
            )
        finally:
            indicator.stop()

        async with get_manager().get_lock(user_id):
            if new_session_id and new_session_id != state["current_session"]:
                state["current_session"] = new_session_id
                name = state.pop("pending_session_name", None)
                state.setdefault("session_names", {})[new_session_id] = name
                if new_session_id not in state["sessions"]:
                    state["sessions"].append(new_session_id)
                get_manager().save_state()

        # Send text response (split if too long)
        await finalize_response(update, processing_msg, response)

        # Send voice response if audio enabled
        if settings["audio_enabled"]:
            tts_text = response[:MAX_VOICE_CHARS] if len(response) > MAX_VOICE_CHARS else response
            audio = await text_to_speech(tts_text, speed=settings["voice_speed"])
            if audio:
                await update.message.reply_voice(voice=audio)
            else:
                await update.message.reply_text(format_tts_fallback(tts_text))

    except Exception as e:
        logger.exception("Error in handle_text")
        await processing_msg.edit_text(error_message("Text processing failed", e))
    finally:
        typing_stop.set()


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

    state = get_manager().get_user_state(user_id)
    settings = get_manager().get_user_settings(user_id)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    typing_stop = asyncio.Event()
    asyncio.ensure_future(typing_loop(update, context, typing_stop))
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
            get_manager().save_state()

        await processing_msg.edit_text("Toris thinking...")

        continue_last = state["current_session"] is not None
        indicator = WorkingIndicator(edit_fn=processing_msg.edit_text, interval=5.0)
        indicator.start()
        try:
            response, new_session_id, metadata = await call_claude(
                prompt,
                session_id=state["current_session"],
                continue_last=continue_last,
                user_settings=settings,
                update=update,
                context=context,
                processing_msg=processing_msg,
            )
        finally:
            indicator.stop()

        async with get_manager().get_lock(user_id):
            if new_session_id and new_session_id != state["current_session"]:
                state["current_session"] = new_session_id
                name = state.pop("pending_session_name", None)
                state.setdefault("session_names", {})[new_session_id] = name
                if new_session_id not in state["sessions"]:
                    state["sessions"].append(new_session_id)
                get_manager().save_state()

        await finalize_response(update, processing_msg, response)

        if settings["audio_enabled"]:
            tts_text = response[:MAX_VOICE_CHARS] if len(response) > MAX_VOICE_CHARS else response
            audio = await text_to_speech(tts_text, speed=settings["voice_speed"])
            if audio:
                await update.message.reply_voice(voice=audio)
            else:
                await update.message.reply_text(format_tts_fallback(tts_text))

    except Exception as e:
        logger.exception("Error in handle_photo")
        await processing_msg.edit_text(error_message("Photo processing failed", e))
    finally:
        typing_stop.set()


def main():
    """Main entry point."""
    # Apply any saved credentials first (from previous /setup)
    apply_saved_credentials()

    # Now validate environment (will check if auth is configured)
    validate_environment()
    StateManager.init(_cfg.STATE_FILE, _cfg.SETTINGS_FILE)
    get_manager().load()

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
    app.add_handler(CommandHandler("automations", cmd_automations))
    app.add_handler(CommandHandler("setup", cmd_setup))
    app.add_handler(CommandHandler("claude_token", cmd_claude_token))
    app.add_handler(CommandHandler("elevenlabs_key", cmd_elevenlabs_key))
    app.add_handler(CommandHandler("openai_key", cmd_openai_key))

    # Callback handlers for inline keyboards
    app.add_handler(CallbackQueryHandler(handle_settings_callback, pattern="^setting_"))
    app.add_handler(CallbackQueryHandler(handle_approval_callback, pattern="^(approve_|reject_)"))
    app.add_handler(CallbackQueryHandler(handle_automations_callback, pattern="^auto_"))

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
            BotCommand("settings",    "Voice, mode & speed settings"),
            BotCommand("automations", "Manage scheduled automations"),
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
