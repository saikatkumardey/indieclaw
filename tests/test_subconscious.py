"""Tests for subconscious thread management and prompt building."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))


def _patch_workspace(tmp_path, monkeypatch):
    import indieclaw.workspace as ws
    monkeypatch.setattr(ws, "HOME", tmp_path)
    monkeypatch.setattr(ws, "SUBCONSCIOUS", tmp_path / "subconscious.yaml")


def _make_thread(id: str, priority: str = "medium", hours_until_expiry: int = 24) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": id,
        "created": now.isoformat(),
        "priority": priority,
        "summary": f"Test thread {id}",
        "action": f"Do something about {id}",
        "expires": (now + timedelta(hours=hours_until_expiry)).isoformat(),
    }


class TestLoadThreads:
    def test_load_threads_empty(self, monkeypatch, tmp_path):
        _patch_workspace(tmp_path, monkeypatch)
        from indieclaw.subconscious import load_threads
        assert load_threads() == []


class TestAddThread:
    def test_add_thread(self, monkeypatch, tmp_path):
        _patch_workspace(tmp_path, monkeypatch)
        (tmp_path / "subconscious.yaml").write_text("threads: []\n")
        from indieclaw.subconscious import add_thread, load_threads
        thread = _make_thread("test-1")
        tid = add_thread(thread)
        assert tid == "test-1"
        threads = load_threads()
        assert len(threads) == 1
        assert threads[0]["id"] == "test-1"

    def test_add_thread_enforces_cap(self, monkeypatch, tmp_path):
        _patch_workspace(tmp_path, monkeypatch)
        from indieclaw.subconscious import add_thread, save_threads
        # Fill to cap
        threads = [_make_thread(f"t-{i}") for i in range(20)]
        save_threads(threads)
        with pytest.raises(ValueError, match="cap reached"):
            add_thread(_make_thread("overflow"))

    def test_add_thread_missing_fields(self, monkeypatch, tmp_path):
        _patch_workspace(tmp_path, monkeypatch)
        (tmp_path / "subconscious.yaml").write_text("threads: []\n")
        from indieclaw.subconscious import add_thread
        with pytest.raises(ValueError, match="Missing required"):
            add_thread({"id": "bad"})


class TestResolveThread:
    def test_resolve_thread(self, monkeypatch, tmp_path):
        _patch_workspace(tmp_path, monkeypatch)
        from indieclaw.subconscious import add_thread, load_threads, resolve_thread
        add_thread(_make_thread("to-resolve"))
        assert resolve_thread("to-resolve") is True
        assert load_threads() == []

    def test_resolve_nonexistent(self, monkeypatch, tmp_path):
        _patch_workspace(tmp_path, monkeypatch)
        (tmp_path / "subconscious.yaml").write_text("threads: []\n")
        from indieclaw.subconscious import resolve_thread
        assert resolve_thread("nope") is False


class TestAutoExpire:
    def test_auto_expire(self, monkeypatch, tmp_path):
        _patch_workspace(tmp_path, monkeypatch)
        from indieclaw.subconscious import load_threads
        now = datetime.now(timezone.utc)
        expired = {
            "id": "old",
            "created": (now - timedelta(hours=48)).isoformat(),
            "priority": "low",
            "summary": "Expired thread",
            "action": "nothing",
            "expires": (now - timedelta(hours=1)).isoformat(),
        }
        active = _make_thread("active", hours_until_expiry=24)
        data = {"threads": [expired, active]}
        (tmp_path / "subconscious.yaml").write_text(yaml.dump(data))
        threads = load_threads()
        assert len(threads) == 1
        assert threads[0]["id"] == "active"


class TestBuildPrompt:
    def test_build_prompt(self, monkeypatch, tmp_path):
        _patch_workspace(tmp_path, monkeypatch)
        from indieclaw.subconscious import build_prompt
        threads = [_make_thread("test-prompt")]
        result = build_prompt(threads, "user said hello", "remember: user likes coffee")
        assert "test-prompt" in result
        assert "user said hello" in result
        assert "user likes coffee" in result
        assert "SUBCONSCIOUS_OK" in result

    def test_build_prompt_empty(self, monkeypatch, tmp_path):
        _patch_workspace(tmp_path, monkeypatch)
        from indieclaw.subconscious import build_prompt
        result = build_prompt([], "", "")
        assert "no open threads" in result
        assert "no recent activity" in result
