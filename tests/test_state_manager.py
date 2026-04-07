"""Tests for state_manager.py."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from state_manager import StateManager


@pytest.fixture
def sm(tmp_path):
    return StateManager(tmp_path / "state.json", tmp_path / "settings.json")


class TestStateManagerLoad:
    def test_load_empty_when_no_files(self, sm):
        sm.load()
        assert sm._sessions == {}
        assert sm._settings == {}

    def test_load_handles_corrupt_state_json(self, tmp_path):
        state_f = tmp_path / "state.json"
        state_f.write_text("{bad json[")
        sm = StateManager(state_f, tmp_path / "settings.json")
        sm.load()
        assert sm._sessions == {}

    def test_load_handles_corrupt_settings_json(self, tmp_path):
        settings_f = tmp_path / "settings.json"
        settings_f.write_text("not json")
        sm = StateManager(tmp_path / "state.json", settings_f)
        sm.load()
        assert sm._settings == {}

    def test_load_reads_existing_data(self, tmp_path):
        state_f = tmp_path / "state.json"
        state_f.write_text(json.dumps({"u1": {"current_session": "abc", "sessions": ["abc"]}}))
        sm = StateManager(state_f, tmp_path / "settings.json")
        sm.load()
        assert sm._sessions["u1"]["current_session"] == "abc"


class TestStateManagerSave:
    def test_save_state_creates_file(self, sm, tmp_path):
        sm._sessions = {"u1": {"current_session": "x", "sessions": ["x"]}}
        sm.save_state()
        assert sm.state_file.exists()

    def test_save_state_atomic_no_tmp_left(self, sm):
        sm._sessions = {"u1": {"current_session": "x", "sessions": []}}
        sm.save_state()
        assert not Path(str(sm.state_file) + ".tmp").exists()

    def test_save_state_roundtrip(self, sm):
        sm._sessions = {"u1": {"current_session": "abc", "sessions": ["abc"]}}
        sm.save_state()
        data = json.loads(sm.state_file.read_text())
        assert data["u1"]["current_session"] == "abc"

    def test_save_settings_roundtrip(self, sm):
        sm._settings = {"u1": {"audio_enabled": False, "voice_speed": 1.2}}
        sm.save_settings()
        data = json.loads(sm.settings_file.read_text())
        assert data["u1"]["audio_enabled"] is False


class TestStateManagerGetters:
    def test_get_user_state_creates_default(self, sm):
        state = sm.get_user_state("new_user")
        assert state["current_session"] is None
        assert state["sessions"] == []

    def test_get_user_state_same_object(self, sm):
        s1 = sm.get_user_state("u1")
        s2 = sm.get_user_state("u1")
        assert s1 is s2

    def test_get_user_settings_creates_defaults(self, sm):
        settings = sm.get_user_settings("new_user")
        assert "audio_enabled" in settings
        assert "mode" in settings

    def test_int_and_str_user_id_equivalent(self, sm):
        sm.get_user_state(123)
        assert "123" in sm._sessions


class TestStateManagerLocking:
    def test_get_lock_returns_asyncio_lock(self, sm):
        lock = sm.get_lock("u1")
        assert isinstance(lock, asyncio.Lock)

    def test_same_user_same_lock(self, sm):
        lock1 = sm.get_lock("u1")
        lock2 = sm.get_lock("u1")
        assert lock1 is lock2

    def test_different_users_different_locks(self, sm):
        lock1 = sm.get_lock("u1")
        lock2 = sm.get_lock("u2")
        assert lock1 is not lock2

    def test_int_str_user_id_same_lock(self, sm):
        lock1 = sm.get_lock(123)
        lock2 = sm.get_lock("123")
        assert lock1 is lock2


class TestStateManagerSingleton:
    def test_init_creates_singleton(self, tmp_path):
        from state_manager import StateManager
        sm = StateManager.init(tmp_path / "s.json", tmp_path / "set.json")
        from state_manager import get_manager
        assert get_manager() is sm

    def test_get_manager_raises_before_init(self):
        import state_manager
        state_manager._instance = None
        with pytest.raises(RuntimeError, match="not initialized"):
            state_manager.get_manager()


class TestStateManagerUserSettingsDefaults:
    def test_new_user_gets_defaults(self, sm):
        s = sm.get_user_settings("new_user")
        assert s["audio_enabled"] is True
        assert s["voice_speed"] == 1.1
        assert s["mode"] == "go_all"
        assert s["watch_mode"] == "off"

    def test_existing_user_without_mode_gets_default(self, sm):
        sm._settings["u1"] = {"audio_enabled": False, "voice_speed": 1.0}
        s = sm.get_user_settings("u1")
        assert s["mode"] == "go_all"
        assert s["watch_mode"] == "off"

    def test_migration_watch_enabled_true(self, sm):
        sm._settings["u1"] = {"watch_enabled": True}
        s = sm.get_user_settings("u1")
        assert s["watch_mode"] == "live"
        assert "watch_enabled" not in s

    def test_migration_show_activity_true(self, sm):
        sm._settings["u1"] = {"show_activity": True}
        s = sm.get_user_settings("u1")
        assert s["watch_mode"] == "debug"
        assert "show_activity" not in s

    def test_migration_neither_flag_gives_off(self, sm):
        sm._settings["u1"] = {"watch_enabled": False, "show_activity": False}
        s = sm.get_user_settings("u1")
        assert s["watch_mode"] == "off"
        assert "watch_enabled" not in s
        assert "show_activity" not in s
