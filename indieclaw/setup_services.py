from __future__ import annotations

import getpass
import shutil
import subprocess
import sys
from pathlib import Path

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
        return subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _install_system_service(workspace_home: Path, binary: str) -> bool:
    content = _SYSTEM_SERVICE_TEMPLATE.format(
        workspace_home=workspace_home, indieclaw_binary=binary,
        user=getpass.getuser(), home=str(Path.home()))
    try:
        for cmd in [
            ["sudo", "tee", str(_SYSTEM_SERVICE_PATH)],
        ]:
            subprocess.run(cmd, input=content.encode(), check=True, capture_output=True, timeout=10)
        for cmd in [
            ["sudo", "systemctl", "daemon-reload"],
            ["sudo", "systemctl", "enable", "indieclaw.service"],
        ]:
            subprocess.run(cmd, check=True, capture_output=True, timeout=10)
        print("  OK: System service installed.")
        return True
    except (PermissionError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"  Warning: Could not install system service ({exc}).")
        return False

def _install_user_service(workspace_home: Path, binary: str) -> None:
    content = _USER_SERVICE_TEMPLATE.format(workspace_home=workspace_home, indieclaw_binary=binary)
    svc = Path.home() / ".config/systemd/user/indieclaw.service"
    try:
        svc.parent.mkdir(parents=True, exist_ok=True)
        svc.write_text(content)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True)
        subprocess.run(["systemctl", "--user", "enable", "indieclaw"], check=True, capture_output=True)
        print("  OK: Systemd user service installed.")
        try:
            subprocess.run(["loginctl", "enable-linger", getpass.getuser()], check=True, capture_output=True)
        except (PermissionError, subprocess.CalledProcessError):
            print(f"  Warning: Run: sudo loginctl enable-linger {getpass.getuser()}")
    except (PermissionError, OSError, subprocess.CalledProcessError) as exc:
        print(f"  Warning: Could not install user service ({exc}). Save unit to {svc}")

def install_systemd_service(workspace_home: Path) -> None:
    if not _SYSTEMD_RUNTIME.exists():
        return
    binary = shutil.which("indieclaw") or sys.executable
    if _has_sudo() and _install_system_service(workspace_home, binary):
        return
    _install_user_service(workspace_home, binary)


_WATCHDOG_DEST = Path("/usr/local/bin/indieclaw-watchdog")
_WATCHDOG_CRON = "*/10 * * * * /usr/local/bin/indieclaw-watchdog >> ~/.indieclaw/watchdog.log 2>&1"


def install_watchdog(workspace_home: Path) -> None:
    watchdog_src = Path(__file__).parent / "watchdog.sh"
    if not watchdog_src.exists():
        print("  Warning: watchdog.sh not found -- skipping.")
        return
    try:
        shutil.copy2(watchdog_src, _WATCHDOG_DEST)
        _WATCHDOG_DEST.chmod(0o755)
    except (PermissionError, OSError) as exc:
        print(f"  Warning: Could not install watchdog ({exc}).")
        return
    try:
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        current = existing.stdout if existing.returncode == 0 else ""
        filtered = "\n".join(line for line in current.splitlines() if "indieclaw-watchdog" not in line)
        new = (filtered.rstrip("\n") + "\n" + _WATCHDOG_CRON + "\n").lstrip("\n")
        proc = subprocess.run(["crontab", "-"], input=new, capture_output=True, text=True)
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, "crontab -", proc.stderr)
        print("  OK: Watchdog cron installed (every 10 min)")
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"  Warning: Could not install cron ({exc}). Add manually: {_WATCHDOG_CRON}")
