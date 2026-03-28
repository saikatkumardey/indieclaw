from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from claude_agent_sdk import tool

from . import workspace
from .auth import default_chat_id, is_allowed
from .tools import (
    _edit_telegram,
    _send_telegram,
    _send_telegram_file,
    _send_telegram_voice,
    _set_reaction,
    _text_to_voice,
)
from .tools_browser import browse, browser_click, browser_eval, browser_screenshot, browser_type
from .version import check_remote_version as _check_remote_version
from .version import get_update_summary as _get_update_summary
from .version import local_version as _local_version

_ALLOWED_SOURCE_PREFIX = "git+https://github.com/saikatkumardey/indieclaw"
_PROCESS_START_TIME = time.monotonic()


def _text(t: str) -> dict:
    return {"content": [{"type": "text", "text": t}]}


def _check_allowed(chat_id: str) -> dict | None:
    """Return error dict if chat_id is not allowed, else None."""
    if not is_allowed(chat_id):
        return _text(f"Error: chat_id {chat_id!r} is not in ALLOWED_USER_IDS.")
    return None


@tool(
    "telegram_send",
    "Send a new Telegram message. Each response should be a single new message. "
    "Do not send multiple messages for one response — consolidate into one.",
    {"chat_id": str, "message": str},
)
async def telegram_send(args: dict) -> dict:
    if err := _check_allowed(args["chat_id"]):
        return err
    text = await asyncio.to_thread(_send_telegram, args["chat_id"], args["message"])
    return _text(text)


@tool(
    "telegram_edit",
    "Edit an existing Telegram message by message_id. ONLY use for interim progress updates "
    "(e.g. updating a 'working on it...' placeholder). NEVER use to rewrite or refine a "
    "response you already sent — always send a new message instead.",
    {"chat_id": str, "message_id": str, "message": str},
)
async def telegram_edit(args: dict) -> dict:
    if err := _check_allowed(args["chat_id"]):
        return err
    try:
        message_id = int(args["message_id"])
    except (ValueError, TypeError):
        return _text(f"Error: invalid message_id {args.get('message_id')!r}")
    text = await asyncio.to_thread(_edit_telegram, args["chat_id"], message_id, args["message"])
    return _text(text)


_MIN_UPTIME_BEFORE_RESTART = 120  # seconds


@tool("self_restart", "Restart the indieclaw process in-place.", {})
async def self_restart(args: dict) -> dict:
    import signal
    uptime = time.monotonic() - _PROCESS_START_TIME
    if uptime < _MIN_UPTIME_BEFORE_RESTART:
        return _text(
            f"Restart blocked: process only started {uptime:.0f}s ago (minimum {_MIN_UPTIME_BEFORE_RESTART}s). "
            "Wait longer before restarting to avoid a restart loop."
        )
    if chat_id := default_chat_id():
        await asyncio.to_thread(_send_telegram, chat_id, "Restarting…")
    os.kill(os.getpid(), signal.SIGTERM)
    return _text("unreachable")


@tool("self_update", "Check for updates and install if a newer version is available.", {})
async def self_update(args: dict) -> dict:
    source = os.getenv("INDIECLAW_SOURCE", "git+https://github.com/saikatkumardey/indieclaw")
    if not source.startswith(_ALLOWED_SOURCE_PREFIX):
        return _text(f"Error: INDIECLAW_SOURCE {source!r} is not an allowed update URL.")

    old_version = _local_version()

    remote = await asyncio.to_thread(_check_remote_version, source)
    if remote and remote == old_version:
        return _text(f"Already on latest version (v{old_version}). No update needed.")

    result = await asyncio.to_thread(
        subprocess.run,
        ["uv", "tool", "install", "--upgrade", source],
        capture_output=True, text=True, timeout=120,
    )
    chat_id = default_chat_id()
    if result.returncode != 0:
        msg = f"Update failed:\n{result.stderr}"
        if chat_id:
            await asyncio.to_thread(_send_telegram, chat_id, msg)
        return _text(msg)

    summary = _get_update_summary(source, old_version)
    if chat_id:
        await asyncio.to_thread(_send_telegram, chat_id, f"Update successful. Restarting...\n\n{summary}")

    import signal
    os.kill(os.getpid(), signal.SIGTERM)
    return _text("unreachable")


@tool("telegram_send_file", "Send a local file (markdown, CSV, script, image, etc.) to a Telegram chat_id.", {"chat_id": str, "file_path": str})
async def telegram_send_file(args: dict) -> dict:
    if err := _check_allowed(args["chat_id"]):
        return err
    resolved = Path(args["file_path"]).resolve()
    try:
        resolved.relative_to(workspace.HOME.resolve())
    except ValueError:
        return _text(f"Error: file path {args['file_path']!r} is outside the workspace.")
    text = await asyncio.to_thread(_send_telegram_file, args["chat_id"], args["file_path"])
    return _text(text)


@tool(
    "update_config",
    "Update a runtime configuration value (max_turns, btw_model). Use /models command for main model changes.",
    {"key": str, "value": str},
)
async def update_config(args: dict) -> dict:
    from .config import Config

    _AGENT_EXCLUDED = {"model"}
    mutable = set(Config.DEFAULTS.keys()) - _AGENT_EXCLUDED
    key = args["key"]
    if key not in mutable:
        return _text(f"Error: Cannot set '{key}' via this tool. Use /models for model changes.")
    cfg = Config.load()
    expected_type = type(Config.DEFAULTS[key])
    try:
        raw = args["value"]
        if expected_type is bool:
            value = raw.lower() in ("true", "1", "yes")
        else:
            value = expected_type(raw)
        cfg.set(key, value)
    except (KeyError, TypeError, ValueError) as e:
        return _text(f"Error: {e}")
    return _text(f"Set {key} = {value}")


@tool("read_skill", "Read the instructions for a skill by name.", {"name": str})
async def read_skill_tool(args: dict) -> dict:
    from .skills import read_skill
    content = read_skill(args["name"], workspace.SKILLS_DIR)
    if content is None:
        return _text(f"Skill {args['name']!r} not found.")
    return _text(content)


async def _try_qmd_search(query_str: str) -> str | None:
    if not (shutil.which("qmd") and len(query_str) > 3):
        return None
    try:
        qmd_result = await asyncio.to_thread(
            subprocess.run,
            ["qmd", "search", query_str, "--limit", "10"],
            capture_output=True, text=True, timeout=30,
        )
        if qmd_result.returncode == 0 and qmd_result.stdout.strip():
            return f"[qmd results]\n{qmd_result.stdout.strip()}"
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _entry_matches(entry: dict, query_lower: str, chat_filter: str) -> bool:
    if entry.get("role") not in ("user", "assistant"):
        return False
    if chat_filter and entry.get("chat_id") != chat_filter:
        return False
    content = entry.get("content", "")
    return isinstance(content, str) and query_lower in content.lower()


def _format_entry(entry: dict) -> str:
    ts = entry.get("ts", "")[:16]
    cid = entry.get("chat_id", "")
    content = entry.get("content", "")[:300]
    return f"[{ts}] ({cid}) {entry['role']}: {content}"


def _grep_session_files(files: list, query_lower: str, chat_filter: str, max_results: int = 20) -> list[str]:
    results = []
    for f in files:
        entries = _parse_session_file(f, query_lower, chat_filter, max_results - len(results))
        results.extend(entries)
        if len(results) >= max_results:
            break
    return results


def _parse_session_file(f, query_lower: str, chat_filter: str, limit: int) -> list[str]:
    if not f.exists():
        return []
    results = []
    try:
        with f.open() as fh:
            for line in fh:
                if not line.strip():
                    continue
                entry = json.loads(line)
                if _entry_matches(entry, query_lower, chat_filter):
                    results.append(_format_entry(entry))
                    if len(results) >= limit:
                        break
    except Exception:
        return results  # best-effort: return what we got
    return results


@tool(
    "search_sessions",
    "Search past conversation logs. Use this before claiming you don't remember something. "
    "Returns matching messages from session history.",
    {"query": str, "date": str, "chat_id": str},
)
async def search_sessions(args: dict) -> dict:
    query_str = str(args.get("query", ""))
    date_filter = str(args.get("date", ""))
    chat_filter = str(args.get("chat_id", ""))

    sessions_dir = workspace.HOME / "sessions"
    if not sessions_dir.exists():
        return _text("No session logs found.")

    if date_filter:
        files = [sessions_dir / f"{date_filter}.jsonl"]
    else:
        files = sorted(sessions_dir.glob("*.jsonl"), reverse=True)[:7]

    qmd_result = await _try_qmd_search(query_str)
    if qmd_result:
        return _text(qmd_result)

    results = _grep_session_files(files, query_str.lower(), chat_filter)
    if not results:
        return _text(f"No matches for '{query_str}' in recent session logs.")
    return _text("\n\n".join(results))


@tool(
    "telegram_send_voice",
    "Convert text to a voice message (OGG/Opus) and send it to a Telegram chat. "
    "Great for daily summaries, briefings, or any content that's nicer to listen to. "
    "Uses edge-tts for synthesis. Optional voice parameter (default: en-US-AriaNeural).",
    {"chat_id": str, "text": str, "voice": str, "caption": str},
)
async def telegram_send_voice(args: dict) -> dict:
    import tempfile

    chat_id = str(args["chat_id"])
    if err := _check_allowed(chat_id):
        return err

    text = str(args["text"])
    voice = str(args.get("voice", "en-US-AriaNeural") or "en-US-AriaNeural")
    caption = str(args.get("caption", "") or "")

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False, dir=str(workspace.HOME)) as tmp:
        ogg_path = tmp.name

    try:
        result = await asyncio.to_thread(_text_to_voice, text, ogg_path, voice)
        if result != ogg_path:
            return _text(f"TTS error: {result}")

        send_result = await asyncio.to_thread(_send_telegram_voice, chat_id, ogg_path, caption)
        return _text(send_result)
    finally:
        Path(ogg_path).unlink(missing_ok=True)


@tool(
    "telegram_react",
    "React to a user's Telegram message with an emoji. Use this to acknowledge messages, "
    "express understanding, or give feedback. Pick the emoji that fits the situation — "
    "don't always use the same one. The message_id is provided in the user message context.",
    {"chat_id": str, "message_id": str, "emoji": str},
)
async def telegram_react(args: dict) -> dict:
    chat_id = str(args["chat_id"])
    if err := _check_allowed(chat_id):
        return err
    try:
        message_id = int(args["message_id"])
    except (ValueError, TypeError):
        return _text(f"Error: invalid message_id {args.get('message_id')!r}")
    emoji = str(args.get("emoji", "\U0001f44d") or "\U0001f44d")
    result = await asyncio.to_thread(_set_reaction, chat_id, message_id, emoji)
    return _text(result)


@tool(
    "test_tool",
    "Validate a staged tool file in tools/.staging/ before deploying. "
    "Checks SCHEMA structure and callable execute. Optionally runs execute with test_args (JSON string).",
    {"file_name": str, "test_args": str},
)
async def test_tool(args: dict) -> dict:
    from .tool_loader import validate_tool_module

    file_name = str(args["file_name"])
    test_args_raw = str(args.get("test_args", "") or "")
    path = workspace.TOOLS_STAGING / file_name

    if not path.exists():
        return _text(f"FAIL: {file_name} not found in staging directory.")

    ok, errors, mod = validate_tool_module(path)
    if not ok:
        return _text(f"FAIL: {file_name}\n" + "\n".join(f"- {e}" for e in errors))

    report = f"PASS: {file_name} — schema and execute valid."

    if test_args_raw:
        try:
            test_args_parsed = json.loads(test_args_raw)
        except json.JSONDecodeError as e:
            return _text(f"PASS (validation) but test_args is invalid JSON: {e}")
        try:
            result = mod.execute(**test_args_parsed)
            report += f"\nexecute() returned: {result!r}"
        except Exception as e:
            report += f"\nexecute() raised: {type(e).__name__}: {e}"

    return _text(report)


@tool(
    "deploy_tool",
    "Move a staged tool from tools/.staging/ to tools/ (goes live on next message). "
    "Re-validates before deploying.",
    {"file_name": str},
)
async def deploy_tool(args: dict) -> dict:
    from .tool_loader import validate_tool_module

    file_name = str(args["file_name"])
    src = workspace.TOOLS_STAGING / file_name

    if not src.exists():
        return _text(f"Error: {file_name} not found in staging directory.")

    ok, errors, _mod = validate_tool_module(src)
    if not ok:
        return _text(f"Refused to deploy {file_name} — validation failed:\n" + "\n".join(f"- {e}" for e in errors))

    dest = workspace.TOOLS_DIR / file_name
    shutil.move(str(src), str(dest))
    return _text(f"Deployed {file_name} to tools/. It will be live on the next message.")


@tool(
    "disable_tool",
    "Disable a live tool by renaming foo.py → foo.py.disabled. Reversible.",
    {"tool_name": str},
)
async def disable_tool(args: dict) -> dict:
    name = str(args["tool_name"])
    # Accept both "foo" and "foo.py"
    if not name.endswith(".py"):
        name = name + ".py"
    path = workspace.TOOLS_DIR / name
    if not path.exists():
        return _text(f"Error: {name} not found in tools directory.")
    disabled = path.with_suffix(".py.disabled")
    path.rename(disabled)
    return _text(f"Disabled {name} → {disabled.name}. Rename back to re-enable.")


def _subconscious_list(args: dict) -> dict:
    from . import subconscious
    threads = subconscious.load_threads()
    if not threads:
        return _text("No open threads.")
    import yaml
    return _text(yaml.dump(threads, default_flow_style=False))


def _subconscious_resolve(args: dict) -> dict:
    from . import subconscious
    thread_id = str(args.get("thread_id", ""))
    if not thread_id:
        return _text("Error: thread_id required for resolve action.")
    removed = subconscious.resolve_thread(thread_id)
    return _text(f"Resolved thread: {thread_id}") if removed else _text(f"Thread not found: {thread_id}")


def _subconscious_add(args: dict) -> dict:
    from . import subconscious
    raw = str(args.get("thread_data", "") or "")
    if not raw:
        return _text("Error: thread_data (JSON string) required for add action.")
    try:
        thread_data = json.loads(raw)
    except json.JSONDecodeError as e:
        return _text(f"Error: invalid JSON in thread_data: {e}")
    try:
        tid = subconscious.add_thread(thread_data)
    except ValueError as e:
        return _text(f"Error: {e}")
    return _text(f"Added thread: {tid}")


@tool(
    "update_subconscious",
    "Manage the subconscious reflection log. Actions: 'add' (with thread_data JSON), "
    "'resolve' (with thread_id), 'list' (returns all open threads).",
    {"action": str, "thread_id": str, "thread_data": str},
)
async def update_subconscious(args: dict) -> dict:
    action = str(args.get("action", ""))
    dispatch = {"list": _subconscious_list, "resolve": _subconscious_resolve, "add": _subconscious_add}
    handler = dispatch.get(action)
    if handler is None:
        return _text(f"Error: unknown action {action!r}. Use 'add', 'resolve', or 'list'.")
    return handler(args)


@tool(
    "reflect",
    "Trigger an immediate subconscious reflection cycle. "
    "Reads open threads, recent session logs, and memory, then decides whether to act.",
    {},
)
async def reflect(args: dict) -> dict:
    from .scheduler import _run_subconscious
    await asyncio.to_thread(_run_subconscious)
    return _text("Reflection cycle complete.")


_WEB_FETCH_TIMEOUT = 30


@tool(
    "web_fetch",
    "Fetch a URL and return its content as text. Has a 30-second timeout — "
    "will not hang on unresponsive sites. Returns page text for HTML, or raw content otherwise. "
    "Use 'browse' instead for JavaScript-heavy pages.",
    {"url": str, "max_chars": str},
)
async def web_fetch(args: dict) -> dict:
    from html.parser import HTMLParser

    import aiohttp

    url = str(args.get("url", ""))
    max_chars = int(args.get("max_chars", "20000") or "20000")
    if not url:
        return _text("Error: url is required")

    class _TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self._parts: list[str] = []
            self._skip = False
        def handle_starttag(self, tag, attrs):
            self._skip = tag in ("script", "style", "noscript")
        def handle_endtag(self, tag):
            if tag in ("script", "style", "noscript"):
                self._skip = False
        def handle_data(self, data):
            if not self._skip:
                text = data.strip()
                if text:
                    self._parts.append(text)
        def get_text(self) -> str:
            return "\n".join(self._parts)

    try:
        timeout = aiohttp.ClientTimeout(total=_WEB_FETCH_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session, \
                session.get(url, allow_redirects=True) as resp:
                content_type = resp.headers.get("content-type", "")
                body = await resp.text(errors="replace")

                if "html" in content_type:
                    parser = _TextExtractor()
                    parser.feed(body)
                    text = parser.get_text()
                else:
                    text = body

                truncated = text[:max_chars]
                if len(text) > max_chars:
                    truncated += f"\n\n[Truncated — {len(text)} chars total]"
                return _text(f"URL: {resp.url}\nStatus: {resp.status}\n\n{truncated}")
    except TimeoutError:
        return _text(f"Error: Request timed out after {_WEB_FETCH_TIMEOUT}s — site is unresponsive.")
    except Exception as e:
        return _text(f"Error fetching {url}: {type(e).__name__}: {e}")


CUSTOM_TOOLS = [
    telegram_send, telegram_edit, telegram_send_file, self_restart, self_update,
    update_config, read_skill_tool, search_sessions,
    browse, browser_click, browser_type, browser_screenshot, browser_eval,
    telegram_send_voice, telegram_react,
    test_tool, deploy_tool, disable_tool,
    update_subconscious, reflect,
    web_fetch,
]
