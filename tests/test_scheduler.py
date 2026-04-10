"""Tests for scheduler.py — cron job timeout and delivery."""
from __future__ import annotations

import asyncio
import os
import threading
from unittest.mock import patch

from indieclaw import scheduler as _sched


def _with_main_loop(fn):
    """Run test function while a real event loop is available as _main_loop."""
    def wrapper(*args, **kwargs):
        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()
        original = _sched._main_loop
        _sched._main_loop = loop
        try:
            fn(*args, **kwargs)
        finally:
            _sched._main_loop = original
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=5)
            loop.close()
    return wrapper


class TestRunJobTimeout:
    """Cron jobs must not block forever — verify timeout behavior."""

    @patch.object(_sched, "_send_telegram")
    @patch("indieclaw.agent.run")
    @_with_main_loop
    def test_timeout_cancels_future(self, mock_run, mock_send):
        """A cron job that exceeds the timeout notifies the user."""

        async def _hang(*a, **kw):
            await asyncio.sleep(30)
            return "should never arrive"

        mock_run.side_effect = _hang

        with patch.object(_sched, "_CRON_TIMEOUT_SECONDS", 0.5):
            _sched._run_job("test-hang", "prompt", deliver_to="123")

        mock_send.assert_called_once()
        assert "timed out" in mock_send.call_args.kwargs["message"]

    @patch.object(_sched, "_send_telegram")
    @patch("indieclaw.agent.run")
    @_with_main_loop
    def test_successful_job_delivers(self, mock_run, mock_send):
        """A job that completes within timeout delivers its result."""

        async def _quick(*a, **kw):
            return "hello world"

        mock_run.side_effect = _quick
        _sched._run_job("test-ok", "prompt", deliver_to="123")
        mock_send.assert_called_once_with(chat_id="123", message="hello world")

    @patch.object(_sched, "_send_telegram")
    @patch("indieclaw.agent.run")
    @_with_main_loop
    def test_exception_notifies_user(self, mock_run, mock_send):
        """A job that raises an exception should notify the user."""

        async def _boom(*a, **kw):
            raise RuntimeError("boom")

        mock_run.side_effect = _boom
        _sched._run_job("test-err", "prompt", deliver_to="123")
        mock_send.assert_called_once()
        assert "failed" in mock_send.call_args.kwargs["message"]
        assert "boom" in mock_send.call_args.kwargs["message"]


class TestSubconsciousTimeout:
    """Subconscious jobs get a longer timeout than regular cron jobs."""

    def test_subconscious_timeout_is_longer_than_default(self):
        assert _sched._SUBCONSCIOUS_TIMEOUT_SECONDS > _sched._CRON_TIMEOUT_SECONDS

    @patch.object(_sched, "_send_telegram")
    @patch("indieclaw.agent.run")
    def test_subconscious_passes_longer_timeout(self, mock_run, mock_send):
        """_run_subconscious uses _SUBCONSCIOUS_TIMEOUT_SECONDS, not the default."""
        call_log = []

        def fake_run_job(job_id, prompt, deliver_to, timeout=None):
            call_log.append({"job_id": job_id, "timeout": timeout})

        with patch.object(_sched, "_run_job", side_effect=fake_run_job), \
             patch("indieclaw.subconscious.load_threads", return_value=[]), \
             patch("indieclaw.workspace.read", return_value=""), \
             patch("indieclaw.workspace.HOME") as mock_home, \
             patch("indieclaw.config.Config.load") as mock_cfg:
            mock_cfg.return_value.get = lambda k, default=None: True if k == "subconscious_enabled" else default
            mock_home.__truediv__ = lambda self, x: mock_home
            mock_home.exists.return_value = False
            _sched._run_subconscious()

        assert len(call_log) == 1
        assert call_log[0]["timeout"] == _sched._SUBCONSCIOUS_TIMEOUT_SECONDS


class TestDeliverToExplicitEmpty:
    """Jobs with deliver_to: '' should not deliver results to Telegram."""

    @patch.object(_sched, "_send_telegram")
    @patch.object(_sched, "default_chat_id", return_value="999")
    def test_deliver_to_empty_string_suppresses_delivery(self, mock_default, mock_send):
        """deliver_to='' means the agent handles its own delivery — scheduler should not send."""
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler()
        job = {"id": "test-no-deliver", "cron": "0 * * * *", "prompt": "do stuff", "deliver_to": ""}
        _sched._schedule_cron_job(scheduler, job)

        # Extract the kwargs that _schedule_cron_job passed to add_job
        added = scheduler.get_job("test-no-deliver")
        assert added is not None
        assert added.kwargs["deliver_to"] == ""

    @patch.object(_sched, "_send_telegram")
    @patch.object(_sched, "default_chat_id", return_value="999")
    def test_deliver_to_absent_falls_back_to_default(self, mock_default, mock_send):
        """When deliver_to is not set, fall back to default_chat_id()."""
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler()
        job = {"id": "test-default-deliver", "cron": "0 * * * *", "prompt": "do stuff"}
        _sched._schedule_cron_job(scheduler, job)

        added = scheduler.get_job("test-default-deliver")
        assert added is not None
        assert added.kwargs["deliver_to"] == "999"



class TestVersionCheckSkipped:
    """Cron jobs skip the SDK version check subprocess."""

    def test_skip_version_check_env_set(self):
        assert os.environ.get("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK") == "1"
