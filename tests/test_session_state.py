"""SessionState tests — token recording, daily rollover, fresh load."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_result(input_tokens=100, output_tokens=50, turns=1):
    result = MagicMock()
    result.usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    result.num_turns = turns
    return result


def _patch_workspace(tmp_path, monkeypatch):
    import indieclaw.workspace as ws
    monkeypatch.setattr(ws, "SESSION_STATE", tmp_path / "session_state.json")


class TestRecordTurn:
    def test_accumulates_tokens(self, tmp_path, monkeypatch):
        _patch_workspace(tmp_path, monkeypatch)
        from indieclaw.session_state import SessionState
        state = SessionState()
        state.record_turn("chat1", _make_result(100, 50))
        state.record_turn("chat1", _make_result(200, 75))
        sess = state.get_session("chat1")
        assert sess["input_tokens"] == 300
        assert sess["output_tokens"] == 125
        assert sess["turns"] == 2

    def test_separate_chat_ids(self, tmp_path, monkeypatch):
        _patch_workspace(tmp_path, monkeypatch)
        from indieclaw.session_state import SessionState
        state = SessionState()
        state.record_turn("chat1", _make_result(100, 50))
        state.record_turn("chat2", _make_result(200, 75))
        assert state.get_session("chat1")["input_tokens"] == 100
        assert state.get_session("chat2")["input_tokens"] == 200


class TestDailyRollover:
    def test_new_day_resets_counters(self, tmp_path, monkeypatch):
        _patch_workspace(tmp_path, monkeypatch)
        from indieclaw.session_state import SessionState

        state = SessionState()
        # Record with today's date
        state.record_turn("chat1", _make_result(100, 50))
        usage = state.get_usage_today()
        assert usage["input_tokens"] == 100

        # Simulate a different day
        state._data["usage_today"]["date"] = "2020-01-01"
        state.record_turn("chat1", _make_result(50, 25))
        usage = state.get_usage_today()
        assert usage["input_tokens"] == 50  # reset, not 150

    def test_get_usage_today_returns_zero_on_stale_date(self, tmp_path, monkeypatch):
        _patch_workspace(tmp_path, monkeypatch)
        from indieclaw.session_state import SessionState
        state = SessionState()
        state._data["usage_today"]["date"] = "2020-01-01"
        state._data["usage_today"]["input_tokens"] = 999
        usage = state.get_usage_today()
        assert usage["input_tokens"] == 0  # stale date → zero


class TestLoad:
    def test_fresh_state_when_file_missing(self, tmp_path, monkeypatch):
        _patch_workspace(tmp_path, monkeypatch)
        from indieclaw.session_state import SessionState
        state = SessionState.load()
        assert state._data["version"] == 1
        assert state._data["sessions"] == {}
        usage = state.get_usage_today()
        assert usage["input_tokens"] == 0

    def test_load_persisted_state(self, tmp_path, monkeypatch):
        _patch_workspace(tmp_path, monkeypatch)
        from indieclaw.session_state import SessionState
        state = SessionState()
        state.record_turn("chat1", _make_result(100, 50))
        # Load from disk
        loaded = SessionState.load()
        sess = loaded.get_session("chat1")
        assert sess["input_tokens"] == 100
