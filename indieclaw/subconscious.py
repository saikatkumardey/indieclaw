from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import yaml

from . import workspace

_MAX_THREADS = 20
_REQUIRED_FIELDS = {"id", "created", "priority", "summary", "action", "expires"}
_VALID_PRIORITIES = {"high", "medium", "low"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load_threads() -> list[dict]:
    try:
        data = yaml.safe_load(workspace.SUBCONSCIOUS.read_text()) or {}
    except (FileNotFoundError, yaml.YAMLError):
        return []
    threads = data.get("threads", [])
    if not isinstance(threads, list):
        return []

    now = _now()
    active = []
    for t in threads:
        expires = t.get("expires", "")
        if expires:
            try:
                exp_dt = datetime.fromisoformat(expires)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if exp_dt < now:
                    continue  # expired
            except (ValueError, TypeError):
                pass  # keep if expires is unparseable
        active.append(t)

    if len(active) != len(threads):
        save_threads(active)

    return active


def save_threads(threads: list[dict]) -> None:
    path = workspace.SUBCONSCIOUS
    content = yaml.dump({"threads": threads}, default_flow_style=False)
    fd, tmp_name = tempfile.mkstemp(suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        os.unlink(tmp_name)
        raise


def add_thread(thread_data: dict) -> str:
    missing = _REQUIRED_FIELDS - set(thread_data.keys())
    if missing:
        raise ValueError(f"Missing required fields: {missing}")
    if thread_data.get("priority") not in _VALID_PRIORITIES:
        raise ValueError(f"Invalid priority: {thread_data['priority']!r}. Must be one of {_VALID_PRIORITIES}")

    threads = load_threads()
    threads = [t for t in threads if t.get("id") != thread_data["id"]]
    if len(threads) >= _MAX_THREADS:
        raise ValueError(f"Thread cap reached ({_MAX_THREADS}). Resolve some threads first.")
    threads.append(thread_data)
    save_threads(threads)
    return thread_data["id"]


def resolve_thread(thread_id: str) -> bool:
    threads = load_threads()
    new_threads = [t for t in threads if t.get("id") != thread_id]
    if len(new_threads) == len(threads):
        return False
    save_threads(new_threads)
    return True


def build_prompt(threads: list[dict], recent_logs: str, memory: str) -> str:
    template = workspace.read_template("SUBCONSCIOUS.md")

    if threads:
        threads_str = yaml.dump(threads, default_flow_style=False)
    else:
        threads_str = "(no open threads)"

    return template.format(
        threads=threads_str,
        recent_logs=recent_logs or "(no recent activity)",
        memory=memory or "(no memory)",
    )
