from __future__ import annotations

from pathlib import Path


def _extract_description(path: Path) -> str:
    try:
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                return stripped[:80]
    except OSError:
        pass
    return ""


def list_skills(skills_dir: Path = Path("skills")) -> list[tuple[str, str]]:
    if not skills_dir.exists():
        return []
    result = []
    for md in sorted(skills_dir.glob("*/SKILL.md")):
        name = md.parent.name
        desc = _extract_description(md)
        result.append((name, desc))
    return result


def read_skill(name: str, skills_dir: Path = Path("skills")) -> str | None:
    md = skills_dir / name / "SKILL.md"
    try:
        return md.read_text().strip()
    except OSError:
        return None
