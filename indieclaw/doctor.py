from __future__ import annotations

import importlib.util
import os
import shutil

from dotenv import dotenv_values

from . import workspace

_REQUIRED_SUBDIRS = ("sessions", "tools", "skills", "uploads")
_CORE_FILES = ("SOUL.md", "MEMORY.md")
_REQUIRED_ENV_VARS = ("TELEGRAM_BOT_TOKEN", "ALLOWED_USER_IDS")


def _check_workspace(home) -> bool:
    """Check home dir, subdirs, and core files. Returns True if any failed."""
    failed = False
    if not home.is_dir():
        print(f"FAIL  Home directory missing ({home})")
        return True
    print(f"OK    Home directory exists ({home})")
    for name in _REQUIRED_SUBDIRS:
        if (home / name).is_dir():
            print(f"OK    {name}/ exists")
        else:
            print(f"FAIL  {name}/ missing")
            failed = True
    for name in _CORE_FILES:
        if (home / name).exists():
            print(f"OK    {name} present")
        else:
            print(f"FAIL  {name} missing")
            failed = True
    return failed


def _check_env(env_path) -> bool:
    """Check .env vars. Returns True if any failed."""
    if not env_path.exists():
        print("FAIL  .env file missing")
        return True
    failed = False
    env = dotenv_values(env_path)
    for var in _REQUIRED_ENV_VARS:
        if (env.get(var, "") or "").strip():
            print(f"OK    .env has {var}")
        else:
            print(f"FAIL  .env missing or empty: {var}")
            failed = True
    return failed


def _check_auth(env_path) -> bool:
    """Check Claude authentication. Returns True if failed."""
    env = dotenv_values(env_path) if env_path.exists() else {}
    api_key = (env.get("ANTHROPIC_API_KEY", "") or "").strip() or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        print("OK    ANTHROPIC_API_KEY is set")
        return False
    if shutil.which("claude"):
        print("WARN  No API key — claude CLI found (login-based auth)")
        return False
    print("FAIL  No Claude authentication found")
    return True


def _check_tools() -> bool:
    """Check custom tools load. Returns True if any failed."""
    tools_dir = workspace.TOOLS_DIR
    if not tools_dir.is_dir():
        return False
    failed = False
    for path in sorted(tools_dir.glob("*.py")):
        try:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if not hasattr(mod, "SCHEMA") or not hasattr(mod, "execute"):
                print(f"FAIL  Tool {path.name}: missing SCHEMA or execute()")
                failed = True
            else:
                print(f"OK    Tool {path.name} loads OK")
        except Exception as e:
            print(f"FAIL  Tool {path.name} failed to load: {e}")
            failed = True
    return failed


def run() -> int:
    home = workspace.HOME
    failed = _check_workspace(home)
    failed |= _check_env(home / ".env")
    failed |= _check_auth(home / ".env")

    from .daemon import is_running
    running, pid = is_running()
    print(f"OK    Daemon running (PID {pid})" if running else "WARN  Daemon not running")

    failed |= _check_tools()
    return 1 if failed else 0
