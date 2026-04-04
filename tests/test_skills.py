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
    assert result == [("my_skill", "Do this and that.")]


def test_list_skills_multiple(tmp_path):
    from indieclaw.skills import list_skills
    _make_skill(tmp_path, "alpha", "Alpha skill")
    _make_skill(tmp_path, "beta", "Beta skill")
    result = list_skills(tmp_path)
    assert result == [("alpha", "Alpha skill"), ("beta", "Beta skill")]


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
    assert result == [("real_skill", "Real skill content")]


class TestSkillDescriptions:
    def test_list_skills_returns_tuples(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# My Skill\nDo something useful.\n\nMore details.")
        from indieclaw.skills import list_skills
        result = list_skills(tmp_path)
        assert len(result) == 1
        assert result[0] == ("my-skill", "Do something useful.")

    def test_description_skips_headings(self, tmp_path):
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Title\n## Subtitle\nActual description here.")
        from indieclaw.skills import list_skills
        result = list_skills(tmp_path)
        assert result[0][1] == "Actual description here."

    def test_description_truncated_at_80_chars(self, tmp_path):
        skill_dir = tmp_path / "long-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("A" * 200)
        from indieclaw.skills import list_skills
        result = list_skills(tmp_path)
        assert len(result[0][1]) == 80

    def test_empty_skill_file_returns_empty_desc(self, tmp_path):
        skill_dir = tmp_path / "empty-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Just a heading\n")
        from indieclaw.skills import list_skills
        result = list_skills(tmp_path)
        assert result[0] == ("empty-skill", "")

    def test_no_skills_returns_empty(self, tmp_path):
        from indieclaw.skills import list_skills
        assert list_skills(tmp_path) == []


def test_read_skill_returns_content(tmp_path):
    from indieclaw.skills import read_skill
    _make_skill(tmp_path, "my_skill", "Do this and that.")
    assert read_skill("my_skill", tmp_path) == "Do this and that."


def test_read_skill_not_found(tmp_path):
    from indieclaw.skills import read_skill
    assert read_skill("nonexistent", tmp_path) is None
