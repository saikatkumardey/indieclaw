from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

HOME = Path(os.getenv("INDIECLAW_HOME", Path.home() / ".indieclaw"))

SOUL       = HOME / "SOUL.md"
AGENT      = HOME / "AGENT.md"
USER       = HOME / "USER.md"
MEMORY     = HOME / "MEMORY.md"
CRONS      = HOME / "crons.yaml"
SKILLS_DIR   = HOME / "skills"
TOOLS_DIR    = HOME / "tools"
TOOLS_STAGING = HOME / "tools" / ".staging"
UPLOADS_DIR  = HOME / "uploads"
HANDOVER       = HOME / "handover.md"
SUBCONSCIOUS   = HOME / "subconscious.yaml"
CONFIG        = HOME / "indieclaw.json"
SESSION_STATE = HOME / "session_state.json"
PID_FILE      = HOME / ".pid"
LOCK_FILE     = HOME / ".lock"
BRANCH_FILE   = HOME / ".branch"
LOG_FILE      = HOME / "indieclaw.log"

_TEMPLATES = Path(__file__).parent.parent / "templates"


def init() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    HOME.chmod(0o700)
    SKILLS_DIR.mkdir(exist_ok=True)
    TOOLS_DIR.mkdir(exist_ok=True)
    TOOLS_STAGING.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(exist_ok=True)
    UPLOADS_DIR.chmod(0o700)
    sessions_dir = HOME / "sessions"
    sessions_dir.mkdir(exist_ok=True)
    sessions_dir.chmod(0o700)

    for name in ("SOUL.md", "AGENT.md", "USER.md", "MEMORY.md", "crons.yaml"):
        dest = HOME / name
        if not dest.exists():
            src = _TEMPLATES / name
            if src.exists():
                shutil.copy(src, dest)

    if not CONFIG.exists():
        write_json(CONFIG, {})

    if not SUBCONSCIOUS.exists():
        SUBCONSCIOUS.write_text("threads: []\n")

    _copy_skill_templates(_TEMPLATES / "skills", SKILLS_DIR)


def _copy_skill_templates(src_skills: Path, dest_skills: Path) -> None:
    if not src_skills.is_dir():
        return
    for skill_dir in src_skills.iterdir():
        if not skill_dir.is_dir():
            continue
        dest_skill = dest_skills / skill_dir.name
        dest_skill.mkdir(exist_ok=True)
        for f in skill_dir.iterdir():
            dest = dest_skill / f.name
            if not dest.exists():
                shutil.copy(f, dest)


def read_template(name: str) -> str:
    path = _TEMPLATES / name
    return path.read_text()


def read(path: Path, default: str = "") -> str:
    try:
        return path.read_text()
    except OSError:
        return default


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        Path(tmp_name).replace(path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def get_branch() -> str | None:
    try:
        text = BRANCH_FILE.read_text().strip()
        return text or None
    except OSError:
        return None


def set_branch(branch: str | None) -> None:
    if branch:
        BRANCH_FILE.write_text(branch + "\n")
    else:
        BRANCH_FILE.unlink(missing_ok=True)
