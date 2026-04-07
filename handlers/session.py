"""
Session command handlers.

Commands: /start /new /cancel /compact /continue /sessions /switch /status /search
"""
import asyncio
import json
import logging
import subprocess
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

import config as _cfg
import shared_state as _shared
from auth import should_handle_message, _is_authorized
from claude_service import call_claude
from state_manager import get_manager

logger = logging.getLogger(__name__)


def _extract_session_messages(session_id: str, max_msgs: int = 6) -> list:
    """Return up to max_msgs user message texts from a session JSONL file."""
    hashed = _cfg.SANDBOX_DIR.replace("/", "-")
    jsonl_path = Path.home() / ".claude" / "projects" / hashed / f"{session_id}.jsonl"
    if not jsonl_path.exists():
        return []
    messages = []
    try:
        with open(jsonl_path) as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message", {})
                if msg.get("role") != "user":
                    continue
                content = msg.get("content", "")
                text = None
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            text = c["text"]
                            break
                elif isinstance(content, str):
                    text = content
                if text and text.strip():
                    messages.append(text[:250])
                    if len(messages) >= max_msgs:
                        break
    except Exception:
        pass
    return messages


def _get_session_first_prompt(session_id: str) -> "str | None":
    """Return the first user message text from a Claude session JSONL file."""
    msgs = _extract_session_messages(session_id, max_msgs=1)
    return msgs[0] if msgs else None


def parse_session_name(args: list) -> "str | None":
    """Return joined args as a session name, or None if empty."""
    return " ".join(args).strip() or None


def format_sessions_list(sessions: list) -> str:
    """Format a list of session dicts for display in Telegram."""
    if not sessions:
        return "No sessions yet."
    lines = []
    for i, s in enumerate(sessions, 1):
        sid = s.get("id", "")[:8]
        name = s.get("name") or "(unnamed)"
        lines.append(f"{i}. `{sid}` — {name}")
    return "\n".join(lines)


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
        "/settings - Configure audio and voice speed\n"
        "/health - Check Claude, STT, TTS status"
    )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /new command - start new session."""
    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_authorized(update):
        return

    user_id = update.effective_user.id
    state = get_manager().get_user_state(user_id)

    session_name = parse_session_name(context.args or [])
    state["current_session"] = None  # Will be set on first message
    state["pending_session_name"] = session_name

    if session_name:
        await update.message.reply_text(f"✅ Starting new session: *{session_name}*", parse_mode="Markdown")
    else:
        await update.message.reply_text("✅ Starting new session.")

    get_manager().save_state()


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cancel command — interrupt active Claude request."""
    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_authorized(update):
        return

    user_id = update.effective_user.id
    event = _shared.cancel_events.get(user_id)
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
    state = get_manager().get_user_state(user_id)
    settings = get_manager().get_user_settings(user_id)

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
        get_manager().save_state()

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
    state = get_manager().get_user_state(user_id)

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
    state = get_manager().get_user_state(user_id)

    names = state.get("session_names", {})
    sessions_data = [
        {"id": sid, "name": names.get(sid)}
        for sid in state.get("sessions", [])[-10:]
    ]
    text = format_sessions_list(sessions_data)
    current_id = state.get("current_session")
    if current_id:
        current_short = current_id[:8]
        text += f"\n\nCurrent: `{current_short}`"
    await update.message.reply_text(f"Sessions:\n{text}", parse_mode="Markdown")


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
    state = get_manager().get_user_state(user_id)
    session_id = context.args[0]

    # Find matching session
    matches = [s for s in state["sessions"] if s.startswith(session_id)]

    if len(matches) == 1:
        state["current_session"] = matches[0]
        get_manager().save_state()
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
    state = get_manager().get_user_state(user_id)

    if state["current_session"]:
        names = state.get("session_names", {})
        name = names.get(state["current_session"])
        name_part = f" — {name}" if name else ""
        await update.message.reply_text(
            f"Session: `{state['current_session'][:8]}`{name_part}\n"
            f"Total: {len(state['sessions'])}",
            parse_mode="Markdown"
        )
    elif "pending_session_name" in state:
        name = state["pending_session_name"]
        if name:
            await update.message.reply_text(f"New session pending: *{name}* — send a message to start.", parse_mode="Markdown")
        else:
            await update.message.reply_text("New session pending — send a message to start.")
    else:
        await update.message.reply_text("No active session. Use /new to start one.")


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /search command - find sessions by keyword in name or first message."""
    if not should_handle_message(update.message.message_thread_id):
        return

    if not _is_authorized(update):
        return

    if not context.args:
        await update.message.reply_text("Usage: /search <query>")
        return

    query = " ".join(context.args).strip()
    user_id = update.effective_user.id
    state = get_manager().get_user_state(user_id)

    sessions = state.get("sessions", [])
    names = state.get("session_names", {})

    if not sessions:
        await update.message.reply_text("No sessions yet.")
        return

    searching_msg = await update.message.reply_text("🔍 Searching sessions...")

    # Extract user messages from each session (Python-side, fast)
    session_data = []
    for sid in reversed(sessions[-30:]):
        msgs = _extract_session_messages(sid, max_msgs=6)
        session_data.append({
            "id": sid,
            "name": names.get(sid) or "",
            "messages": msgs,
        })

    sessions_json = json.dumps(session_data, ensure_ascii=False)
    prompt = (
        f'Find the most relevant sessions for this query: "{query}"\n\n'
        f"Sessions (id + name + user messages sample):\n{sessions_json}\n\n"
        "Return ONLY a JSON array of session IDs, most relevant first, max 5. "
        'If nothing relevant return []. Example: ["full-uuid-1", "full-uuid-2"]'
    )

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["claude", "-p", prompt, "--output-format", "json"],
            capture_output=True, text=True, timeout=45,
            cwd=_cfg.CLAUDE_WORKING_DIR,
        )
        if result.returncode != 0:
            await searching_msg.edit_text(f"Search failed: {result.stderr[:100]}")
            return

        outer = json.loads(result.stdout)
        raw_result = outer.get("result", "")
        start = raw_result.find("[")
        end = raw_result.rfind("]") + 1
        if start == -1 or end == 0:
            await searching_msg.edit_text("No sessions found.")
            return
        matched_ids = json.loads(raw_result[start:end])
    except Exception as e:
        logger.error(f"cmd_search error: {e}")
        await searching_msg.edit_text(f"Search error: {e}")
        return

    if not matched_ids:
        await searching_msg.edit_text(f"No sessions matching: {query}")
        return

    # Fetch first prompts for display (already have them cached in _get_session_first_prompt)
    lines = [f"Sessions matching *{query}*:\n"]
    for sid in matched_ids:
        if sid not in sessions:
            continue
        short = sid[:8]
        name = names.get(sid) or ""
        first_prompt = _get_session_first_prompt(sid) or ""
        excerpt = first_prompt[:120].replace("\n", " ")
        if len(first_prompt) > 120:
            excerpt += "..."
        name_part = f" — {name}" if name else ""
        lines.append(f"`{short}`{name_part}")
        if excerpt:
            lines.append(f"_{excerpt}_")
        lines.append(f"→ /switch {short}\n")

    await searching_msg.edit_text("\n".join(lines), parse_mode="Markdown")
