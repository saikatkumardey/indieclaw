from __future__ import annotations

import asyncio
import os
import shutil
import sys

# Clear CLAUDECODE early — if set, claude-agent-sdk subprocess refuses to start
# with "cannot be launched inside another Claude Code session".
os.environ.pop("CLAUDECODE", None)

import typer
from dotenv import load_dotenv

app = typer.Typer(name="indieclaw", help="Your personal AI agent.", add_completion=False)


def version_callback(value: bool) -> None:
    if value:
        from .version import local_version
        typer.echo(f"indieclaw {local_version()}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: bool = typer.Option(
        None,
        "--version",
        "-V",
        callback=version_callback,
        is_eager=True,
        help="Show version.",
    ),
) -> None:
    pass


@app.command()
def setup() -> None:
    """Interactive setup wizard. Run this first."""
    from .setup import run
    run()


def _setup_token_prompt_choice(console):
    import questionary
    from rich.panel import Panel

    console.print()
    console.print(Panel(
        "[bold]IndieClaw uses [cyan]claude-agent-sdk[/cyan] to power your AI agent.[/bold]\n\n"
        "You need to authenticate with Claude. Choose how:\n\n"
        "  [bold cyan]1. API key[/bold cyan]   — Paste an [link=https://console.anthropic.com/settings/keys]Anthropic API key[/link] "
        "(works with any Anthropic account)\n"
        "  [bold cyan]2. Login[/bold cyan]     — Sign in with your Claude.ai account "
        "[dim](Claude Pro / Max / Team subscription)[/dim]",
        title="[blue]Claude Authentication[/blue]",
        border_style="blue",
        padding=(1, 2),
    ))
    console.print()

    try:
        return questionary.select(
            "How would you like to authenticate?",
            choices=[
                questionary.Choice("Paste an API key  (console.anthropic.com/settings/keys)", value="key"),
                questionary.Choice("Login with Claude account  (Claude Pro / Max subscription)", value="login"),
            ],
            style=questionary.Style([
                ("selected", "fg:cyan bold"),
                ("pointer", "fg:cyan bold"),
                ("question", "fg:blue bold"),
            ]),
        ).ask()
    except KeyboardInterrupt:
        console.print()
        raise typer.Exit(0)


def _setup_token_api_key(console, env, env_path):
    import getpass

    import questionary

    from .setup import _write_env

    existing = env.get("ANTHROPIC_API_KEY", "")
    if existing:
        console.print(f"\n  [dim]Current key:[/dim] [green]{existing[:12]}…[/green]")
        try:
            if not questionary.confirm("Replace it?", default=False).ask():
                console.print("  [dim]Keeping existing key.[/dim]")
                raise typer.Exit(0)
        except KeyboardInterrupt:
            raise typer.Exit(0)

    console.print()
    try:
        api_key = getpass.getpass("  Paste your ANTHROPIC_API_KEY (hidden): ").strip()
    except KeyboardInterrupt:
        console.print()
        raise typer.Exit(0)

    if not api_key:
        console.print("  [yellow]No key entered — nothing saved.[/yellow]")
        raise typer.Exit(1)

    env["ANTHROPIC_API_KEY"] = api_key
    _write_env(env_path, env)
    console.print(f"\n  [bold green]✓[/bold green]  API key saved to [dim]{env_path}[/dim]")


def _setup_token_login(console):
    import subprocess
    console.print()
    console.print("  [dim]Opening browser for Claude.ai login…[/dim]\n")
    try:
        subprocess.run(["claude", "auth", "login"], check=True)
    except FileNotFoundError:
        console.print("  [red]✗[/red]  [bold]claude[/bold] CLI not found. The SDK should have bundled it.")
        console.print("  Try: [bold]pip install --upgrade claude-agent-sdk[/bold]")
        raise typer.Exit(1)
    except subprocess.CalledProcessError:
        console.print("  [red]✗[/red]  Login failed or was cancelled.")
        raise typer.Exit(1)
    console.print("\n  [bold green]✓[/bold green]  Logged in. IndieClaw will use your Claude subscription.")


@app.command(name="setup-token")
def setup_token() -> None:
    """Configure Claude authentication (API key or Claude.ai subscription login)."""
    from rich.console import Console
    from rich.rule import Rule

    from . import workspace
    from .setup import _read_env

    workspace.init()
    console = Console()
    env_path = workspace.HOME / ".env"
    env = _read_env(env_path)

    choice = _setup_token_prompt_choice(console)
    if choice is None:
        raise typer.Exit(0)

    if choice == "key":
        _setup_token_api_key(console, env, env_path)
    else:
        _setup_token_login(console)

    console.print()
    console.print(Rule(style="green"))
    console.print("\n  [bold]Run your agent:[/bold]  [bold cyan]indieclaw start[/bold cyan]\n")


@app.command()
def update() -> None:
    """Pull latest indieclaw from GitHub and reinstall."""
    source = os.getenv("INDIECLAW_SOURCE", "git+https://github.com/saikatkumardey/indieclaw")
    typer.echo(f"Updating from {source}...")
    import subprocess
    result = subprocess.run(["uv", "tool", "install", "--upgrade", source], text=True)
    if result.returncode != 0:
        typer.echo("Update failed.")
        raise typer.Exit(1)
    typer.echo("Updated. Run 'indieclaw start' to restart.")


@app.command()
def doctor() -> None:
    """Check workspace health: files, auth, tools, and state."""
    from .doctor import run
    raise typer.Exit(run())


def _preflight_checks() -> bool:
    from . import workspace
    env_path = workspace.HOME / ".env"
    if not env_path.exists():
        typer.echo("No .env found. Run `indieclaw setup` first.")
        return False
    if not os.getenv("TELEGRAM_BOT_TOKEN", ""):
        typer.echo("No Telegram bot token. Run `indieclaw setup` to configure.")
        return False
    if not os.getenv("ALLOWED_USER_IDS", "").strip():
        typer.echo("ALLOWED_USER_IDS is not set. Run `indieclaw setup` to configure your Telegram user ID.")
        return False
    return True


async def _post_init(app, scheduler, commands) -> None:
    from telegram import BotCommand
    await app.bot.set_my_commands([BotCommand(name, desc) for name, _, desc in commands if desc is not None])
    from .scheduler import set_main_loop
    set_main_loop(asyncio.get_running_loop())
    scheduler.start()
    from .auth import default_chat_id
    default_chat = default_chat_id()
    if default_chat:
        from .version import local_version
        ver = local_version()
        try:
            await app.bot.send_message(chat_id=default_chat, text=f"\u2705 IndieClaw v{ver} online")
        except (OSError, RuntimeError):
            pass  # best-effort startup notification; bot may not be ready


async def _post_shutdown(app, scheduler) -> None:
    from loguru import logger
    logger.info("Shutdown: cleaning up...")
    try:
        from .browser import BrowserManager
        await BrowserManager.get().close_all()
    except Exception:
        pass
    scheduler.shutdown(wait=False)
    from .daemon import delete_pid
    delete_pid()
    logger.info("IndieClaw stopped.")


@app.command()
def chat() -> None:
    """Launch the interactive TUI chat. Do not run alongside 'indieclaw start'."""
    from dotenv import load_dotenv

    from . import workspace
    workspace.init()
    load_dotenv(workspace.HOME / ".env", override=True)
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
    os.environ.setdefault("ALLOWED_USER_IDS", "123")
    from .tui import IndieClawApp  # deferred — env vars must be set first
    IndieClawApp().run()


def _start_daemon() -> None:
    from . import workspace
    from .daemon import is_running, write_pid

    running, pid = is_running()
    if running:
        typer.echo(f"IndieClaw is already running (PID {pid}). Use 'indieclaw restart' to restart.")
        raise typer.Exit(1)

    import subprocess
    exe = shutil.which("indieclaw") or sys.argv[0]
    log_path = workspace.LOG_FILE
    with open(log_path, "a") as log_fh:
        proc = subprocess.Popen(
            [exe, "start", "--foreground"],
            stdout=log_fh, stderr=log_fh, start_new_session=True,
        )
    write_pid(proc.pid)
    typer.echo(f"IndieClaw started (PID {proc.pid}). Run 'indieclaw logs' to view output.")


def _ensure_path() -> None:
    _user_bin_dirs = [
        os.path.expanduser("~/.npm-global/bin"),
        os.path.expanduser("~/.local/bin"),
    ]
    current_path = os.environ.get("PATH", "")
    missing = [d for d in _user_bin_dirs if d not in current_path.split(os.pathsep)]
    if missing:
        os.environ["PATH"] = os.pathsep.join(missing) + os.pathsep + current_path


def _setup_logging() -> None:
    from loguru import logger

    from . import workspace
    logger.remove()
    logger.add(
        sys.stderr, level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
    )
    logger.add(
        workspace.LOG_FILE, level="INFO",
        format="{time:HH:mm:ss} | {level:<7} | {name} - {message}",
        rotation="10 MB", retention=3,
    )


def _register_handlers(bot) -> list:
    from telegram.ext import (
        CallbackQueryHandler,
        CommandHandler,
        MessageHandler,
        MessageReactionHandler,
        filters,
    )

    from . import handlers as h

    commands = [
        ("start",   h.on_start,   "Wake the bot"),
        ("help",    h.on_help,    "Show available commands"),
        ("status",  h.on_status,  "Show model, workspace, tool counts"),
        ("model",   h.on_model,   "Switch Claude model (opus/sonnet/haiku)"),
        ("models",  h.on_model,   None),
        ("reset",   h.on_reset,   "Clear conversation history"),
        ("stop",    h.on_stop,    "Cancel the current agent run"),
        ("crons",   h.on_crons,   "List scheduled jobs"),
        ("restart", h.on_restart, "Restart the bot process"),
        ("update",  h.on_update,  "Update indieclaw and restart"),
        ("btw",     h.on_btw,     "Ask a side question (no history)"),
        ("effort",  h.on_effort,  "Set thinking effort level"),
        ("efforts", h.on_effort, None),
        ("cc",      h.on_cc,      "Start a live Claude Code session"),
        ("streaming", h.on_streaming, "Toggle response streaming"),
    ]
    for name, handler, _ in commands:
        bot.add_handler(CommandHandler(name, handler))
    bot.add_handler(CallbackQueryHandler(h.on_model_callback, pattern="^model:"))
    bot.add_handler(CallbackQueryHandler(h.on_effort_callback, pattern="^effort:"))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, h.on_message))
    bot.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE & filters.TEXT & ~filters.COMMAND, h.on_message))
    bot.add_handler(MessageHandler(filters.PHOTO, h.on_photo))
    bot.add_handler(MessageHandler(filters.VOICE, h.on_voice))
    bot.add_handler(MessageHandler(filters.VIDEO | filters.ANIMATION, h.on_video))
    bot.add_handler(MessageHandler(filters.Document.ALL, h.on_document))
    bot.add_handler(MessageReactionHandler(h.on_reaction))
    return commands


def _handle_bot_error(e: Exception) -> None:
    err = str(e).lower()
    if "unauthorized" in err or "token" in err:
        typer.echo("Bot token rejected by Telegram. Check TELEGRAM_BOT_TOKEN in your .env.")
    elif "network" in err or "connect" in err or "timeout" in err:
        typer.echo(f"Network error connecting to Telegram: {e}\nCheck your internet connection and try again.")
    else:
        typer.echo(f"Failed to start bot: {e}")


@app.command()
def start(
    foreground: bool = typer.Option(False, "--foreground", "-f", help="Run in foreground (blocking)."),
) -> None:
    """Start the Telegram bot (daemonizes by default)."""
    from . import workspace
    workspace.init()

    if not foreground:
        _start_daemon()
        return

    from .daemon import is_running, write_pid
    running, pid = is_running()
    if running and pid != os.getpid():
        typer.echo(f"Another IndieClaw instance is running (PID {pid}). Exiting.")
        raise typer.Exit(1)
    write_pid(os.getpid())

    _ensure_path()
    load_dotenv(workspace.HOME / ".env", override=True)
    from .config import Config
    os.environ["INDIECLAW_MODEL"] = Config.load().get("model")
    _setup_logging()

    if not _preflight_checks():
        raise typer.Exit(1)

    from loguru import logger
    from telegram.ext import ApplicationBuilder

    from .scheduler import setup_scheduler

    try:
        bot = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN", "")).concurrent_updates(True).build()
        commands = _register_handlers(bot)
        scheduler = setup_scheduler()
        bot.post_init = lambda app: _post_init(app, scheduler, commands)
        bot.post_shutdown = lambda app: _post_shutdown(app, scheduler)
        logger.info("IndieClaw running.")
        bot.run_polling()
    except Exception as e:
        _handle_bot_error(e)
        raise typer.Exit(1)


def _systemd_active() -> tuple[bool, bool]:
    """Check if indieclaw is running via systemd. Returns (active, is_system_level)."""
    import subprocess
    # Check system-level first (preferred)
    result = subprocess.run(
        ["systemctl", "is-active", "indieclaw.service"],
        capture_output=True, text=True,
    )
    if result.stdout.strip() in ("active", "activating"):
        return True, True
    # Fall back to user-level
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "indieclaw.service"],
        capture_output=True, text=True,
    )
    if result.stdout.strip() in ("active", "activating"):
        return True, False
    return False, False


def _systemctl_cmd(is_system: bool) -> list[str]:
    """Return the base systemctl command for the detected service level."""
    if is_system:
        return ["sudo", "systemctl"]
    return ["systemctl", "--user"]


@app.command()
def stop() -> None:
    """Stop the running IndieClaw daemon."""
    active, is_system = _systemd_active()
    if active:
        import subprocess
        cmd = [*_systemctl_cmd(is_system), "stop", "indieclaw.service"]
        subprocess.run(cmd, check=True)
        typer.echo(f"Stopped via {'system' if is_system else 'user'} systemd.")
        return

    from .daemon import read_pid, stop_daemon
    pid = read_pid()  # for display; stop_daemon re-checks liveness
    if stop_daemon():
        typer.echo(f"Stopped (PID {pid})." if pid else "Stopped.")
    else:
        typer.echo("IndieClaw is not running.")
        raise typer.Exit(1)


@app.command()
def restart() -> None:
    """Restart the IndieClaw daemon."""
    active, is_system = _systemd_active()
    if active:
        import subprocess
        cmd = [*_systemctl_cmd(is_system), "restart", "indieclaw.service"]
        level = "system" if is_system else "user"
        typer.echo(f"Restarting via {level} systemd...")
        subprocess.run(cmd, check=True)
        typer.echo(f"Restarted via {level} systemd.")
        return

    from .daemon import is_running, stop_daemon
    running, pid = is_running()
    if running:
        typer.echo(f"Stopping IndieClaw (PID {pid})...")
        if not stop_daemon():
            typer.echo("Failed to stop IndieClaw. Aborting restart.")
            raise typer.Exit(1)
        typer.echo(f"Stopped (PID {pid}).")
    start(foreground=False)


@app.command()
def logs(
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream log file live."),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show."),
) -> None:
    """Show IndieClaw logs."""
    from . import workspace
    log_path = workspace.LOG_FILE
    if not log_path.exists():
        typer.echo("No log file found. Has IndieClaw been started?")
        raise typer.Exit(1)

    if not follow:
        import collections
        with open(log_path) as f:
            last = collections.deque(f, maxlen=lines)
        typer.echo("".join(last), nl=False)
        return

    # Follow mode: seek to end then stream new lines
    import time as _time
    with open(log_path) as f:
        f.seek(0, 2)  # seek to end
        try:
            while True:
                line = f.readline()
                if line:
                    typer.echo(line, nl=False)
                else:
                    _time.sleep(0.2)
        except KeyboardInterrupt:
            pass


def main() -> None:
    app()
