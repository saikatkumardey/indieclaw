"""Skills loader tests."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_skill(base, name, content):
    d = base / name
    d.mkdir()
    (d / "SKILL.md").write_text(content)


def test_list_skills_returns_names(tmp_path):
    from indieclaw.skills import list_skills
    _make_skill(tmp_path, "my_skill", "Do this and that.")
    result = list_skills(tmp_path)
    assert result == ["my_skill"]


def test_list_skills_multiple(tmp_path):
    from indieclaw.skills import list_skills
    _make_skill(tmp_path, "alpha", "Alpha skill")
    _make_skill(tmp_path, "beta", "Beta skill")
    result = list_skills(tmp_path)
    assert result == ["alpha", "beta"]


def test_list_skills_empty_dir(tmp_path):
    from indieclaw.skills import list_skills
    assert list_skills(tmp_path) == []


def test_list_skills_missing_dir(tmp_path):
    from indieclaw.skills import list_skills
    assert list_skills(tmp_path / "no_such_dir") == []


def test_list_skills_ignores_dirs_without_skill_md(tmp_path):
    from indieclaw.skills import list_skills
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "README.md").write_text("not a skill")
    _make_skill(tmp_path, "real_skill", "Real skill content")
    result = list_skills(tmp_path)
    assert result == ["real_skill"]


def test_read_skill_returns_content(tmp_path):
    from indieclaw.skills import read_skill
    _make_skill(tmp_path, "my_skill", "Do this and that.")
    assert read_skill("my_skill", tmp_path) == "Do this and that."


def test_read_skill_not_found(tmp_path):
    from indieclaw.skills import read_skill
    assert read_skill("nonexistent", tmp_path) is None
