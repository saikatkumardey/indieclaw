from __future__ import annotations

import os
import signal
import subprocess
import time

from .workspace import PID_FILE


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
        return True


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
