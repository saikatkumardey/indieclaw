"""Daemon tests — PID file helpers and stale PID detection."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _patch_pid_file(tmp_path, monkeypatch):
    pid_file = tmp_path / ".pid"
    import indieclaw.daemon as dm
    monkeypatch.setattr(dm, "PID_FILE", pid_file)
    return pid_file


class TestPidFileRoundTrip:
    def test_write_and_read(self, tmp_path, monkeypatch):
        _patch_pid_file(tmp_path, monkeypatch)
        from indieclaw.daemon import read_pid, write_pid
        write_pid(12345)
        assert read_pid() == 12345

    def test_read_missing(self, tmp_path, monkeypatch):
        _patch_pid_file(tmp_path, monkeypatch)
        from indieclaw.daemon import read_pid
        assert read_pid() is None

    def test_delete_removes_file(self, tmp_path, monkeypatch):
        pid_file = _patch_pid_file(tmp_path, monkeypatch)
        from indieclaw.daemon import delete_pid, write_pid
        write_pid(99)
        assert pid_file.exists()
        delete_pid()
        assert not pid_file.exists()

    def test_delete_missing_is_noop(self, tmp_path, monkeypatch):
        _patch_pid_file(tmp_path, monkeypatch)
        from indieclaw.daemon import delete_pid
        delete_pid()  # should not raise


class TestIsRunning:
    def test_missing_pid_file(self, tmp_path, monkeypatch):
        _patch_pid_file(tmp_path, monkeypatch)
        from indieclaw.daemon import is_running
        assert is_running() == (False, None)

    def test_dead_process(self, tmp_path, monkeypatch):
        pid_file = _patch_pid_file(tmp_path, monkeypatch)
        from indieclaw.daemon import is_running, write_pid
        write_pid(999999)  # almost certainly not running
        with patch("os.kill", side_effect=ProcessLookupError):
            running, pid = is_running()
        assert running is False
        assert pid is None
        assert not pid_file.exists()  # stale PID file cleaned up

    def test_stale_pid_not_indieclaw(self, tmp_path, monkeypatch):
        """PID exists but belongs to a non-indieclaw process — should return False."""
        _patch_pid_file(tmp_path, monkeypatch)
        from indieclaw.daemon import is_running, write_pid
        write_pid(os.getpid())  # current process is pytest, not indieclaw
        with patch("indieclaw.daemon._is_indieclaw_process", return_value=False):
            running, pid = is_running()
        assert running is False
        assert pid is None

    def test_alive_indieclaw_process(self, tmp_path, monkeypatch):
        _patch_pid_file(tmp_path, monkeypatch)
        from indieclaw.daemon import is_running, write_pid
        write_pid(os.getpid())
        with patch("indieclaw.daemon._is_indieclaw_process", return_value=True):
            running, pid = is_running()
        assert running is True
        assert pid == os.getpid()


class TestIsIndieclawProcess:
    def test_matches_indieclaw(self, monkeypatch):
        from unittest.mock import MagicMock

        from indieclaw.daemon import _is_indieclaw_process
        result = MagicMock()
        result.stdout = "/usr/local/bin/indieclaw start --foreground"
        with patch("subprocess.run", return_value=result):
            assert _is_indieclaw_process(123) is True

    def test_no_match(self, monkeypatch):
        from unittest.mock import MagicMock

        from indieclaw.daemon import _is_indieclaw_process
        result = MagicMock()
        result.stdout = "/usr/bin/python3 some_other_script.py"
        with patch("subprocess.run", return_value=result):
            assert _is_indieclaw_process(123) is False

    def test_os_error_returns_true(self):
        """If ps fails with OSError, assume it could be indieclaw (safe default)."""
        from indieclaw.daemon import _is_indieclaw_process
        with patch("subprocess.run", side_effect=OSError("no ps")):
            assert _is_indieclaw_process(123) is True

    def test_timeout_returns_true(self):
        """If ps times out, assume it could be indieclaw (safe default)."""
        import subprocess

        from indieclaw.daemon import _is_indieclaw_process
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ps", 5)):
            assert _is_indieclaw_process(123) is True
