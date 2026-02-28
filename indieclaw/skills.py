from __future__ import annotations

from pathlib import Path


def list_skills(skills_dir: Path = Path("skills")) -> list[str]:
    if not skills_dir.exists():
        return []
    return sorted(md.parent.name for md in skills_dir.glob("*/SKILL.md") if md.exists())


def read_skill(name: str, skills_dir: Path = Path("skills")) -> str | None:
    md = skills_dir / name / "SKILL.md"
    try:
        return md.read_text().strip()
    except OSError:
        return None
