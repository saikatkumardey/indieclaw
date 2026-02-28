"""Config tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _patch_workspace(tmp_path, monkeypatch):
    import indieclaw.workspace as ws
    monkeypatch.setattr(ws, "HOME", tmp_path)
    monkeypatch.setattr(ws, "CONFIG", tmp_path / "indieclaw.json")
    monkeypatch.setattr(ws, "SESSION_STATE", tmp_path / "session_state.json")


# --- Config tests ---


def test_load_no_file_returns_defaults(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    from indieclaw.config import Config
    cfg = Config.load()
    assert cfg.get("model") == "claude-sonnet-4-6"
    assert cfg.get("max_turns") == 10


def test_load_existing_file_merges(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    (tmp_path / "indieclaw.json").write_text(json.dumps({"max_turns": 20}))
    from indieclaw.config import Config
    cfg = Config.load()
    assert cfg.get("max_turns") == 20
    assert cfg.get("model") == "claude-sonnet-4-6"  # filled from defaults


def test_set_validates_and_persists(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    from indieclaw.config import Config
    cfg = Config.load()
    cfg.set("max_turns", 25)
    assert cfg.get("max_turns") == 25
    # Verify persisted to disk
    on_disk = json.loads((tmp_path / "indieclaw.json").read_text())
    assert on_disk["max_turns"] == 25


def test_set_rejects_unknown_key(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    from indieclaw.config import Config
    cfg = Config.load()
    with pytest.raises(KeyError, match="Unknown config key"):
        cfg.set("nonexistent", 42)


def test_set_rejects_wrong_type(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    from indieclaw.config import Config
    cfg = Config.load()
    with pytest.raises(TypeError, match="must be int"):
        cfg.set("max_turns", "not a number")


def test_migration_from_env_var(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("INDIECLAW_MODEL", "claude-opus-4-6")
    from indieclaw.config import Config
    cfg = Config.load()
    assert cfg.get("model") == "claude-opus-4-6"


def test_file_takes_priority_over_env(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    (tmp_path / "indieclaw.json").write_text(json.dumps({"model": "claude-haiku-4-5-20251001"}))
    monkeypatch.setenv("INDIECLAW_MODEL", "claude-opus-4-6")
    from indieclaw.config import Config
    cfg = Config.load()
    assert cfg.get("model") == "claude-haiku-4-5-20251001"


def test_to_dict(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    from indieclaw.config import Config
    cfg = Config.load()
    d = cfg.to_dict()
    assert isinstance(d, dict)
    assert set(d.keys()) == {"model", "effort", "max_turns", "btw_model", "cron_model", "subconscious_enabled", "subconscious_interval_hours", "subconscious_model", "streaming", "debounce_seconds", "max_budget_usd", "agent_stall_timeout", "agent_initial_timeout"}


def test_atomic_write_creates_file(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    from indieclaw.config import Config
    cfg = Config.load()
    cfg.set("max_turns", 5)
    # .tmp should not linger
    assert not (tmp_path / "indieclaw.tmp").exists()
    assert (tmp_path / "indieclaw.json").exists()


# --- SessionState tests ---


def test_session_state_load_empty(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    from indieclaw.session_state import SessionState
    ss = SessionState.load()
    assert ss.get_usage_today()["turns"] == 0
    assert ss.get_usage_today()["input_tokens"] == 0


def test_record_turn_updates_session(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    from indieclaw.session_state import SessionState
    ss = SessionState.load()
    result = MagicMock()
    result.usage = {
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_input_tokens": 500,
        "cache_creation_input_tokens": 100,
    }
    result.num_turns = 3
    ss.record_turn("chat123", result)

    sess = ss.get_session("chat123")
    assert sess["input_tokens"] == 1000
    assert sess["output_tokens"] == 200
    assert sess["cache_read_tokens"] == 500
    assert sess["cache_write_tokens"] == 100
    assert sess["turns"] == 3


def test_record_turn_accumulates(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    from indieclaw.session_state import SessionState
    ss = SessionState.load()
    result = MagicMock()
    result.usage = {"input_tokens": 100, "output_tokens": 50,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    result.num_turns = 1
    ss.record_turn("chat1", result)
    ss.record_turn("chat1", result)
    sess = ss.get_session("chat1")
    assert sess["input_tokens"] == 200
    assert sess["turns"] == 2


def test_daily_rollover(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    from indieclaw.session_state import SessionState
    ss = SessionState.load()
    # Simulate stale date
    ss._data["usage_today"] = {"date": "1999-01-01", "input_tokens": 9999, "output_tokens": 9999, "cache_read_tokens": 0, "cache_write_tokens": 0, "turns": 999}
    result = MagicMock()
    result.usage = {"input_tokens": 100, "output_tokens": 50,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    result.num_turns = 1
    ss.record_turn("chat1", result)
    usage = ss.get_usage_today()
    assert usage["turns"] == 1
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 50


def test_session_state_persists_to_disk(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    from indieclaw.session_state import SessionState
    ss = SessionState.load()
    result = MagicMock()
    result.usage = {"input_tokens": 100, "output_tokens": 50,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    result.num_turns = 1
    ss.record_turn("chat1", result)
    # Reload from disk
    ss2 = SessionState.load()
    assert ss2.get_session("chat1")["turns"] == 1


def test_get_session_missing(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    from indieclaw.session_state import SessionState
    ss = SessionState.load()
    assert ss.get_session("nonexistent") == {}
