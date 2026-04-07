"""Tests for load_credentials and save_credentials in handlers/admin.py."""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:token")
os.environ.setdefault("TELEGRAM_DEFAULT_CHAT_ID", "0")
os.environ.setdefault("ELEVENLABS_API_KEY", "dummy-key-for-tests")
sys.path.insert(0, str(Path(__file__).parent.parent))

import handlers.admin as admin_mod


@pytest.fixture(autouse=True)
def patch_credentials_file(monkeypatch, tmp_path):
    """Redirect CREDENTIALS_FILE to a tmp path for every test."""
    monkeypatch.setattr(admin_mod, "CREDENTIALS_FILE", tmp_path / "creds.json")


class TestLoadCredentials:
    def test_returns_empty_when_file_missing(self):
        result = admin_mod.load_credentials()
        assert result == {}

    def test_reads_existing_credentials(self, tmp_path):
        creds_file = tmp_path / "creds.json"
        creds_file.write_text(json.dumps({"claude_token": "sk-ant-test123"}))
        admin_mod.CREDENTIALS_FILE = creds_file
        result = admin_mod.load_credentials()
        assert result["claude_token"] == "sk-ant-test123"

    def test_returns_empty_on_corrupt_json(self, tmp_path):
        creds_file = tmp_path / "creds.json"
        creds_file.write_text("{bad json[[[")
        admin_mod.CREDENTIALS_FILE = creds_file
        result = admin_mod.load_credentials()
        assert result == {}

    def test_returns_empty_on_io_error(self, tmp_path):
        """Non-JSON content (IOError path) also returns empty dict."""
        creds_file = tmp_path / "creds.json"
        creds_file.write_text("")
        admin_mod.CREDENTIALS_FILE = creds_file
        result = admin_mod.load_credentials()
        assert result == {}


class TestSaveCredentials:
    def test_creates_file(self, tmp_path):
        admin_mod.save_credentials({"elevenlabs_key": "abc123"})
        assert admin_mod.CREDENTIALS_FILE.exists()

    def test_file_permissions_restricted(self, tmp_path):
        """Saved file must be owner-read/write only (0o600)."""
        admin_mod.save_credentials({"openai_key": "sk-test"})
        mode = stat.S_IMODE(admin_mod.CREDENTIALS_FILE.stat().st_mode)
        assert mode == 0o600

    def test_roundtrip(self, tmp_path):
        payload = {"claude_token": "sk-ant-xyz", "elevenlabs_key": "el-key"}
        admin_mod.save_credentials(payload)
        loaded = json.loads(admin_mod.CREDENTIALS_FILE.read_text())
        assert loaded == payload

    def test_overwrites_existing(self, tmp_path):
        admin_mod.save_credentials({"claude_token": "old-token"})
        admin_mod.save_credentials({"claude_token": "new-token"})
        loaded = json.loads(admin_mod.CREDENTIALS_FILE.read_text())
        assert loaded["claude_token"] == "new-token"
