from __future__ import annotations

import getpass
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

console = Console()


def _success(msg: str) -> None:
    console.print(f"  [bold green]✓[/bold green]  {msg}")


def _warn(msg: str) -> None:
    console.print(f"  [bold yellow]⚠[/bold yellow]  {msg}")


_USER_SERVICE_TEMPLATE = """\
[Unit]
Description=IndieClaw Telegram AI Agent
After=network.target

[Service]
Type=simple
WorkingDirectory={workspace_home}
EnvironmentFile={workspace_home}/.env
ExecStart={indieclaw_binary} start --foreground
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""

_SYSTEM_SERVICE_TEMPLATE = """\
[Unit]
Description=IndieClaw Telegram AI Agent
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User={user}
Group={user}
WorkingDirectory={workspace_home}
EnvironmentFile={workspace_home}/.env
Environment=HOME={home}
Environment=PATH={home}/.local/bin:{home}/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart={indieclaw_binary} start --foreground
MemoryMax=2G
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

_SYSTEMD_RUNTIME = Path("/run/systemd/system")
_SYSTEM_SERVICE_PATH = Path("/etc/systemd/system/indieclaw.service")


def _has_sudo() -> bool:
    try:
        result = subprocess.run(
            ["sudo", "-n", "true"],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _install_system_service(workspace_home: Path, indieclaw_binary: str) -> bool:
    """Install a system-level service (survives SSH disconnects). Returns True on success."""
    user = getpass.getuser()
    home = str(Path.home())
    service_content = _SYSTEM_SERVICE_TEMPLATE.format(
        workspace_home=workspace_home,
        indieclaw_binary=indieclaw_binary,
        user=user,
        home=home,
    )
    try:
        subprocess.run(
            ["sudo", "tee", str(_SYSTEM_SERVICE_PATH)],
            input=service_content.encode(), check=True,
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["sudo", "systemctl", "daemon-reload"],
            check=True, capture_output=True, timeout=10,
        )
        subprocess.run(
            ["sudo", "systemctl", "enable", "indieclaw.service"],
            check=True, capture_output=True, timeout=10,
        )
        _success("System service installed (survives SSH disconnects, starts at boot).")
        return True
    except (PermissionError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        _warn(f"Could not install system-level service ({exc}).")
        return False


def _install_user_service(workspace_home: Path, indieclaw_binary: str) -> None:
    """Fallback: install a user-level service."""
    service_content = _USER_SERVICE_TEMPLATE.format(
        workspace_home=workspace_home,
        indieclaw_binary=indieclaw_binary,
    )
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_path = service_dir / "indieclaw.service"

    try:
        service_dir.mkdir(parents=True, exist_ok=True)
        service_path.write_text(service_content)
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["systemctl", "--user", "enable", "indieclaw"],
            check=True, capture_output=True,
        )
        _success("Systemd user service installed.")
        try:
            subprocess.run(
                ["loginctl", "enable-linger", getpass.getuser()],
                check=True, capture_output=True,
            )
        except (PermissionError, subprocess.CalledProcessError):
            _warn(
                "Could not enable linger (needs root). Run: sudo loginctl enable-linger "
                + getpass.getuser()
            )
            _warn("Without linger, the service will stop when you disconnect SSH.")
    except (PermissionError, OSError, subprocess.CalledProcessError) as exc:
        _warn(f"Could not install systemd user service ({exc}).")
        _warn(f"To install manually, save the following to {service_path}")
        _warn("then run: systemctl --user daemon-reload && systemctl --user enable indieclaw")
        console.print()
        console.print(Panel(
            service_content,
            title="[dim]indieclaw.service[/dim]",
            border_style="dim yellow",
            padding=(0, 2),
        ))


def install_systemd_service(workspace_home: Path) -> None:
    if not _SYSTEMD_RUNTIME.exists():
        return

    indieclaw_binary = shutil.which("indieclaw") or sys.executable

    # Prefer system-level service (reliable, survives SSH disconnects).
    # Fall back to user-level service if no sudo available.
    if _has_sudo() and _install_system_service(workspace_home, indieclaw_binary):
        return

    _install_user_service(workspace_home, indieclaw_binary)


_WATCHDOG_DEST = Path("/usr/local/bin/indieclaw-watchdog")
_WATCHDOG_CRON = "*/10 * * * * /usr/local/bin/indieclaw-watchdog >> ~/.indieclaw/watchdog.log 2>&1"


def install_watchdog(workspace_home: Path) -> None:
    watchdog_src = Path(__file__).parent / "watchdog.sh"
    if not watchdog_src.exists():
        _warn("watchdog.sh not found in package — skipping watchdog installation.")
        return

    try:
        shutil.copy2(watchdog_src, _WATCHDOG_DEST)
        _WATCHDOG_DEST.chmod(0o755)
    except (PermissionError, OSError) as exc:
        _warn(f"Could not install watchdog script ({exc}). Try: sudo cp {watchdog_src} {_WATCHDOG_DEST} && sudo chmod +x {_WATCHDOG_DEST}")
        return

    try:
        existing = subprocess.run(
            ["crontab", "-l"],
            capture_output=True, text=True,
        )
        current_crontab = existing.stdout if existing.returncode == 0 else ""
        filtered = "\n".join(
            line for line in current_crontab.splitlines()
            if "indieclaw-watchdog" not in line
        )
        new_crontab = (filtered.rstrip("\n") + "\n" + _WATCHDOG_CRON + "\n").lstrip("\n")
        proc = subprocess.run(
            ["crontab", "-"],
            input=new_crontab, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, "crontab -", proc.stderr)
        _success("Watchdog installed (system cron, every 10 min)")
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        _warn(f"Could not install cron entry ({exc}).")
        _warn(f"Add manually: {_WATCHDOG_CRON}")
