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
import time
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
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
    run_remote_trigger_list,
    build_automations_list,
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
    cmd_sessions, cmd_switch, cmd_status, cmd_search,
    handle_session_switch_callback,
)
from handlers.admin import (
    cmd_setup, cmd_claude_token, cmd_elevenlabs_key, cmd_openai_key,
    handle_settings_callback, handle_approval_callback,
    load_credentials, save_credentials, apply_saved_credentials,
)
from handlers.messages import (
    handle_voice, handle_text, handle_photo, handle_automations_callback,
)
import voice_service
from voice_service import (
    is_valid_transcription,
    format_tts_fallback,
)
import claude_service
from claude_service import (
    build_claude_options,
    WorkingIndicator,
)
TELEGRAM_BOT_TOKEN = _cfg.TELEGRAM_BOT_TOKEN
ELEVENLABS_API_KEY = _cfg.ELEVENLABS_API_KEY
ALLOWED_CHAT_ID = _cfg.ALLOWED_CHAT_ID
TOPIC_ID = _cfg.TOPIC_ID
CLAUDE_WORKING_DIR = _cfg.CLAUDE_WORKING_DIR
SANDBOX_DIR = _cfg.SANDBOX_DIR
PERSONA_NAME = _cfg.PERSONA_NAME
SYSTEM_PROMPT_FILE = _cfg.SYSTEM_PROMPT_FILE
ELEVENLABS_VOICE_ID = _cfg.ELEVENLABS_VOICE_ID
CLAUDE_SETTINGS_FILE = _cfg.CLAUDE_SETTINGS_FILE
TTS_PROVIDER = _cfg.TTS_PROVIDER
STT_PROVIDER = _cfg.STT_PROVIDER
OPENAI_VOICE_ID = _cfg.OPENAI_VOICE_ID
OPENAI_TTS_MODEL = _cfg.OPENAI_TTS_MODEL
OPENAI_STT_MODEL = _cfg.OPENAI_STT_MODEL

# OpenAI client (None if no key configured)
openai_client = OpenAIClient(api_key=_cfg.OPENAI_API_KEY) if _cfg.OPENAI_API_KEY else None

# ElevenLabs client
elevenlabs = ElevenLabs(api_key=ELEVENLABS_API_KEY)


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


# ============ Command Handlers ============

async def handle_unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catch-all for unrecognised commands."""
    if not should_handle_message(update.message.message_thread_id):
        return
    cmd = update.message.text.split()[0]
    await update.message.reply_text(
        f"Unknown command: {cmd}\n\nType /start to see all available commands."
    )


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /health command - check all systems."""
    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_authorized(update):
        return

    logger.debug(f"HEALTH command from user {update.effective_user.id}, chat {update.effective_chat.id}, topic {update.message.message_thread_id}")

    checking_msg = await update.message.reply_text("🔍 Checking systems...")

    status = []
    status.append("=== Health Check ===\n")

    # TTS provider check
    status.append(f"TTS Provider: {TTS_PROVIDER}")
    if TTS_PROVIDER == "elevenlabs":
        try:
            test_audio = await asyncio.to_thread(
                elevenlabs.text_to_speech.convert,
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
            test_audio = await asyncio.to_thread(
                openai_client.audio.speech.create,
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

    await checking_msg.edit_text("\n".join(status))


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
    app.add_handler(CommandHandler("search", cmd_search))
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
    app.add_handler(CallbackQueryHandler(handle_session_switch_callback, pattern="^sess_switch_"))

    # Messages
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    # Unknown commands — must be last
    app.add_handler(MessageHandler(filters.COMMAND, handle_unknown_command))

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
            BotCommand("search",   "Search sessions by keyword"),
            BotCommand("status",   "Current session info"),
            BotCommand("settings",    "Voice, mode & speed settings"),
            BotCommand("health",   "Check bot & API status"),
            BotCommand("setup",       "Configure API tokens"),
            BotCommand("automations", "Manage scheduled automations"),
            BotCommand("start",       "Show help"),
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
    # Python 3.14 removed implicit event loop creation in get_event_loop()
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
