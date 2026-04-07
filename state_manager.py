"""
Thread-safe user session and settings state management.

Wraps the global dicts from bot.py with per-user asyncio.Lock and atomic file persistence.
Use get_manager() to access the singleton after bot.py initializes it.
"""
import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_USER_SETTINGS: dict = {
    "audio_enabled": True,
    "voice_speed": 1.1,
    "mode": "go_all",
    "watch_mode": "off",
    "automation_card_style": "full",
}

_instance: "StateManager | None" = None


def get_manager() -> "StateManager":
    """Return the initialized StateManager singleton. Raises if not initialized yet."""
    if _instance is None:
        raise RuntimeError("StateManager not initialized — call StateManager.init() first")
    return _instance


class StateManager:
    """Manages user sessions and settings with per-user locking and atomic persistence."""

    def __init__(self, state_file: Path, settings_file: Path):
        self.state_file = state_file
        self.settings_file = settings_file
        self._sessions: dict[str, Any] = {}
        self._settings: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_mutex = threading.Lock()

    @classmethod
    def init(cls, state_file: Path, settings_file: Path) -> "StateManager":
        """Create and register the singleton. Call once at startup."""
        global _instance
        _instance = cls(state_file, settings_file)
        return _instance

    # ── Locking ───────────────────────────────────────────────────────────────

    def get_lock(self, user_id: str | int) -> asyncio.Lock:
        """Return asyncio.Lock for user_id — creates one if needed. Thread-safe."""
        key = str(user_id)
        with self._locks_mutex:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    # ── Persistence ───────────────────────────────────────────────────────────

    def load(self):
        """Load state and settings from disk. Resets to empty on corruption."""
        self._sessions = self._load_json(self.state_file)
        self._settings = self._load_json(self.settings_file)

    def _load_json(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load {path} (resetting): {e}")
            return {}

    def _save_json(self, path: Path, data: dict):
        """Write atomically: .tmp then rename."""
        tmp = Path(str(path) + ".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(path)
        except OSError as e:
            logger.error(f"Failed to save {path}: {e}")
            tmp.unlink(missing_ok=True)

    def save_state(self):
        self._save_json(self.state_file, self._sessions)

    def save_settings(self):
        self._save_json(self.settings_file, self._settings)

    # ── Session state ─────────────────────────────────────────────────────────

    def get_user_state(self, user_id: str | int) -> dict:
        """Return user session state dict, creating default if missing."""
        key = str(user_id)
        if key not in self._sessions:
            self._sessions[key] = {"current_session": None, "sessions": []}
        return self._sessions[key]

    def all_sessions(self) -> dict:
        return self._sessions

    # ── User settings ─────────────────────────────────────────────────────────

    def get_user_settings(self, user_id: str | int) -> dict:
        """Return user settings dict with defaults applied and legacy fields migrated."""
        key = str(user_id)
        if key not in self._settings:
            self._settings[key] = dict(DEFAULT_USER_SETTINGS)
            return self._settings[key]

        s = self._settings[key]

        # Migrate watch_enabled / show_activity → watch_mode (legacy field names)
        # Run before defaults so legacy keys take precedence over the default "off".
        if "watch_mode" not in s:
            if s.get("watch_enabled"):
                s["watch_mode"] = "live"
            elif s.get("show_activity"):
                s["watch_mode"] = "debug"
            else:
                s["watch_mode"] = "off"
        s.pop("watch_enabled", None)
        s.pop("show_activity", None)

        # Apply any missing defaults
        for field, default in DEFAULT_USER_SETTINGS.items():
            if field not in s:
                s[field] = default

        return s

    def all_settings(self) -> dict:
        return self._settings
