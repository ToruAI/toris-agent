"""
Admin command handlers and credential management.

Commands: /setup /claude_token /elevenlabs_key /openai_key
Callbacks: settings inline keyboard, tool approval
Credentials: load/save/apply API keys
"""
import json
import logging
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import config as _cfg
import shared_state as _shared
import voice_service
from auth import should_handle_message, _is_admin, _is_authorized
from state_manager import get_manager

logger = logging.getLogger(__name__)

CREDENTIALS_FILE = _cfg.CREDENTIALS_FILE

# Module-level mutable state (mirrors bot.py globals, updated by key commands)
TTS_PROVIDER = _cfg.TTS_PROVIDER
STT_PROVIDER = _cfg.STT_PROVIDER
OPENAI_TTS_MODEL = _cfg.OPENAI_TTS_MODEL
OPENAI_STT_MODEL = _cfg.OPENAI_STT_MODEL
OPENAI_VOICE_ID = _cfg.OPENAI_VOICE_ID
ELEVENLABS_VOICE_ID = _cfg.ELEVENLABS_VOICE_ID


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
    fd = os.open(str(CREDENTIALS_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(creds, f, indent=2)


def apply_saved_credentials():
    """Apply saved credentials on startup."""
    global TTS_PROVIDER, STT_PROVIDER
    creds = load_credentials()

    if creds.get("claude_token"):
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = creds["claude_token"]
        logger.debug("Applied saved Claude token")

    elevenlabs_key = None
    openai_key = None

    if creds.get("elevenlabs_key"):
        os.environ["ELEVENLABS_API_KEY"] = creds["elevenlabs_key"]
        elevenlabs_key = creds["elevenlabs_key"]
        logger.debug("Applied saved ElevenLabs key")

    if creds.get("openai_key"):
        os.environ["OPENAI_API_KEY"] = creds["openai_key"]
        openai_key = creds["openai_key"]
        logger.debug("Applied saved OpenAI key")

    # Re-resolve providers after credentials are loaded
    TTS_PROVIDER = _cfg.resolve_provider("TTS_PROVIDER")
    STT_PROVIDER = _cfg.resolve_provider("STT_PROVIDER")

    # Sync voice_service clients (single source of truth for TTS/STT)
    voice_service.reconfigure(
        elevenlabs_key=elevenlabs_key,
        openai_key=openai_key,
        tts_provider=TTS_PROVIDER,
        stt_provider=STT_PROVIDER,
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
        f"*API Credentials*\n\n"
        f"Claude: {claude_status}\n"
        f"ElevenLabs: {elevenlabs_status}\n"
        f"OpenAI: {openai_status}\n\n"
        f"*Active providers:*\n"
        f"TTS: `{TTS_PROVIDER}`"
        + (f" ({OPENAI_TTS_MODEL} / {OPENAI_VOICE_ID})" if TTS_PROVIDER == "openai" else f" ({ELEVENLABS_VOICE_ID[:8]}...)" if TTS_PROVIDER == "elevenlabs" else "") + "\n"
        f"STT: `{STT_PROVIDER}`"
        + (f" ({OPENAI_STT_MODEL})" if STT_PROVIDER == "openai" else " (scribe_v1)" if STT_PROVIDER == "elevenlabs" else "") + "\n\n"
        f"*To configure:*\n"
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
        logger.warning(f"Could not delete token message: {e}")
        await update.effective_chat.send_message(
            "⚠️ Could not delete your message. Token NOT saved. Delete the message, rotate the token, and try again.",
            message_thread_id=thread_id
        )
        return

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

    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_admin(update):
        return

    # Delete the message immediately (contains sensitive key)
    thread_id = update.message.message_thread_id
    try:
        await update.message.delete()
    except Exception as e:
        logger.warning(f"Could not delete key message: {e}")
        await update.effective_chat.send_message(
            "⚠️ Could not delete your message. Key NOT saved. Delete the message, rotate the key, and try again.",
            message_thread_id=thread_id
        )
        return

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
    os.environ["ELEVENLABS_API_KEY"] = key
    voice_service.reconfigure(elevenlabs_key=key, tts_provider=TTS_PROVIDER)

    await update.effective_chat.send_message(
        "✓ ElevenLabs API key saved and applied!",
        message_thread_id=thread_id
    )


async def cmd_openai_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /openai_key command - set OpenAI API key."""
    global TTS_PROVIDER, STT_PROVIDER

    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_admin(update):
        return

    # Delete the message immediately (contains sensitive key)
    thread_id = update.message.message_thread_id
    try:
        await update.message.delete()
    except Exception as e:
        logger.warning(f"Could not delete key message: {e}")
        await update.effective_chat.send_message(
            "⚠️ Could not delete your message. Key NOT saved. Delete the message, rotate the key, and try again.",
            message_thread_id=thread_id
        )
        return

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
    TTS_PROVIDER = _cfg.resolve_provider("TTS_PROVIDER")
    STT_PROVIDER = _cfg.resolve_provider("STT_PROVIDER")
    voice_service.reconfigure(openai_key=key, tts_provider=TTS_PROVIDER, stt_provider=STT_PROVIDER)

    await update.effective_chat.send_message(
        f"✓ OpenAI API key saved and applied!\n"
        f"TTS: `{TTS_PROVIDER}` | STT: `{STT_PROVIDER}`",
        message_thread_id=thread_id,
        parse_mode="Markdown"
    )


def build_settings_menu(settings: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Build settings message text and inline keyboard from user settings dict."""
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

    return message, InlineKeyboardMarkup(keyboard)


async def handle_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle settings button callbacks."""
    query = update.callback_query
    logger.debug(f"SETTINGS CALLBACK received: {query.data} from user {update.effective_user.id}")

    if not _is_authorized(update):
        await query.answer()
        return

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
    message, reply_markup = build_settings_menu(settings)

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

    if callback_data.startswith("approve_"):
        approval_id = callback_data.replace("approve_", "")
        logger.debug(f">>> Looking for approval_id: {approval_id} in {list(_shared.pending_approvals.keys())}")
        if approval_id in _shared.pending_approvals:
            # Verify that the user clicking is the one who requested
            if update.effective_user.id != _shared.pending_approvals[approval_id].get("user_id"):
                await query.answer("Only the requester can approve this")
                return

            await query.answer()
            tool_name = _shared.pending_approvals[approval_id]["tool_name"]
            _shared.pending_approvals[approval_id]["approved"] = True
            logger.debug(f">>> Setting event for {approval_id}")
            _shared.pending_approvals[approval_id]["event"].set()
            logger.debug(f">>> Event set, updating message")
            await query.edit_message_text(f"✓ Approved: {tool_name}")
        else:
            logger.debug(f">>> Approval {approval_id} not found (expired)")
            await query.answer()
            await query.edit_message_text("Approval expired")

    elif callback_data.startswith("reject_"):
        approval_id = callback_data.replace("reject_", "")
        logger.debug(f">>> Looking for approval_id: {approval_id} in {list(_shared.pending_approvals.keys())}")
        if approval_id in _shared.pending_approvals:
            # Verify that the user clicking is the one who requested
            if update.effective_user.id != _shared.pending_approvals[approval_id].get("user_id"):
                await query.answer("Only the requester can reject this")
                return

            await query.answer()
            tool_name = _shared.pending_approvals[approval_id]["tool_name"]
            _shared.pending_approvals[approval_id]["approved"] = False
            logger.debug(f">>> Setting event for {approval_id} (reject)")
            _shared.pending_approvals[approval_id]["event"].set()
            logger.debug(f">>> Event set, updating message")
            await query.edit_message_text(f"✗ Rejected: {tool_name}")
        else:
            logger.debug(f">>> Approval {approval_id} not found (expired)")
            await query.answer()
            await query.edit_message_text("Approval expired")
