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
from dotenv import dotenv_values

from .setup_services import install_systemd_service as _install_systemd_service
from .setup_services import install_watchdog as _install_watchdog


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


def step_telegram_bot(env: dict[str, str]) -> dict[str, str]:
    print("\n--- Step 1/3: Telegram Bot Token ---\n")
    existing = env.get("TELEGRAM_BOT_TOKEN", "")
    if existing:
        print(f"  Already configured: {existing[:10]}...")
        if input("  Change this token? [y/N] ").strip().lower() != "y":
            return env
    print("  Create a bot: message @BotFather on Telegram, send /newbot, copy the token.\n")
    for attempt in range(1, 4):
        if attempt > 1:
            print(f"  Attempt {attempt}/3")
        try:
            token = getpass.getpass("  Paste your bot token (hidden): ").strip()
        except KeyboardInterrupt:
            print()
            raise
        if not token:
            print("  Error: Token cannot be empty.")
            continue
        print("  Validating token...")
        try:
            resp = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
            data = resp.json()
            ok, label = data.get("ok"), data.get("description", "Invalid token")
            if ok:
                label = f"@{data['result'].get('username', '?')}"
        except requests.exceptions.ConnectionError:
            ok, label = False, "network_error"
        except Exception as e:
            ok, label = False, str(e)
        if label == "network_error":
            print("  Warning: Could not reach Telegram. Skipping validation.")
            if input("  Save token anyway? [Y/n] ").strip().lower() != "n":
                env["TELEGRAM_BOT_TOKEN"] = token
                return env
            continue
        if ok:
            print(f"  OK: Bot validated: {label}")
            env["TELEGRAM_BOT_TOKEN"] = token
            return env
        print(f"  Error: Invalid token: {label}")
    print("  Max attempts reached. Skipping bot token setup.")
    return env


def step_telegram_id(env: dict[str, str]) -> dict[str, str]:
    print("\n--- Step 2/3: Your Telegram User ID ---\n")
    existing = env.get("ALLOWED_USER_IDS", "")
    if existing:
        print(f"  Already configured: {existing}")
        if input("  Change your Telegram ID? [y/N] ").strip().lower() != "y":
            return env
    print("  Message @userinfobot on Telegram to get your numeric user ID.\n")
    while True:
        try:
            user_id = input("  Your Telegram user ID: ").strip()
        except KeyboardInterrupt:
            print()
            raise
        if not user_id:
            print("  Error: User ID cannot be empty.")
            continue
        if not user_id.lstrip("-").isdigit():
            print("  Error: User ID must be numeric.")
            continue
        env["ALLOWED_USER_IDS"] = user_id
        print(f"  OK: User ID set: {user_id}")
        return env


def _prompt_api_key(env: dict[str, str]) -> dict[str, str]:
    try:
        api_key = getpass.getpass("  Paste your ANTHROPIC_API_KEY (hidden): ").strip()
    except KeyboardInterrupt:
        print()
        raise
    if not api_key:
        print("  Warning: No key entered. Run 'indieclaw setup-token' later.")
        return env
    env["ANTHROPIC_API_KEY"] = api_key
    print("  OK: API key saved.")
    return env


def _do_claude_login() -> None:
    print("\n  Opening browser for Claude.ai login...")
    try:
        subprocess.run(["claude", "auth", "login"], check=True)
        print("  OK: Logged in with Claude account.")
    except FileNotFoundError:
        print("  Error: claude CLI not found. Run 'indieclaw setup-token' later.")
    except subprocess.CalledProcessError:
        print("  Error: Login failed. Run 'indieclaw setup-token' to retry.")


def step_claude_auth(env: dict[str, str]) -> dict[str, str]:
    print("\n--- Step 3/3: Claude Authentication ---\n")
    existing = env.get("ANTHROPIC_API_KEY", "")
    if existing:
        print(f"  Already configured: {existing[:12]}...")
        if input("  Change this API key? [y/N] ").strip().lower() != "y":
            return env
    try:
        choice = questionary.select(
            "How would you like to authenticate?",
            choices=[
                questionary.Choice("Paste an API key (console.anthropic.com/settings/keys)", value="key"),
                questionary.Choice("Login with Claude account (opens browser)", value="login"),
            ],
        ).ask()
    except KeyboardInterrupt:
        print()
        raise
    if choice is None:
        print("  Skipping. Run 'indieclaw setup-token' later.")
    elif choice == "key":
        env = _prompt_api_key(env)
    else:
        _do_claude_login()
    return env


_RTK_VERSION = "v0.34.3"


def _rtk_install(rtk_bin: Path) -> bool:
    arch, os_name = platform.machine(), platform.system().lower()
    if os_name == "linux":
        asset = f"rtk-{arch}-{'unknown-linux-musl' if arch == 'x86_64' else 'unknown-linux-gnu'}.tar.gz"
    elif os_name == "darwin":
        asset = f"rtk-{arch}-apple-darwin.tar.gz"
    else:
        print("  Warning: rtk auto-install not supported on this platform.")
        return False
    print(f"  Downloading rtk {_RTK_VERSION}...")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tar_path = Path(tmpdir) / "rtk.tar.gz"
            urllib.request.urlretrieve(
                f"https://github.com/rtk-ai/rtk/releases/download/{_RTK_VERSION}/{asset}", tar_path)
            with tarfile.open(tar_path) as tf:
                tf.extractall(tmpdir, members=[m for m in tf.getmembers() if not m.name.startswith(("/", ".."))])
            candidates = [f for f in Path(tmpdir).rglob("rtk") if f.is_file() and f.name == "rtk"]
            if not candidates:
                print("  Warning: Could not find rtk binary in archive.")
                return False
            rtk_bin.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidates[0], rtk_bin)
            rtk_bin.chmod(0o755)
        print("  OK: rtk installed")
        return True
    except Exception as e:
        print(f"  Warning: rtk download failed: {e}")
        return False


def _rtk_patch_hook_path() -> None:
    hook = Path.home() / ".claude/hooks/rtk-rewrite.sh"
    if not hook.exists():
        return
    content = hook.read_text()
    inject = 'export PATH="$HOME/.local/bin:$PATH"\n'
    if inject not in content:
        lines = content.splitlines(keepends=True)
        lines.insert(1, inject)
        hook.write_text("".join(lines))


def _rtk_configure_hook(rtk_exe: str) -> bool:
    if subprocess.run([rtk_exe, "init", "-g", "--auto-patch"], capture_output=True, text=True).returncode == 0:
        _rtk_patch_hook_path()
        print("  OK: rtk hook configured")
        return True
    settings_path = Path.home() / ".claude/settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(settings_path.read_text()) if settings_path.exists() else {}
        hook_script = str(Path.home() / ".claude/hooks/rtk-rewrite.sh")
        pre = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
        if not any(isinstance(h, dict) and h.get("matcher") == "Bash" for h in pre):
            pre.append({"matcher": "Bash", "hooks": [{"type": "command", "command": hook_script}]})
        settings_path.write_text(json.dumps(data, indent=2))
        _rtk_patch_hook_path()
        print("  OK: rtk hook configured (manual fallback)")
        return True
    except Exception as e:
        print(f"  Warning: Could not patch settings.json: {e}")
        return False


def setup_rtk() -> bool:
    """Install rtk and configure its Claude Code PreToolUse hook."""
    rtk_bin = Path.home() / ".local/bin/rtk"
    if shutil.which("rtk"):
        print(f"  OK: rtk already installed ({shutil.which('rtk')})")
    elif not _rtk_install(rtk_bin):
        return False
    return _rtk_configure_hook(shutil.which("rtk") or str(rtk_bin))


def run() -> None:
    from . import workspace
    print("\n  IndieClaw Setup\n  Press Ctrl+C at any time -- partial progress will be saved.\n")
    workspace.init()
    print(f"  Workspace: {workspace.HOME}")
    env_path = workspace.HOME / ".env"
    env = _read_env(env_path)
    try:
        for step_fn in [step_telegram_bot, step_telegram_id, step_claude_auth]:
            env = step_fn(env)
            _write_env(env_path, env)
    except KeyboardInterrupt:
        print("\n  Setup interrupted. Saving partial configuration...")
        _write_env(env_path, env)
        print(f"  Saved to {env_path}. Run 'indieclaw setup' to continue.")
        sys.exit(0)
    print(f"\n  Setup complete. Config saved to {env_path}\n  Start your agent: indieclaw start\n")
    _install_systemd_service(workspace.HOME)
    _install_watchdog(workspace.HOME)
    setup_rtk()
