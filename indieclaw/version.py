from __future__ import annotations

import importlib.metadata
import re
import subprocess
from pathlib import Path

from loguru import logger


def local_version() -> str:
    try:
        return importlib.metadata.version("indieclaw")
    except Exception:
        pass
    # uv tool list fallback
    try:
        result = subprocess.run(["uv", "tool", "list"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            if "indieclaw" in line.lower():
                m = re.search(r"v?(\d+\.\d+\.\d+)", line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    # pyproject.toml fallback
    try:
        toml = Path(__file__).parent.parent / "pyproject.toml"
        if toml.exists():
            m = re.search(r'version\s*=\s*"([^"]+)"', toml.read_text())
            if m:
                return m.group(1)
    except Exception:
        pass
    return "unknown"


def _extract_repo(source: str) -> str | None:
    """Extract GitHub owner/repo from a source URL."""
    repo_match = re.search(r"github\.com/([^/]+/[^/.\s]+)", source)
    if not repo_match:
        return None
    return repo_match.group(1).rstrip(".git")


def check_remote_version(source: str) -> str | None:
    try:
        import requests
        repo = _extract_repo(source)
        if not repo:
            return None
        resp = requests.get(
            f"https://raw.githubusercontent.com/{repo}/main/pyproject.toml",
            timeout=10,
        )
        if resp.status_code == 200:
            m = re.search(r'version\s*=\s*"([^"]+)"', resp.text)
            if m:
                return m.group(1)
    except Exception:
        logger.debug("remote version check failed", exc_info=True)
    return None


def _detect_new_version() -> str:
    try:
        ver_result = subprocess.run(
            ["uv", "tool", "run", "indieclaw", "--", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if ver_result.returncode == 0:
            m = re.search(r"(\d+\.\d+\.\d+)", ver_result.stdout)
            if m:
                return m.group(1)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        list_result = subprocess.run(
            ["uv", "tool", "list"], capture_output=True, text=True, timeout=10,
        )
        for line in list_result.stdout.splitlines():
            if "indieclaw" in line.lower():
                m = re.search(r"v?(\d+\.\d+\.\d+)", line)
                if m:
                    return m.group(1)
                break
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _fetch_recent_changes(source: str, old_version: str, max_changes: int = 5) -> list[str]:
    """Fetch commit messages between old version tag and HEAD from GitHub."""
    repo = _extract_repo(source)
    if not repo:
        return []
    try:
        import requests
        tag = f"v{old_version}"
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/compare/{tag}...HEAD",
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        commits = resp.json().get("commits", [])
        changes = []
        for commit in commits:
            msg = commit.get("commit", {}).get("message", "").split("\n")[0]
            if msg and not msg.startswith("release:"):
                changes.append(f"- {msg}")
                if len(changes) >= max_changes:
                    break
        return changes
    except Exception:
        return []


def get_update_summary(source: str, old_version: str) -> str:
    """Get version transition and changelog after a successful update."""
    new_version = _detect_new_version()
    changes = _fetch_recent_changes(source, old_version)
    parts = [f"{old_version} -> {new_version}"]
    if changes:
        parts.append("\nRecent changes:\n" + "\n".join(changes))
    return "\n".join(parts)
