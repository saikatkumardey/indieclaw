from __future__ import annotations

import asyncio
import os

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from . import workspace
from .auth import default_chat_id
from .tools import _send_telegram

os.environ.setdefault("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")

SUBCONSCIOUS_OK = "SUBCONSCIOUS_OK"


_CRON_TIMEOUT_SECONDS = 300
_SUBCONSCIOUS_TIMEOUT_SECONDS = 600

_auth_fail_count = 0
_AUTH_ALERT_THRESHOLD = 3
_auth_alert_sent = False


def _run_agent_on_main_loop(job_id: str, prompt: str, timeout: int) -> tuple[str | None, Exception | None]:
    """Run agent coroutine on the main event loop via run_coroutine_threadsafe.

    This avoids creating throwaway event loops in threads, which caused
    "Event loop is closed" errors from cross-loop object leakage.
    """
    from .agent import run

    if not _main_loop or _main_loop.is_closed() or not _main_loop.is_running():
        return None, RuntimeError("Main event loop not available")

    chat_id = f"cron:{job_id}"
    future = asyncio.run_coroutine_threadsafe(
        run(chat_id=chat_id, user_message=prompt), _main_loop,
    )
    try:
        result = future.result(timeout=timeout)
        return (result or "(no response)"), None
    except TimeoutError:
        future.cancel()
        return None, TimeoutError(f"timed out after {timeout}s")
    except Exception as e:
        return None, e


def _should_suppress_result(job_id: str, result: str) -> bool:
    if job_id == "subconscious" and SUBCONSCIOUS_OK in result:
        return True
    return result == "(no response)"


def _is_auth_error(exc: Exception) -> bool:
    exc_str = str(exc).lower()
    return "401" in exc_str or "authentication" in exc_str or "oauth" in exc_str or ("token" in exc_str and "expired" in exc_str)


_AUTH_REMIND_INTERVAL = 10


def _handle_auth_failure(job_id: str, exc: Exception, deliver_to: str) -> None:
    global _auth_fail_count, _auth_alert_sent
    _auth_fail_count += 1
    if not deliver_to:
        return
    if _auth_fail_count >= _AUTH_ALERT_THRESHOLD and not _auth_alert_sent:
        _send_telegram(
            chat_id=deliver_to,
            message=f"⚠️ AUTH DOWN — {_auth_fail_count} consecutive auth failures. "
            f"OAuth token likely expired. All crons are failing. "
            f"Please run: claude /login"
        )
        _auth_alert_sent = True
    elif _auth_alert_sent and _auth_fail_count % _AUTH_REMIND_INTERVAL == 0:
        _send_telegram(
            chat_id=deliver_to,
            message=f"⚠️ AUTH STILL DOWN — {_auth_fail_count} consecutive failures. "
            f"Please run: claude /login"
        )
    elif not _auth_alert_sent:
        _send_telegram(chat_id=deliver_to, message=f"Cron '{job_id}' failed: {exc}")


def _reset_auth_tracking() -> None:
    global _auth_fail_count, _auth_alert_sent
    _auth_fail_count = 0
    _auth_alert_sent = False


def _run_job(job_id: str, prompt: str, deliver_to: str, timeout: int | None = None) -> None:
    if timeout is None:
        timeout = _CRON_TIMEOUT_SECONDS
    logger.info("Cron: {}", job_id)

    result, exc = _run_agent_on_main_loop(job_id, prompt, timeout)

    if isinstance(exc, TimeoutError):
        logger.error("Cron {} timed out after {}s — thread abandoned (daemon, will die on exit)", job_id, timeout)
        if deliver_to:
            _send_telegram(chat_id=deliver_to, message=f"Cron '{job_id}' timed out after {timeout}s.")
        return

    if exc is not None:
        logger.error("Cron {} failed: {}", job_id, exc, exc_info=exc)
        if _is_auth_error(exc):
            _handle_auth_failure(job_id, exc, deliver_to)
        else:
            _reset_auth_tracking()
            if deliver_to:
                _send_telegram(chat_id=deliver_to, message=f"Cron '{job_id}' failed: {exc}")
        return

    _reset_auth_tracking()

    if not _should_suppress_result(job_id, result) and deliver_to:
        _send_telegram(chat_id=deliver_to, message=result)


def _read_recent_logs(sessions_dir, tail_bytes: int = 4000) -> str:
    if not sessions_dir.exists():
        return ""
    log_parts = []
    for f in sorted(sessions_dir.glob("*.jsonl"), reverse=True)[:3]:
        try:
            size = f.stat().st_size
            if size == 0:
                continue
            with open(f, "rb") as fh:
                if size > tail_bytes:
                    fh.seek(size - tail_bytes)
                    fh.readline()  # skip partial first line
                log_parts.append(fh.read().decode("utf-8", errors="replace"))
        except Exception:
            continue
    return "\n".join(log_parts)[:8000]


def _run_subconscious() -> None:
    from .config import Config
    cfg = Config.load()
    if not cfg.get("subconscious_enabled", True):
        logger.debug("Subconscious disabled via config")
        return

    from . import subconscious
    threads = subconscious.load_threads()
    recent_logs = _read_recent_logs(workspace.HOME / "sessions")
    memory = workspace.read(workspace.MEMORY)
    prompt = subconscious.build_prompt(threads, recent_logs, memory)
    deliver_to = default_chat_id()
    _run_job("subconscious", prompt, deliver_to, timeout=_SUBCONSCIOUS_TIMEOUT_SECONDS)


def _cleanup_stale_files() -> None:
    import time
    cutoff = time.time() - 7 * 86400
    for dirname in ("screenshots", "uploads"):
        d = workspace.HOME / dirname
        if not d.is_dir():
            continue
        for f in d.iterdir():
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
            except Exception:
                continue


_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Store the main event loop so scheduler threads can schedule coroutines on it."""
    global _main_loop
    _main_loop = loop


def _cleanup_idle_browsers() -> None:
    try:
        from .browser import BrowserManager
        mgr = BrowserManager.get()
        if not mgr._contexts:
            return
        # BrowserManager._lock is bound to the main event loop.
        # Use run_coroutine_threadsafe to run cleanup there.
        if _main_loop and _main_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(mgr.cleanup_idle(), _main_loop)
            future.result(timeout=30)
        else:
            asyncio.run(mgr.cleanup_idle())
    except Exception as e:
        logger.debug("Browser cleanup skipped: {}", e)


def _cleanup_idle_cc_sessions() -> None:
    """Prune CC sessions that have been idle for too long."""
    try:
        from .claude_code import cleanup_idle_sessions
        removed = cleanup_idle_sessions()
        if removed:
            logger.info("Cleaned up {} idle CC session(s)", removed)
    except Exception as e:
        logger.debug("CC session cleanup skipped: {}", e)


def _schedule_builtin_jobs(scheduler: BackgroundScheduler) -> None:
    scheduler.add_job(
        _cleanup_idle_browsers,
        IntervalTrigger(minutes=5),
        id="_browser_cleanup",
        replace_existing=True,
    )
    scheduler.add_job(
        _cleanup_stale_files,
        IntervalTrigger(hours=24),
        id="_file_cleanup",
        replace_existing=True,
    )
    scheduler.add_job(
        _cleanup_idle_cc_sessions,
        IntervalTrigger(minutes=30),
        id="_cc_session_cleanup",
        replace_existing=True,
    )

    from .config import Config
    cfg = Config.load()
    if cfg.get("subconscious_enabled", True):
        interval_hours = cfg.get("subconscious_interval_hours", 2)
        scheduler.add_job(
            _run_subconscious,
            IntervalTrigger(hours=interval_hours),
            id="_subconscious",
            replace_existing=True,
        )
        logger.info("Scheduled: subconscious (every {}h)", interval_hours)


def _should_skip_cron_job(job: dict) -> bool:
    if job.get("disabled"):
        logger.info("Skipping disabled job: {}", job.get("id", "?"))
        return True
    missing = [f for f in ("id", "cron", "prompt") if f not in job]
    if missing:
        logger.warning("Skipping cron job — missing fields: %s", missing)
        return True
    return False


def _schedule_cron_job(scheduler: BackgroundScheduler, job: dict) -> None:
    # If deliver_to is explicitly set (even to ""), respect it.
    # Only fall back to default_chat_id() when the key is absent.
    deliver_to = job.get("deliver_to", None)
    if deliver_to is None:
        deliver_to = default_chat_id()
    job_timeout = int(job.get("timeout", _CRON_TIMEOUT_SECONDS))
    try:
        scheduler.add_job(
            _run_job,
            CronTrigger.from_crontab(job["cron"]),
            kwargs={
                "job_id": job["id"],
                "prompt": job["prompt"],
                "deliver_to": deliver_to,
                "timeout": job_timeout,
            },
            id=job["id"],
            replace_existing=True,
            max_instances=2,
            misfire_grace_time=300,
        )
        logger.info("Scheduled: {} ({})", job["id"], job["cron"])
    except Exception as e:
        logger.error("Failed to schedule job %s: %s", job.get("id", "?"), e)


def setup_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    _schedule_builtin_jobs(scheduler)

    crons_path = workspace.CRONS
    if not crons_path.exists():
        return scheduler

    data = yaml.safe_load(crons_path.read_text()) or {}
    for job in data.get("jobs", []):
        if not _should_skip_cron_job(job):
            _schedule_cron_job(scheduler, job)
    return scheduler
