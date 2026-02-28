from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import requests
import yaml
from dotenv import dotenv_values
from rich.console import Console

from . import workspace
from .agent import AVAILABLE_MODELS

_KNOWN_MODELS = {mid for mid, _ in AVAILABLE_MODELS}

_REQUIRED_SUBDIRS = ("skills", "tools", "uploads", "sessions")
_CORE_FILES = ("SOUL.md", "AGENT.md", "USER.md", "MEMORY.md", "crons.yaml")
_REQUIRED_ENV_VARS = ("TELEGRAM_BOT_TOKEN", "ALLOWED_USER_IDS")

_SESSION_LOG_WARN_BYTES = 100 * 1024 * 1024  # 100 MB
_UPLOADS_WARN_BYTES = 500 * 1024 * 1024  # 500 MB
_SESSION_STATE_WARN_ENTRIES = 50

console = Console()


class Status(Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class CheckResult:
    status: Status
    message: str
    suggestion: str = ""


def _human_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}" if unit != "B" else f"{nbytes} B"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


def _dir_size(path: Path) -> tuple[int, int]:
    total = 0
    count = 0
    if not path.exists():
        return 0, 0
    for f in path.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
            count += 1
    return total, count



def _check_subdirs(home: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    for name in _REQUIRED_SUBDIRS:
        d = home / name
        if d.is_dir():
            results.append(CheckResult(Status.OK, f"{name}/ exists"))
        else:
            results.append(CheckResult(Status.FAIL, f"{name}/ missing", "Run: indieclaw setup"))
    return results


def _check_core_files(home: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    for name in _CORE_FILES:
        f = home / name
        if not f.exists():
            results.append(CheckResult(Status.FAIL, f"{name} missing", "Run: indieclaw setup"))
        elif f.stat().st_size == 0:
            results.append(CheckResult(Status.WARN, f"{name} is empty (0 bytes) — may be corrupted", "Delete and run: indieclaw setup"))
        else:
            results.append(CheckResult(Status.OK, f"{name} present"))
    return results


def _check_crons_yaml(home: Path) -> list[CheckResult]:
    crons = home / "crons.yaml"
    if not (crons.exists() and crons.stat().st_size > 0):
        return []
    try:
        yaml.safe_load(crons.read_text())
        return [CheckResult(Status.OK, "crons.yaml is valid YAML")]
    except yaml.YAMLError as e:
        return [CheckResult(Status.FAIL, f"crons.yaml has invalid YAML: {e}", "Fix the syntax manually")]


def _check_env_file(home: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    env_path = home / ".env"
    if not env_path.exists():
        results.append(CheckResult(Status.FAIL, ".env file missing", "Run: indieclaw setup"))
        return results
    results.append(CheckResult(Status.OK, ".env file exists"))
    env = dotenv_values(env_path)
    for var in _REQUIRED_ENV_VARS:
        val = env.get(var, "")
        if val and val.strip():
            results.append(CheckResult(Status.OK, f".env has {var}"))
        else:
            results.append(CheckResult(Status.FAIL, f".env missing or empty: {var}", "Run: indieclaw setup"))
    return results


def _check_json_config() -> list[CheckResult]:
    results: list[CheckResult] = []
    config_path = workspace.CONFIG
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text())
            max_turns = data.get("max_turns")
            if isinstance(max_turns, int) and max_turns < 2:
                results.append(CheckResult(Status.WARN, f"indieclaw.json: max_turns={max_turns} — agent may be crippled", "Increase max_turns or delete indieclaw.json to reset defaults"))
            else:
                results.append(CheckResult(Status.OK, "indieclaw.json is valid"))
        except (json.JSONDecodeError, ValueError) as e:
            results.append(CheckResult(Status.FAIL, f"indieclaw.json has invalid JSON: {e}", "Delete to reset to defaults"))

    ss_path = workspace.SESSION_STATE
    if ss_path.exists():
        try:
            json.loads(ss_path.read_text())
            results.append(CheckResult(Status.OK, "session_state.json is valid"))
        except (json.JSONDecodeError, ValueError) as e:
            results.append(CheckResult(Status.FAIL, f"session_state.json has invalid JSON: {e}", "Delete to reset"))
    return results


def _check_workspace() -> list[CheckResult]:
    home = workspace.HOME

    if not home.is_dir():
        return [CheckResult(Status.FAIL, f"Home directory missing ({home})", "Run: indieclaw setup")]

    results: list[CheckResult] = [CheckResult(Status.OK, f"Home directory exists ({home})")]
    results.extend(_check_subdirs(home))
    results.extend(_check_core_files(home))
    results.extend(_check_crons_yaml(home))
    results.extend(_check_env_file(home))
    results.extend(_check_json_config())
    return results


def _check_telegram_token(env: dict) -> list[CheckResult]:
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return [CheckResult(Status.WARN, "No Telegram bot token — skipping token check", "Run: indieclaw setup")]
    try:
        resp = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        if resp.status_code == 200:
            username = resp.json().get("result", {}).get("username", "unknown")
            return [CheckResult(Status.OK, f"Telegram bot token valid (@{username})")]
        return [CheckResult(Status.FAIL, f"Telegram bot token rejected (HTTP {resp.status_code})", "Run: indieclaw setup")]
    except Exception as e:
        return [CheckResult(Status.WARN, f"Could not verify Telegram token: {e}", "Check your network connection")]


def _check_claude_auth(env: dict) -> CheckResult:
    api_key = env.get("ANTHROPIC_API_KEY", "").strip() or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        return CheckResult(Status.OK, "ANTHROPIC_API_KEY is set")
    if shutil.which("claude"):
        return CheckResult(Status.WARN, "No API key — claude CLI found (login-based auth, unverifiable)")
    return CheckResult(Status.FAIL, "No Claude authentication found", "Run: indieclaw setup-token")


def _resolve_model(env: dict) -> str:
    model = env.get("INDIECLAW_MODEL", "").strip() or os.environ.get("INDIECLAW_MODEL", "").strip()
    if not model:
        config_path = workspace.CONFIG
        if config_path.exists():
            try:
                model = json.loads(config_path.read_text()).get("model", "")
            except (json.JSONDecodeError, ValueError):
                pass
    return model or "claude-sonnet-4-6"


def _check_model(env: dict) -> CheckResult:
    model = _resolve_model(env)
    if model in _KNOWN_MODELS:
        return CheckResult(Status.OK, f"Model: {model}")
    return CheckResult(Status.WARN, f"Model '{model}' is not a known model ID (may still work)")


def _check_single_tool(path: Path) -> CheckResult:
    try:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        return CheckResult(Status.FAIL, f"Tool {path.name} failed to load: {e}", "Fix the error or remove the file")
    if not hasattr(mod, "SCHEMA"):
        return CheckResult(Status.FAIL, f"Tool {path.name}: missing SCHEMA", "Add SCHEMA dict or remove the file")
    if not hasattr(mod, "execute"):
        return CheckResult(Status.FAIL, f"Tool {path.name}: missing execute()", "Add execute() function or remove the file")
    name = mod.SCHEMA.get("function", {}).get("name", "?")
    return CheckResult(Status.OK, f"Tool {path.name} loads OK ({name})")


def _check_custom_tools() -> list[CheckResult]:
    tools_dir = workspace.TOOLS_DIR
    if not tools_dir.is_dir():
        return []
    py_files = sorted(tools_dir.glob("*.py"))
    if not py_files:
        return [CheckResult(Status.OK, "No custom tools (tools/ empty)")]
    return [_check_single_tool(path) for path in py_files]


def _check_browser_backend() -> list[CheckResult]:
    results: list[CheckResult] = []
    lp_bin = shutil.which("lightpanda")
    if lp_bin:
        results.append(CheckResult(Status.OK, "Lightpanda found (preferred browser backend)"))
    else:
        results.append(CheckResult(Status.OK, "Lightpanda not found — will use Chromium", "Optional: install Lightpanda for faster, lighter browsing: https://github.com/lightpanda-io/browser"))

    pw_check = shutil.which("playwright")
    if pw_check:
        pw_result = subprocess.run(["playwright", "install", "--dry-run", "chromium"], capture_output=True, text=True, timeout=10)
        if pw_result.returncode == 0:
            results.append(CheckResult(Status.OK, "Playwright Chromium installed (fallback)"))
        elif not lp_bin:
            results.append(CheckResult(Status.WARN, "Playwright Chromium not installed — browser tools won't work", "Run: playwright install chromium --with-deps"))
        else:
            results.append(CheckResult(Status.OK, "Playwright Chromium not installed (Lightpanda available)"))
    elif not lp_bin:
        results.append(CheckResult(Status.WARN, "No browser backend available — browser tools won't work", "Install Lightpanda or run: playwright install chromium --with-deps"))
    return results


def _check_tts_deps() -> CheckResult:
    edge_tts = shutil.which("edge-tts")
    ffmpeg = shutil.which("ffmpeg")
    if edge_tts and ffmpeg:
        return CheckResult(Status.OK, "edge-tts and ffmpeg available (TTS ready)")
    missing = [name for name, present in [("edge-tts", edge_tts), ("ffmpeg", ffmpeg)] if not present]
    return CheckResult(Status.WARN, f"Missing {', '.join(missing)} — voice messages (telegram_send_voice) won't work", "Install: pip install edge-tts && apt install ffmpeg")


def _check_cron_expressions() -> list[CheckResult]:
    crons_path = workspace.CRONS
    if not (crons_path.exists() and crons_path.stat().st_size > 0):
        return []
    try:
        data = yaml.safe_load(crons_path.read_text()) or {}
    except yaml.YAMLError:
        return []  # already reported in workspace checks
    results: list[CheckResult] = []
    for job in data.get("jobs", []):
        cron_expr = job.get("cron", "")
        if not cron_expr:
            continue
        job_id = job.get("id", "?")
        try:
            from apscheduler.triggers.cron import CronTrigger
            CronTrigger.from_crontab(cron_expr)
            results.append(CheckResult(Status.OK, f"Cron '{job_id}': expression valid ({cron_expr})"))
        except Exception as e:
            results.append(CheckResult(Status.FAIL, f"Cron '{job_id}': invalid expression '{cron_expr}' — {e}", "Fix the cron expression in crons.yaml"))
    return results


def _check_runtime() -> list[CheckResult]:
    env_path = workspace.HOME / ".env"
    env = dotenv_values(env_path) if env_path.exists() else {}

    results: list[CheckResult] = []
    results.extend(_check_telegram_token(env))
    results.append(_check_claude_auth(env))
    results.append(_check_model(env))
    results.extend(_check_custom_tools())
    results.extend(_check_browser_backend())
    results.append(_check_tts_deps())
    results.extend(_check_cron_expressions())
    return results


def _check_process() -> list[CheckResult]:
    results: list[CheckResult] = []
    from .daemon import is_running
    running, pid = is_running()
    if running:
        results.append(CheckResult(Status.OK, f"Daemon running (PID {pid})"))
    else:
        results.append(CheckResult(Status.WARN, "Daemon not running", "Run: indieclaw start"))
    return results


def _check_dir_size(label: str, path, warn_bytes: int, clean_cmd: str) -> CheckResult:
    total, count = _dir_size(path)
    size_str = _human_size(total)
    if total > warn_bytes:
        return CheckResult(Status.WARN, f"{label}: {size_str} across {count} files", f"Consider cleaning: {clean_cmd}")
    return CheckResult(Status.OK, f"{label}: {size_str} ({count} files)")


def _check_state() -> list[CheckResult]:
    results: list[CheckResult] = []
    home = workspace.HOME

    sessions_dir = home / "sessions"
    results.append(_check_dir_size("Session logs", sessions_dir, _SESSION_LOG_WARN_BYTES, f"rm {sessions_dir}/*.jsonl"))

    uploads_dir = home / "uploads"
    results.append(_check_dir_size("Uploads", uploads_dir, _UPLOADS_WARN_BYTES, f"rm {uploads_dir}/*"))

    # Session state entries
    ss_path = workspace.SESSION_STATE
    if ss_path.exists():
        try:
            data = json.loads(ss_path.read_text())
            sessions = data.get("sessions", {})
            count = len(sessions)
            if count > _SESSION_STATE_WARN_ENTRIES:
                results.append(CheckResult(
                    Status.WARN,
                    f"Session state: {count} entries",
                    "Informational — old sessions accumulate over time",
                ))
            else:
                results.append(CheckResult(
                    Status.OK,
                    f"Session state: {count} entries",
                ))
        except (json.JSONDecodeError, ValueError):
            pass  # already reported in workspace checks

    # Stale handover
    handover = workspace.HANDOVER
    if handover.exists():
        results.append(CheckResult(
            Status.WARN,
            "handover.md exists — leftover from crash or restart",
            "Will be consumed on next bot start; delete manually if stale",
        ))
    else:
        results.append(CheckResult(Status.OK, "No stale handover.md"))

    return results


_ICONS = {
    Status.OK: "[bold green]✓[/bold green]",
    Status.WARN: "[bold yellow]![/bold yellow]",
    Status.FAIL: "[bold red]✗[/bold red]",
}


def _compute_score(ok: int, warn: int, fail: int, total: int) -> int:
    if total == 0:
        return 100
    points = ok * 1.0 + warn * 0.5
    return round(100 * points / total)


def _print_results(categories: dict[str, list[CheckResult]]) -> None:
    console.print()
    console.print("[bold]IndieClaw Doctor[/bold]")
    console.print("=" * 40)

    totals = {Status.OK: 0, Status.WARN: 0, Status.FAIL: 0}

    for title, checks in categories.items():
        console.print()
        console.print(f"[bold]{title}[/bold]")
        for c in checks:
            totals[c.status] += 1
            icon = _ICONS[c.status]
            console.print(f"  {icon} {c.message}")
            if c.suggestion:
                console.print(f"    [dim]→ {c.suggestion}[/dim]")

    console.print()
    ok, warn, fail = totals[Status.OK], totals[Status.WARN], totals[Status.FAIL]
    total = ok + warn + fail
    score = _compute_score(ok, warn, fail, total)
    parts = []
    if ok:
        parts.append(f"[green]{ok} passed[/green]")
    if warn:
        parts.append(f"[yellow]{warn} warnings[/yellow]")
    if fail:
        parts.append(f"[red]{fail} failures[/red]")
    console.print(f"[bold]Summary:[/bold] {', '.join(parts)}")
    score_color = "green" if score >= 90 else "yellow" if score >= 70 else "red"
    console.print(f"[bold]Score:[/bold] [{score_color}]{score}/100[/{score_color}]")
    console.print()


def run() -> int:
    categories: dict[str, list[CheckResult]] = {}
    categories["Workspace"] = _check_workspace()
    categories["Runtime"] = _check_runtime()
    categories["Process"] = _check_process()
    categories["State"] = _check_state()
    _print_results(categories)

    has_failures = any(
        c.status == Status.FAIL
        for checks in categories.values()
        for c in checks
    )
    return 1 if has_failures else 0
