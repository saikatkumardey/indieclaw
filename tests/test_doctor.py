"""Tests for indieclaw doctor."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from indieclaw.doctor import run


def _patch_workspace(tmp_path: Path, monkeypatch) -> None:
    import indieclaw.workspace as ws
    monkeypatch.setattr(ws, "HOME", tmp_path)
    monkeypatch.setattr(ws, "TOOLS_DIR", tmp_path / "tools")


def _make_healthy(tmp_path: Path) -> None:
    for d in ("sessions", "tools", "skills", "uploads"):
        (tmp_path / d).mkdir(exist_ok=True)
    for name in ("SOUL.md", "MEMORY.md"):
        (tmp_path / name).write_text(f"# {name}\n")
    (tmp_path / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=123:ABC\nALLOWED_USER_IDS=12345\n"
    )


def test_workspace_missing_home(tmp_path, monkeypatch):
    import indieclaw.workspace as ws
    monkeypatch.setattr(ws, "HOME", tmp_path / "nonexistent")
    monkeypatch.setattr(ws, "TOOLS_DIR", tmp_path / "nonexistent" / "tools")
    with patch("indieclaw.daemon.is_running", return_value=(False, None)):
        code = run()
    assert code == 1


def test_workspace_missing_subdir(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy(tmp_path)
    (tmp_path / "tools").rmdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with patch("indieclaw.daemon.is_running", return_value=(True, 1)):
        code = run()
    assert code == 1


def test_workspace_missing_core_file(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy(tmp_path)
    (tmp_path / "SOUL.md").unlink()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with patch("indieclaw.daemon.is_running", return_value=(True, 1)):
        code = run()
    assert code == 1


def test_workspace_env_missing_var(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy(tmp_path)
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=abc\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with patch("indieclaw.daemon.is_running", return_value=(True, 1)):
        code = run()
    assert code == 1


def test_auth_api_key(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with patch("indieclaw.daemon.is_running", return_value=(True, 1)):
        code = run()
    assert code == 0


def test_auth_no_key_no_cli(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("indieclaw.daemon.is_running", return_value=(True, 1)), \
         patch("shutil.which", return_value=None):
        code = run()
    assert code == 1


def test_run_healthy(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with patch("indieclaw.daemon.is_running", return_value=(True, 1)):
        code = run()
    assert code == 0


def test_run_broken_tool(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    (tmp_path / "tools" / "bad.py").write_text("raise RuntimeError('boom')")
    with patch("indieclaw.daemon.is_running", return_value=(True, 1)):
        code = run()
    assert code == 1
