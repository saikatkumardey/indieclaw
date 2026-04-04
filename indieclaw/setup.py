from __future__ import annotations

import getpass
import json
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import questionary
import requests
import yaml
from dotenv import dotenv_values
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .setup_services import install_systemd_service as _install_systemd_service
from .setup_services import install_watchdog as _install_watchdog

console = Console()

BANNER = r"""
 ____                  _  ____  _
/ ___|| _ __ ___   ___ | |/ ___|| | __ _ __      __
\___ \| '_ ` _ \ / _ \| | |    | |/ _` |\ \ /\ / /
 ___) | | | | | | (_) | | |___ | | (_| | \ V  V /
|____/|_| |_| |_|\___/|_|\____||_|\__,_|  \_/\_/
"""

def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(dotenv_values(path))


def _write_env(path: Path, data: dict[str, str]) -> None:
    lines: list[str] = []
    for k, v in data.items():
        escaped = v.replace('\\', '\\\\').replace('"', '\\"')
        lines.append(f'{k}="{escaped}"')
    path.write_text("\n".join(lines) + "\n")


def _step_header(num: int, title: str, total: int = 3) -> None:
    console.print()
    console.print(Rule(
        f"[bold blue]  Step {num}/{total} — {title}  [/bold blue]",
        style="blue",
        align="left",
    ))
    console.print()


def _success(msg: str) -> None:
    console.print(f"  [bold green]✓[/bold green]  {msg}")


def _warn(msg: str) -> None:
    console.print(f"  [bold yellow]⚠[/bold yellow]  {msg}")


def _error(msg: str) -> None:
    console.print(f"  [bold red]✗[/bold red]  {msg}")


def _info(msg: str) -> None:
    console.print(f"  [dim]→[/dim]  {msg}")


def _already(label: str, value: str) -> None:
    console.print(
        Panel(
            f"[green]Already configured:[/green]  {value}",
            title=f"[dim]{label}[/dim]",
            border_style="dim green",
            padding=(0, 2),
        )
    )


def _validate_token(token: str) -> tuple[bool, str | None]:
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            username = data["result"].get("username", "?")
            return True, f"@{username}"
        return False, data.get("description", "Invalid token")
    except requests.exceptions.ConnectionError:
        return False, "network_error"
    except Exception as e:
        return False, f"error: {e}"


def _prompt_and_validate_token() -> tuple[str | None, bool]:
    try:
        token = getpass.getpass("  Paste your bot token (hidden): ").strip()
    except KeyboardInterrupt:
        console.print()
        raise

    if not token:
        _error("Token cannot be empty.")
        return None, True

    with Progress(
        SpinnerColumn(spinner_name="dots", style="blue"),
        TextColumn("[blue]Validating token with Telegram…[/blue]"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("validate", total=None)
        ok, result_label = _validate_token(token)

    if result_label == "network_error":
        _warn("Could not reach Telegram (network error). Skipping validation.")
        if Confirm.ask("  Save token anyway?", default=True):
            return token, False
        return None, True

    if ok:
        _success(f"Bot validated: [bold green]{result_label}[/bold green]")
        return token, False

    _error(f"Invalid token: {result_label}")
    return None, True


def step_telegram_bot(env: dict[str, str]) -> dict[str, str]:
    _step_header(1, "Telegram Bot Token")

    existing = env.get("TELEGRAM_BOT_TOKEN", "")
    if existing:
        bot_display = f"{existing[:10]}..."
        _already("TELEGRAM_BOT_TOKEN", bot_display)
        if not Confirm.ask("  Change this token?", default=False):
            return env

    console.print(Panel(
        "[bold]To create a Telegram bot:[/bold]\n\n"
        "  1. Open Telegram and message [bold cyan]@BotFather[/bold cyan]\n"
        "  2. Send [bold]/newbot[/bold] and follow the prompts\n"
        "  3. Copy the [bold]API token[/bold] it gives you\n\n"
        "[dim]The token looks like: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz[/dim]",
        border_style="blue",
        title="[blue]Instructions[/blue]",
        padding=(1, 2),
    ))

    for attempt in range(1, 4):
        if attempt > 1:
            _warn(f"Attempt {attempt}/3")
        token, should_continue = _prompt_and_validate_token()
        if token:
            env["TELEGRAM_BOT_TOKEN"] = token
            return env
        if not should_continue or attempt == 3:
            if attempt == 3:
                _warn("Max attempts reached. Skipping bot token setup.")
            return env

    return env


def step_telegram_id(env: dict[str, str]) -> dict[str, str]:
    _step_header(2, "Your Telegram User ID")

    existing = env.get("ALLOWED_USER_IDS", "")
    if existing:
        _already("ALLOWED_USER_IDS", existing)
        if not Confirm.ask("  Change your Telegram ID?", default=False):
            return env

    console.print(Panel(
        "[bold]How to find your Telegram user ID:[/bold]\n\n"
        "  1. Message [bold cyan]@userinfobot[/bold cyan] on Telegram\n"
        "  2. It will reply with your numeric user ID\n\n"
        "[dim]Example: 123456789[/dim]",
        border_style="blue",
        title="[blue]Instructions[/blue]",
        padding=(1, 2),
    ))

    while True:
        try:
            user_id = Prompt.ask("  Your Telegram user ID").strip()
        except KeyboardInterrupt:
            console.print()
            raise

        if not user_id:
            _error("User ID cannot be empty.")
            continue
        if not user_id.lstrip("-").isdigit():
            _error("User ID must be numeric (digits only).")
            continue

        env["ALLOWED_USER_IDS"] = user_id
        _success(f"User ID set: [bold green]{user_id}[/bold green]")
        return env


def _prompt_api_key(env: dict[str, str]) -> dict[str, str]:
    try:
        api_key = getpass.getpass("  Paste your ANTHROPIC_API_KEY (hidden): ").strip()
    except KeyboardInterrupt:
        console.print()
        raise
    if not api_key:
        _warn("No key entered. Run 'indieclaw setup-token' to authenticate later.")
        return env
    env["ANTHROPIC_API_KEY"] = api_key
    _success("API key saved.")
    return env


def _do_claude_login() -> None:
    console.print()
    _info("Opening browser for Claude.ai login…")
    try:
        subprocess.run(["claude", "auth", "login"], check=True)
        _success("Logged in with Claude account.")
    except FileNotFoundError:
        _error("[bold]claude[/bold] CLI not found. The SDK should have bundled it.")
        _warn("Run 'indieclaw setup-token' after installation to retry.")
    except subprocess.CalledProcessError:
        _error("Login failed or was cancelled.")
        _warn("Run 'indieclaw setup-token' to retry.")


def _ask_auth_method() -> str | None:
    try:
        return questionary.select(
            "How would you like to authenticate?",
            choices=[
                questionary.Choice("Paste an API key", value="key"),
                questionary.Choice("Login with Claude account (opens browser)", value="login"),
            ],
            style=questionary.Style([
                ("selected", "fg:cyan bold"),
                ("pointer", "fg:cyan bold"),
                ("question", "fg:blue bold"),
            ]),
        ).ask()
    except KeyboardInterrupt:
        console.print()
        raise


def step_claude_auth(env: dict[str, str]) -> dict[str, str]:
    _step_header(3, "Claude Authentication")

    existing = env.get("ANTHROPIC_API_KEY", "")
    if existing:
        _already("ANTHROPIC_API_KEY", existing[:12] + "…")
        if not Confirm.ask("  Change this API key?", default=False):
            return env

    console.print(Panel(
        "[bold]How to authenticate with Claude:[/bold]\n\n"
        "  [bold cyan]1. API key[/bold cyan]  — Paste an Anthropic API key\n"
        "              [dim](get one at console.anthropic.com/settings/keys)[/dim]\n\n"
        "  [bold cyan]2. Login[/bold cyan]    — Sign in with your Claude.ai account\n"
        "              [dim](Claude Pro / Max / Team subscription)[/dim]",
        border_style="blue",
        title="[blue]Instructions[/blue]",
        padding=(1, 2),
    ))

    choice = _ask_auth_method()
    if choice is None:
        _warn("Skipping Claude authentication. Run 'indieclaw setup-token' later.")
    elif choice == "key":
        env = _prompt_api_key(env)
    else:
        _do_claude_login()
    return env


_SECRET_KEYS = {
    "TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    "GROQ_API_KEY", "LITELLM_API_KEY",
}

_DISPLAY_ORDER = [
    "TELEGRAM_BOT_TOKEN", "ALLOWED_USER_IDS", "LITELLM_MODEL",
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY",
    "LITELLM_API_KEY", "OLLAMA_BASE_URL",
]


def _mask(v: str, n: int = 10) -> str:
    return "***" if len(v) <= n else v[:n] + "…"


def _build_summary_table(env: dict[str, str]) -> Table:
    summary = Table(show_header=False, border_style="dim green", box=None, padding=(0, 2))
    summary.add_column("Key", style="dim", min_width=24)
    summary.add_column("Value", style="bold")
    shown = set()
    for key in _DISPLAY_ORDER:
        if key in env:
            val = _mask(env[key]) if key in _SECRET_KEYS else env[key]
            summary.add_row(key, f"[green]{val}[/green]")
            shown.add(key)
    for key, val in env.items():
        if key not in shown:
            display_val = _mask(val) if key in _SECRET_KEYS else val
            summary.add_row(key, f"[green]{display_val}[/green]")
    return summary


def _print_summary(env: dict[str, str], workspace_home: Path) -> None:
    console.print()
    console.print(Rule(style="green"))
    console.print()
    console.print(Panel(
        _build_summary_table(env),
        title="[bold green]✓  Setup Complete[/bold green]",
        subtitle=f"[dim]{workspace_home / '.env'}[/dim]",
        border_style="green",
        padding=(1, 2),
    ))
    console.print()
    console.print(Panel(
        "Start your agent:\n\n  [bold cyan]indieclaw start[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))
    console.print()


def _patch_cron_deliver_to(env: dict[str, str]) -> None:
    from . import workspace
    user_id = env.get("ALLOWED_USER_IDS", "").split(",")[0].strip()
    if not user_id or not workspace.CRONS.exists():
        return
    try:
        crons_data = yaml.safe_load(workspace.CRONS.read_text()) or {}
        patched = False
        for job in crons_data.get("jobs", []):
            if not job.get("deliver_to"):
                job["deliver_to"] = user_id
                patched = True
        if patched:
            workspace.CRONS.write_text(yaml.dump(crons_data, default_flow_style=False, allow_unicode=True))
    except (OSError, ValueError, KeyError):
        pass


_RTK_VERSION = "v0.34.3"


def _rtk_asset() -> str | None:
    arch = platform.machine()
    os_name = platform.system().lower()
    if os_name == "linux":
        suffix = "unknown-linux-musl" if arch == "x86_64" else "unknown-linux-gnu"
        return f"rtk-{arch}-{suffix}.tar.gz"
    if os_name == "darwin":
        return f"rtk-{arch}-apple-darwin.tar.gz"
    return None


def _rtk_install(rtk_bin: Path) -> bool:
    asset = _rtk_asset()
    if not asset:
        _warn("rtk auto-install not supported on this platform. Install manually: https://github.com/rtk-ai/rtk")
        return False
    _info(f"Downloading rtk {_RTK_VERSION}…")
    url = f"https://github.com/rtk-ai/rtk/releases/download/{_RTK_VERSION}/{asset}"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tar_path = Path(tmpdir) / "rtk.tar.gz"
            urllib.request.urlretrieve(url, tar_path)
            with tarfile.open(tar_path) as tf:
                safe_members = [m for m in tf.getmembers() if not m.name.startswith(("/", ".."))]
                tf.extractall(tmpdir, members=safe_members)
            candidates = [f for f in Path(tmpdir).rglob("rtk") if f.is_file() and f.name == "rtk"]
            if not candidates:
                _warn("Could not find rtk binary in archive.")
                return False
            rtk_bin.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidates[0], rtk_bin)
            rtk_bin.chmod(0o755)
        _success("rtk installed")
        return True
    except Exception as e:
        _warn(f"rtk download failed: {e}")
        return False


def _rtk_configure_hook(rtk_exe: str) -> bool:
    result = subprocess.run([rtk_exe, "init", "-g", "--auto-patch"], capture_output=True, text=True)
    if result.returncode == 0:
        _success("rtk hook configured in ~/.claude/settings.json")
        return True
    # auto-patch failed — patch manually
    settings_path = Path.home() / ".claude/settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(settings_path.read_text()) if settings_path.exists() else {}
        hook_script = str(Path.home() / ".claude/hooks/rtk-rewrite.sh")
        pre = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
        if not any(isinstance(h, dict) and h.get("matcher") == "Bash" for h in pre):
            pre.append({"matcher": "Bash", "hooks": [{"type": "command", "command": hook_script}]})
        settings_path.write_text(json.dumps(data, indent=2))
        _success("rtk hook configured manually in ~/.claude/settings.json")
        return True
    except Exception as e:
        _warn(f"Could not patch settings.json: {e}")
        return False


def setup_rtk() -> bool:
    """Install rtk and configure its Claude Code PreToolUse hook. Returns True on success."""
    rtk_bin = Path.home() / ".local/bin/rtk"
    if shutil.which("rtk"):
        _success(f"rtk already installed ({shutil.which('rtk')})")
    elif not _rtk_install(rtk_bin):
        return False
    rtk_exe = shutil.which("rtk") or str(rtk_bin)
    return _rtk_configure_hook(rtk_exe)


def run() -> None:
    from . import workspace

    console.print(Panel(
        Text(BANNER.strip(), style="bold cyan", justify="center"),
        subtitle="[dim]Personal AI Agent Setup Wizard[/dim]",
        border_style="cyan",
        padding=(0, 4),
    ))

    console.print()
    console.print(
        "  Welcome! This wizard will configure your IndieClaw agent.\n"
        "  [dim]Press Ctrl+C at any time — partial progress will be saved.[/dim]"
    )
    console.print()

    workspace.init()
    _info(f"Workspace: [bold]{workspace.HOME}[/bold]")

    env_path = workspace.HOME / ".env"
    env = _read_env(env_path)

    steps = [
        step_telegram_bot,
        step_telegram_id,
        step_claude_auth,
    ]

    completed = 0
    try:
        for step_fn in steps:
            env = step_fn(env)
            completed += 1
            _write_env(env_path, env)
    except KeyboardInterrupt:
        console.print()
        _warn("Setup interrupted. Saving partial configuration…")
        _write_env(env_path, env)
        console.print(f"  [dim]Saved to {env_path}. Run [bold]indieclaw setup[/bold] to continue.[/dim]")
        console.print()
        sys.exit(0)

    _patch_cron_deliver_to(env)
    _print_summary(env, workspace.HOME)
    _install_systemd_service(workspace.HOME)
    _install_watchdog(workspace.HOME)
    console.print()
    console.print(Rule("Token Optimizer", style="dim"))
    setup_rtk()
