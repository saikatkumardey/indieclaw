from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import time

from .workspace import LOCK_FILE, PID_FILE

# Module-level lock file handle — kept open for the lifetime of the process.
# The OS releases the flock automatically when the process dies.
_lock_fh = None


def acquire_lock() -> bool:
    """Try to acquire the singleton flock. Returns True if acquired, False if another instance holds it."""
    global _lock_fh
    _lock_fh = open(LOCK_FILE, "w")  # noqa: WPS515 — intentionally kept open
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fh.write(str(os.getpid()))
        _lock_fh.flush()
        return True
    except OSError:
        _lock_fh.close()
        _lock_fh = None
        return False


def release_lock() -> None:
    global _lock_fh
    if _lock_fh is not None:
        try:
            fcntl.flock(_lock_fh, fcntl.LOCK_UN)
            _lock_fh.close()
        except OSError:
            pass
        _lock_fh = None
        LOCK_FILE.unlink(missing_ok=True)


def read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def write_pid(pid: int) -> None:
    PID_FILE.write_text(str(pid))


def delete_pid() -> None:
    PID_FILE.unlink(missing_ok=True)


def _is_indieclaw_process(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
        return "indieclaw" in result.stdout.lower()
    except (OSError, subprocess.TimeoutExpired):
        return False  # fail-safe: don't assume it's ours


def is_running() -> tuple[bool, int | None]:
    pid = read_pid()
    if pid is None:
        return False, None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        delete_pid()
        return False, None
    except PermissionError:
        if not _is_indieclaw_process(pid):
            delete_pid()
            return False, None
        return True, pid
    if not _is_indieclaw_process(pid):
        delete_pid()
        return False, None
    return True, pid


def stop_daemon(timeout: int = 10) -> bool:
    running, pid = is_running()
    if not running or pid is None:
        return False

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        delete_pid()
        return True

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.25)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            delete_pid()
            return True
        except PermissionError:
            pass  # still alive

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    delete_pid()
    return True
