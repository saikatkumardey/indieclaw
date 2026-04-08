"""Workspace tests."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _fake_workspace(tmp_path, monkeypatch):
    import indieclaw.workspace as ws
    fake = tmp_path / "indieclaw_home"
    for attr in ("HOME",):
        monkeypatch.setattr(ws, attr, fake)
    for name, attr in [("SOUL.md", "SOUL"), ("USER.md", "USER"), ("MEMORY.md", "MEMORY"),
                        ("crons.yaml", "CRONS"), ("skills", "SKILLS_DIR"),
                        ("tools", "TOOLS_DIR"), ("uploads", "UPLOADS_DIR")]:
        monkeypatch.setattr(ws, attr, fake / name)
    return ws, fake


def test_init_creates_directories(tmp_path, monkeypatch):
    ws, fake = _fake_workspace(tmp_path, monkeypatch)
    ws.init()
    for d in ("skills", "tools", "uploads", "sessions"):
        assert (fake / d).is_dir()


def test_init_copies_templates(tmp_path, monkeypatch):
    ws, fake = _fake_workspace(tmp_path, monkeypatch)
    tpl = tmp_path / "templates"
    tpl.mkdir()
    (tpl / "SOUL.md").write_text("# Soul template")
    (tpl / "USER.md").write_text("# User template")
    monkeypatch.setattr(ws, "_TEMPLATES", tpl)
    ws.init()
    assert (fake / "SOUL.md").read_text() == "# Soul template"
    assert (fake / "USER.md").exists()


def test_read_existing_file(tmp_path):
    from indieclaw.workspace import read
    f = tmp_path / "test.md"
    f.write_text("hello")
    assert read(f) == "hello"


def test_read_missing_file(tmp_path):
    from indieclaw.workspace import read
    missing = tmp_path / "missing.md"
    assert read(missing) == ""
    assert read(missing, default="fallback") == "fallback"


class TestBranch:
    def test_get_branch_no_file(self, tmp_path, monkeypatch):
        import indieclaw.workspace as ws
        monkeypatch.setattr(ws, "BRANCH_FILE", tmp_path / ".branch")
        assert ws.get_branch() is None

    def test_set_and_get_branch(self, tmp_path, monkeypatch):
        import indieclaw.workspace as ws
        monkeypatch.setattr(ws, "BRANCH_FILE", tmp_path / ".branch")
        ws.set_branch("feat-x")
        assert ws.get_branch() == "feat-x"

    def test_set_branch_none_deletes_file(self, tmp_path, monkeypatch):
        import indieclaw.workspace as ws
        monkeypatch.setattr(ws, "BRANCH_FILE", tmp_path / ".branch")
        ws.set_branch("feat-x")
        ws.set_branch(None)
        assert ws.get_branch() is None
        assert not (tmp_path / ".branch").exists()
