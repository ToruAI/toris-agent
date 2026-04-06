"""
Claude service module — prompt building, SDK integration, and working indicator.

Extracted from bot.py. All Claude-specific logic lives here.
Call configure() once in main() before run_polling() to inject shared state dicts.
"""
import asyncio
import json
import logging
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from claude_agent_sdk import (
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

import config as _cfg

logger = logging.getLogger(__name__)

# ── Shared state injected via configure() ─────────────────────────────────────

_pending_approvals: dict = {}
_cancel_events: dict = {}


def configure(pending_approvals: dict, cancel_events: dict):
    """Inject shared state dicts from bot.py. Call once in main() before run_polling()."""
    global _pending_approvals, _cancel_events
    _pending_approvals = pending_approvals
    _cancel_events = cancel_events


# ── System prompt ──────────────────────────────────────────────────────────────

def load_system_prompt() -> str:
    """Load system prompt from file or use default."""
    if _cfg.SYSTEM_PROMPT_FILE:
        prompt_path = Path(_cfg.SYSTEM_PROMPT_FILE)
        # If relative, look relative to this file
        if not prompt_path.is_absolute():
            prompt_path = Path(__file__).parent / prompt_path
        if prompt_path.exists():
            content = prompt_path.read_text()
            # Replace placeholders
            content = content.replace("{sandbox_dir}", _cfg.SANDBOX_DIR)
            content = content.replace("{read_dir}", _cfg.CLAUDE_WORKING_DIR)
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
- You can READ files from anywhere in {_cfg.CLAUDE_WORKING_DIR}
- You can WRITE and EXECUTE only in {_cfg.SANDBOX_DIR}
- You have WebSearch for current information

Remember: You're being heard, not read. Speak naturally."""


# Base system prompt loaded once at module import
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


# ── Megg context ───────────────────────────────────────────────────────────────

async def load_megg_context() -> str:
    """Load megg context like the hook does. Runs subprocess in thread — non-blocking."""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["megg", "context"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=_cfg.CLAUDE_WORKING_DIR,
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


# ── Tool formatting ────────────────────────────────────────────────────────────

def format_tool_call(tool_name: str, tool_input: dict) -> str:
    """Format a tool call for display in Telegram."""
    # Truncate long inputs
    input_str = json.dumps(tool_input, indent=2)
    if len(input_str) > 500:
        input_str = input_str[:500] + "..."
    return f"Tool: {tool_name}\n```\n{input_str}\n```"


# ── SDK options builder ────────────────────────────────────────────────────────

def build_claude_options(system_prompt: str, mode: str, can_use_tool=None) -> ClaudeAgentOptions:
    """Build ClaudeAgentOptions from mode and system prompt. Always includes CLAUDE_SETTINGS_FILE."""
    if mode == "approve":
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            cwd=_cfg.SANDBOX_DIR,
            can_use_tool=can_use_tool,
            permission_mode="default",
            add_dirs=[_cfg.CLAUDE_WORKING_DIR],
        )
    else:
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=["Read", "Grep", "Glob", "WebSearch", "WebFetch", "Task", "Bash", "Edit", "Write", "Skill", "RemoteTrigger"],
            cwd=_cfg.SANDBOX_DIR,
            add_dirs=[_cfg.CLAUDE_WORKING_DIR],
        )
    if _cfg.CLAUDE_SETTINGS_FILE:
        options.settings = _cfg.CLAUDE_SETTINGS_FILE
    return options


# ── Main Claude call ───────────────────────────────────────────────────────────

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
    Path(_cfg.SANDBOX_DIR).mkdir(parents=True, exist_ok=True)

    # Load megg context for new sessions
    full_prompt = prompt
    if include_megg and not continue_last and not session_id:
        megg_ctx = await load_megg_context()
        if megg_ctx:
            full_prompt = f"<context>\n{megg_ctx}\n</context>\n\n{prompt}"
            logger.debug("Prepended megg context to prompt")

    # Build dynamic system prompt
    dynamic_persona = build_dynamic_prompt(user_settings)

    logger.debug(f"Calling Claude SDK: prompt={len(prompt)} chars, continue={continue_last}, session={session_id[:8] if session_id else 'new'}...")
    logger.debug(f"Mode: {mode}, Watch: {watch_mode}")
    logger.debug(f"Working dir: {_cfg.SANDBOX_DIR} (sandbox)")

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
        current_approval_id = str(uuid.uuid4())[:8]
        approval_event = asyncio.Event()

        # Store pending approval with requesting user_id
        _pending_approvals[current_approval_id] = {
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

        logger.debug(f">>> Waiting for approval: {current_approval_id} ({tool_name}) - _pending_approvals keys: {list(_pending_approvals.keys())}")

        # Wait for user response (with timeout)
        try:
            logger.debug(f">>> Starting event.wait() for {current_approval_id}")
            await asyncio.wait_for(approval_event.wait(), timeout=300)  # 5 min timeout
            logger.debug(f">>> Event.wait() completed for {current_approval_id}")
        except asyncio.TimeoutError:
            logger.debug(f">>> Approval timeout for {current_approval_id}")
            del _pending_approvals[current_approval_id]
            return PermissionResultDeny(message="Approval timed out")

        # Check result
        logger.debug(f">>> Checking result for {current_approval_id}")
        approval_data = _pending_approvals.pop(current_approval_id, {})
        if approval_data.get("approved"):
            logger.debug(f">>> Tool approved: {tool_name}")
            return PermissionResultAllow()
        else:
            logger.debug(f">>> Tool rejected: {tool_name}")
            return PermissionResultDeny(message="User rejected tool")

    # Build SDK options
    options = build_claude_options(dynamic_persona, mode, can_use_tool)
    logger.debug(f">>> Options built: mode={mode}, settings={bool(_cfg.CLAUDE_SETTINGS_FILE)}, can_use_tool={options.can_use_tool is not None}")

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

    # debug_msg created lazily on first tool use (debug mode only)
    debug_msg = None

    # Set up cancellation tracking for this user
    user_id_for_cancel = update.effective_user.id if update else None
    if user_id_for_cancel is not None:
        if user_id_for_cancel not in _cancel_events:
            _cancel_events[user_id_for_cancel] = asyncio.Event()
        _cancel_events[user_id_for_cancel].clear()  # Reset at start of each call

    async def _run_claude():
        nonlocal result_text, new_session_id, tool_count, tool_log, debug_msg
        logger.debug(f">>> Starting ClaudeSDKClient with prompt: {len(full_prompt)} chars")
        async with ClaudeSDKClient(options=options) as client:
            await client.query(full_prompt)
            async for message in client.receive_response():
                # Check for user cancellation
                if user_id_for_cancel is not None and _cancel_events.get(user_id_for_cancel, asyncio.Event()).is_set():
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
                                    await processing_msg.edit_text("Toris thinking...\n" + "\n".join(tool_log))
                                except Exception:
                                    pass
                            elif watch_mode == "debug" and update:
                                try:
                                    if debug_msg is None:
                                        debug_msg = await update.message.reply_text("🔧 Tools:\n" + "\n".join(tool_log))
                                    else:
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

    try:
        await asyncio.wait_for(_run_claude(), timeout=_cfg.CLAUDE_TIMEOUT)
        return result_text, new_session_id, metadata
    except asyncio.TimeoutError:
        logger.error(f"Claude call timed out after {_cfg.CLAUDE_TIMEOUT}s")
        return f"⏱️ Toris timed out after {_cfg.CLAUDE_TIMEOUT}s. Try a simpler request or /cancel.", session_id, {}
    except Exception as e:
        logger.error(f"Claude SDK error: {e}")
        return f"Error calling Claude: {e}", session_id, {}


# ============ Working Indicator ============

class WorkingIndicator:
    """Sends periodic 'still working' status updates during long Claude calls.

    Usage:
        indicator = WorkingIndicator(edit_fn=processing_msg.edit_text, interval=5.0)
        indicator.start()
        try:
            result = await long_operation()
        finally:
            indicator.stop()
    """

    MESSAGES = [
        "⏳ Toris is thinking...",
        "⏳ Still working...",
        "⏳ Running tools...",
        "⏳ Almost there...",
    ]

    def __init__(self, edit_fn, interval: float = 5.0):
        self._edit_fn = edit_fn
        self._interval = interval
        self._task: asyncio.Task | None = None
        self._count = 0

    async def _loop(self):
        while True:
            await asyncio.sleep(self._interval)
            msg = self.MESSAGES[self._count % len(self.MESSAGES)]
            self._count += 1
            try:
                await self._edit_fn(msg)
            except Exception:
                pass  # Status update failure must never crash the main call

    def start(self):
        """Start the background status update task."""
        self._task = asyncio.create_task(self._loop())

    def stop(self):
        """Stop and cancel the background task."""
        if self._task:
            self._task.cancel()
            self._task = None
