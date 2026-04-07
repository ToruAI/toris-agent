"""
Message handlers: voice, text, photo, automations callback.

These orchestrate the full request pipeline:
transcription → Claude → TTS → state update → reply
"""
import asyncio
import logging
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

import config as _cfg
from auth import should_handle_message, _is_authorized, check_rate_limit
from automations import (
    build_automation_card, build_automations_list,
    run_remote_trigger_list, run_remote_trigger_run, run_remote_trigger_toggle,
)
from claude_service import WorkingIndicator, call_claude
from state_manager import get_manager
from voice_service import format_tts_fallback, is_valid_transcription, text_to_speech, transcribe_voice

logger = logging.getLogger(__name__)

TOPIC_ID = _cfg.TOPIC_ID
SANDBOX_DIR = _cfg.SANDBOX_DIR
MAX_VOICE_CHARS = _cfg.MAX_VOICE_CHARS


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


def _update_session_state(state: dict, new_session_id: str, mgr) -> None:
    """Persist a new session_id into user state."""
    state["current_session"] = new_session_id
    name = state.pop("pending_session_name", None)
    state.setdefault("session_names", {})[new_session_id] = name
    if new_session_id not in state["sessions"]:
        state["sessions"].append(new_session_id)
    mgr.save_state()


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
    asyncio.create_task(typing_loop(update, context, typing_stop))
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
                _update_session_state(state, new_session_id, get_manager())

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
    asyncio.create_task(typing_loop(update, context, typing_stop))
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
                _update_session_state(state, new_session_id, get_manager())

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
    asyncio.create_task(typing_loop(update, context, typing_stop))
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
                _update_session_state(state, new_session_id, get_manager())

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
