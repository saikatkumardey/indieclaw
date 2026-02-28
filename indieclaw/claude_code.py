from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field

from loguru import logger

from . import workspace

_CLAUDE_BIN = shutil.which("claude") or "claude"
_EDIT_INTERVAL = 2.0  # seconds between Telegram message edits
_TYPING_INTERVAL = 4.0  # seconds between "typing…" chat actions
_MAX_TG_MSG = 4000  # leave room for Telegram's 4096 limit
_MAX_TURNS = 15
_MAX_TOOL_LOG = 50  # cap tool_log to prevent unbounded growth
_CC_IDLE_TIMEOUT = 3600  # clean up idle CC sessions after 1 hour

# Fallback CC commands if init event hasn't been received yet
_DEFAULT_CC_COMMANDS = {"compact", "cost", "context"}


@dataclass
class CCSession:
    chat_id: str
    session_id: str | None = None
    process: asyncio.subprocess.Process | None = None
    output_msg_id: int | None = None
    buffer: str = ""
    last_edit: float = 0.0
    task: asyncio.Task | None = None
    working_dir: str = field(default_factory=lambda: str(workspace.HOME))
    tool_status: str = ""
    slash_commands: list[str] = field(default_factory=list)
    model: str = ""
    total_cost: float = 0.0
    turns: int = 0
    started_at: float = field(default_factory=time.monotonic)
    prompt: str = ""
    last_typing: float = 0.0
    tools_used: int = 0
    tool_log: list[str] = field(default_factory=list)
    pending_queue: list[str] = field(default_factory=list)
    html_broken: bool = False  # set when HTML parse fails, avoids flicker


_sessions: dict[str, CCSession] = {}


def has_active_session(chat_id: str) -> bool:
    return chat_id in _sessions


def is_session_busy(chat_id: str) -> bool:
    session = _sessions.get(chat_id)
    return session is not None and session.process is not None


def get_busy_hint(chat_id: str) -> str:
    """Get a user-facing hint about what CC is currently doing."""
    session = _sessions.get(chat_id)
    if not session or not session.process:
        return ""
    if session.tool_status:
        return session.tool_status
    return "thinking…"


def queue_message(chat_id: str, text: str) -> bool:
    """Queue a message for when the current turn finishes. Returns True if queued."""
    session = _sessions.get(chat_id)
    if not session or session.process is None:
        return False
    session.pending_queue.append(text)
    return True


def get_cc_commands(chat_id: str) -> set[str]:
    """Get available CC slash commands for this session."""
    session = _sessions.get(chat_id)
    if session and session.slash_commands:
        return set(session.slash_commands)
    return _DEFAULT_CC_COMMANDS


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as a compact human string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{minutes}m{secs:02d}s"


def get_session_info(chat_id: str) -> str | None:
    """Get session status summary for /cc with no args."""
    session = _sessions.get(chat_id)
    if not session:
        return None
    busy = "working" if session.process else "idle"
    elapsed = _format_elapsed(time.monotonic() - session.started_at)
    parts = [f"🅲🅻🅰🆄🅳🅴 🅲🅾🅳🅴\n\n<b>Session</b> ({busy}) · {elapsed}"]
    if session.model:
        model = session.model
        for prefix in ("claude-", "anthropic/"):
            model = model.removeprefix(prefix)
        parts.append(f"Model: {model}")
    stats = []
    if session.total_cost > 0:
        stats.append(f"${session.total_cost:.3f}")
    stats.append(f"{session.turns} turns")
    if session.tools_used > 0:
        stats.append(f"{session.tools_used} tool calls")
    parts.append(" · ".join(stats))
    if session.tool_status and session.process:
        parts.append(f"\n{session.tool_status}")
    if session.tool_log:
        recent = session.tool_log[-5:]
        parts.append("")
        parts.append("<b>Recent:</b>")
        for entry in recent:
            parts.append(f"  {_html_escape(entry)}")
    if session.pending_queue:
        parts.append(f"\n📨 {len(session.pending_queue)} queued message(s)")
    parts.append("")
    parts.append("/cc &lt;msg&gt;  ·  /cc stop")
    cmds = sorted(get_cc_commands(chat_id) - {"compact", "cost", "context"})
    if cmds:
        parts.append("/cc " + "  /cc ".join(cmds))
    return "\n".join(parts)


def get_stop_summary(chat_id: str) -> str:
    """Build a summary message for when a session is stopped."""
    session = _sessions.get(chat_id)
    if not session:
        return "Session ended."
    elapsed = _format_elapsed(time.monotonic() - session.started_at)
    stats = []
    if session.total_cost > 0:
        stats.append(f"${session.total_cost:.3f}")
    if session.turns > 0:
        stats.append(f"{session.turns} turns")
    if session.tools_used > 0:
        stats.append(f"{session.tools_used} tool calls")
    summary = f"Session ended · {elapsed}"
    if stats:
        summary += " · " + " · ".join(stats)
    return summary


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _md_to_tg_html(text: str) -> str:
    """Convert CommonMark markdown (already HTML-escaped) to Telegram HTML tags."""
    # Code fences → <pre><code> (must come before inline transforms)
    text = re.sub(
        r"```(\w*)\n(.*?)```",
        lambda m: f"<pre><code class=\"language-{m.group(1)}\">{m.group(2)}</code></pre>"
        if m.group(1)
        else f"<pre>{m.group(2)}</pre>",
        text,
        flags=re.DOTALL,
    )
    # Inline code
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Bold: **text** → <b>text</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # Italic: *text* (but not inside words or after bold)
    text = re.sub(r"(?<!\w)\*([^*]+?)\*(?!\w)", r"<i>\1</i>", text)
    # Headers → bold
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    # Links: [text](url) → <a href="url">text</a>
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    # Strikethrough: ~~text~~ → <s>text</s>
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    # Blockquotes: "> text" → Telegram <blockquote>
    text = re.sub(
        r"(^&gt;\s?.+(?:\n&gt;\s?.+)*)",
        lambda m: "<blockquote>" + re.sub(r"^&gt;\s?", "", m.group(0), flags=re.MULTILINE) + "</blockquote>",
        text,
        flags=re.MULTILINE,
    )
    # Bullet lists: leading "- " or "* " → "• "
    text = re.sub(r"^[\-\*]\s+", "• ", text, flags=re.MULTILINE)
    # Numbered lists: "1. " → "1. " (keep but normalize indentation)
    text = re.sub(r"^(\d+)\.\s+", r"\1. ", text, flags=re.MULTILINE)
    return text


def _truncate(text: str, limit: int = 60) -> str:
    """Truncate text with ellipsis indicator."""
    return text[:limit] + "…" if len(text) > limit else text


def _tool_status_line(block: dict) -> str:
    """Build a compact one-line tool status like '🔧 Read: main.py'."""
    name = block.get("name", "?")
    inp = block.get("input", {})
    hint_map = {
        "Bash": lambda: _truncate(inp.get("description", "") or inp.get("command", "")),
        "Read": lambda: inp.get("file_path", "").split("/")[-1],
        "Write": lambda: inp.get("file_path", "").split("/")[-1],
        "Edit": lambda: inp.get("file_path", "").split("/")[-1],
        "Glob": lambda: _truncate(inp.get("pattern", "")),
        "Grep": lambda: _truncate(inp.get("pattern", "")),
        "Agent": lambda: _truncate(inp.get("description", "") or inp.get("prompt", ""), 40),
        "WebFetch": lambda: _truncate(inp.get("url", ""), 50),
        "WebSearch": lambda: _truncate(inp.get("query", ""), 50),
        "TodoWrite": lambda: "updating tasks",
        "NotebookEdit": lambda: inp.get("file_path", "").split("/")[-1],
    }
    hint = hint_map.get(name, lambda: "")()
    if hint:
        return f"🔧 {name}: {hint}"
    return f"🔧 {name}"


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _format_event(event: dict, session: CCSession) -> str:
    etype = event.get("type", "")

    if etype == "system":
        # Capture metadata from init event, filter out internal/plugin commands
        if event.get("slash_commands"):
            _skip = {"heapdump", "init", "debug", "batch", "loop"}
            session.slash_commands = [
                c for c in event["slash_commands"]
                if ":" not in c and c not in _skip
            ]
        if event.get("model"):
            session.model = event["model"]
        return ""

    if etype == "assistant":
        blocks = event.get("message", {}).get("content", [])
        has_text = any(b.get("type") == "text" and b.get("text") for b in blocks)
        if has_text:
            session.turns += 1
        text_parts = []
        for b in blocks:
            if b.get("type") == "tool_use":
                status = _tool_status_line(b)
                session.tool_status = status
                session.tools_used += 1
                session.tool_log.append(status.removeprefix("🔧 "))
                if len(session.tool_log) > _MAX_TOOL_LOG:
                    session.tool_log = session.tool_log[-_MAX_TOOL_LOG:]
            elif b.get("type") == "text":
                text = b.get("text", "")
                if text:
                    session.tool_status = ""
                    text_parts.append(_md_to_tg_html(_html_escape(text)))
        return "".join(text_parts)

    if etype == "result":
        session.tool_status = ""
        cost = event.get("cost_usd")
        duration = event.get("duration_ms")
        if cost is not None:
            session.total_cost += cost
        parts = ["✅"]
        if cost is not None:
            parts.append(f"${cost:.3f}")
        if duration is not None:
            parts.append(f"{duration / 1000:.0f}s")
        return "\n\n" + " · ".join(parts)

    return ""


_CC_HEADER = "🅲🅻🅰🆄🅳🅴 🅲🅾🅳🅴\n"


def _build_footer(session: CCSession, done: bool) -> str:
    if done:
        return ""
    elapsed = _format_elapsed(time.monotonic() - session.started_at)
    return f"\n\n<i>⏱ {elapsed} · /cc stop</i>"


def _build_display(session: CCSession, done: bool = False) -> str:
    """Build the message text from buffer + tool status + footer."""
    footer = _build_footer(session, done)
    status_line = ""
    if session.tool_status and not done:
        status_line = f"\n\n<i>{_html_escape(session.tool_status)}</i>"

    overhead = len(footer) + len(status_line) + len(_CC_HEADER) + 20
    max_body = _MAX_TG_MSG - overhead
    buf = session.buffer
    if len(buf) > max_body:
        # Strip HTML before truncating to avoid breaking mid-tag
        buf = "…\n" + _strip_html(buf)[-(max_body - 5):]
    return _CC_HEADER + buf + status_line + footer


def _strip_html(text: str) -> str:
    from html import unescape
    for tag in ("b", "i", "code", "pre", "a", "s", "blockquote"):
        text = re.sub(rf"<{tag}[^>]*>", "", text)
        text = text.replace(f"</{tag}>", "")
    return unescape(text)


async def _send_typing(session: CCSession, bot) -> None:
    """Send typing indicator if enough time has passed."""
    now = time.monotonic()
    if now - session.last_typing < _TYPING_INTERVAL:
        return
    try:
        await bot.send_chat_action(chat_id=session.chat_id, action="typing")
        session.last_typing = now
    except Exception:
        logger.debug("typing indicator failed")


async def _edit_output(session: CCSession, bot, final: bool = False) -> None:
    now = time.monotonic()
    if not final and (now - session.last_edit) < _EDIT_INTERVAL:
        return
    if not session.output_msg_id:
        return
    if not session.buffer.strip() and not session.tool_status:
        return

    text = _build_display(session, done=final)
    # Try HTML first, fall back to plain text on parse errors.
    # Retry HTML on final edits even if previously broken (content may have changed).
    use_html = not session.html_broken or final
    if use_html:
        try:
            await bot.edit_message_text(
                chat_id=session.chat_id,
                message_id=session.output_msg_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            session.last_edit = now
            session.html_broken = False  # recovered
            return
        except Exception as e:
            err = str(e).lower()
            if "not modified" in err:
                return
            if "parse" not in err and "can't" not in err:
                logger.debug("CC edit failed: %s", e)
                return
            session.html_broken = True

    # Plain text fallback
    try:
        await bot.edit_message_text(
            chat_id=session.chat_id,
            message_id=session.output_msg_id,
            text=_strip_html(text),
            disable_web_page_preview=True,
        )
        session.last_edit = now
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.debug("CC plain edit failed: %s", e)


async def _maybe_split_message(session: CCSession, bot) -> None:
    """If buffer is getting large, finalize current message and start a new one."""
    if len(session.buffer) < _MAX_TG_MSG - 200:
        return

    text = _build_display(session, done=True)
    try:
        await bot.edit_message_text(
            chat_id=session.chat_id,
            message_id=session.output_msg_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        try:
            await bot.edit_message_text(
                chat_id=session.chat_id,
                message_id=session.output_msg_id,
                text=_strip_html(text),
                disable_web_page_preview=True,
            )
        except Exception:
            logger.debug("split fallback edit failed")

    msg = await bot.send_message(
        chat_id=session.chat_id,
        text="🅲🅻🅰🆄🅳🅴 🅲🅾🅳🅴\n<i>continued…</i>",
        parse_mode="HTML",
    )
    session.output_msg_id = msg.message_id
    session.buffer = ""
    session.last_edit = 0.0
    session.html_broken = False


async def _stream_loop(session: CCSession, bot) -> None:
    proc = session.process
    if not proc or not proc.stdout:
        return

    try:
        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("type") == "system" and event.get("session_id"):
                session.session_id = event["session_id"]
            if event.get("type") == "result" and event.get("session_id"):
                session.session_id = event["session_id"]

            formatted = _format_event(event, session)
            if formatted:
                session.buffer += formatted
                await _maybe_split_message(session, bot)
            await _send_typing(session, bot)
            await _edit_output(session, bot)

        stderr_text = ""
        if proc.stderr:
            try:
                stderr_data = await proc.stderr.read()
                stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
            except Exception:
                logger.debug("Failed to read stderr", exc_info=True)

        exit_code = proc.returncode
        stderr_text = _strip_ansi(stderr_text)
        if not session.buffer.strip() and stderr_text:
            session.buffer = f"⚠️ error:\n<pre>{_html_escape(stderr_text[:500])}</pre>"
        elif not session.buffer.strip() and exit_code:
            session.buffer = f"⚠️ exited with code {exit_code}"
        elif not session.buffer.strip():
            session.buffer = "✅ done (no output)"
        elif exit_code and exit_code != 0:
            session.buffer += f"\n\n⚠️ exit code {exit_code}"

        await _edit_output(session, bot, final=True)
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("CC stream error")
        try:
            session.buffer += "\n\n⚠️ Stream error — session is still alive, try sending another message"
            await _edit_output(session, bot, final=True)
        except Exception:
            pass
    finally:
        session.process = None
        await _drain_queue(session, bot)


async def _drain_queue(session: CCSession, bot) -> None:
    """Process any messages that arrived while busy."""
    if not session.pending_queue:
        return
    prompt = session.pending_queue[-1]
    skipped = len(session.pending_queue) - 1
    session.pending_queue.clear()

    if not session.session_id:
        await bot.send_message(
            chat_id=session.chat_id,
            text="💻 Queued message dropped — session has no resume ID. Send /cc &lt;prompt&gt; to start fresh.",
            parse_mode="HTML",
        )
        return

    skip_note = f" ({skipped} earlier skipped)" if skipped else ""
    preview = _html_escape(prompt[:60]) + ("…" if len(prompt) > 60 else "")
    msg = await bot.send_message(
        chat_id=session.chat_id,
        text=f"🅲🅻🅰🆄🅳🅴 🅲🅾🅳🅴\n<i>▶ {preview}{skip_note}</i>",
        parse_mode="HTML",
    )
    session.output_msg_id = msg.message_id
    session.buffer = ""
    session.last_edit = 0.0
    session.tool_status = ""
    session.html_broken = False

    try:
        session.process = await _spawn_proc(session, prompt)
    except Exception as e:
        await bot.edit_message_text(
            chat_id=session.chat_id,
            message_id=msg.message_id,
            text=f"🅲🅻🅰🆄🅳🅴 🅲🅾🅳🅴\n⚠️ {_html_escape(str(e)[:200])}",
            parse_mode="HTML",
        )
        return

    session.task = asyncio.create_task(_stream_loop(session, bot))


def _build_cmd(prompt: str, session: CCSession) -> list[str]:
    cmd = [
        _CLAUDE_BIN, "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--max-turns", str(_MAX_TURNS),
        "--permission-mode", "bypassPermissions",
    ]
    if session.session_id:
        cmd.extend(["--resume", session.session_id])
    return cmd


def _make_env() -> dict:
    env = {**os.environ}
    env.pop("CLAUDECODE", None)
    return env


async def _spawn_proc(session: CCSession, prompt: str) -> asyncio.subprocess.Process:
    env = _make_env()
    return await asyncio.create_subprocess_exec(
        *_build_cmd(prompt, session),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=session.working_dir,
        env=env,
    )


async def start_session(chat_id: str, prompt: str, bot, working_dir: str | None = None) -> None:
    if chat_id in _sessions and _sessions[chat_id].process is not None:
        await bot.send_message(chat_id=chat_id, text="🅲🅻🅰🆄🅳🅴 🅲🅾🅳🅴\nSession already running. Send /cc stop first.")
        return

    truncated = _truncate(prompt, 80)
    resuming = chat_id in _sessions and _sessions.get(chat_id, CCSession(chat_id="")).session_id
    label = "resuming…" if resuming else "starting…"
    msg = await bot.send_message(
        chat_id=chat_id,
        text=f"🅲🅻🅰🆄🅳🅴 🅲🅾🅳🅴\n<i>{_html_escape(truncated)}</i>\n\n{label}",
        parse_mode="HTML",
    )
    session = CCSession(
        chat_id=chat_id,
        output_msg_id=msg.message_id,
        working_dir=working_dir or str(workspace.HOME),
        prompt=prompt,
    )
    old = _sessions.get(chat_id)
    if old and old.session_id:
        session.session_id = old.session_id

    try:
        session.process = await _spawn_proc(session, prompt)
    except Exception as e:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=msg.message_id,
            text=f"🅲🅻🅰🆄🅳🅴 🅲🅾🅳🅴\n⚠️ Failed to start: {_html_escape(str(e))}",
            parse_mode="HTML",
        )
        return

    _sessions[chat_id] = session
    session.task = asyncio.create_task(_stream_loop(session, bot))


async def continue_session(chat_id: str, prompt: str, bot) -> bool:
    session = _sessions.get(chat_id)
    if not session or session.process is not None or not session.session_id:
        return False

    truncated = _truncate(prompt, 80)
    msg = await bot.send_message(
        chat_id=chat_id,
        text=f"🅲🅻🅰🆄🅳🅴 🅲🅾🅳🅴\n<i>{_html_escape(truncated)}</i>\n\nresuming…",
        parse_mode="HTML",
    )
    session.output_msg_id = msg.message_id
    session.buffer = ""
    session.last_edit = 0.0
    session.tool_status = ""
    session.html_broken = False

    try:
        session.process = await _spawn_proc(session, prompt)
    except Exception as e:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=msg.message_id,
            text=f"🅲🅻🅰🆄🅳🅴 🅲🅾🅳🅴\n⚠️ Failed: {_html_escape(str(e))}",
            parse_mode="HTML",
        )
        return False

    session.task = asyncio.create_task(_stream_loop(session, bot))
    return True


def cleanup_idle_sessions() -> int:
    """Remove CC sessions that have been idle (no process) for too long."""
    now = time.monotonic()
    stale = [
        cid for cid, s in _sessions.items()
        if s.process is None and (now - s.started_at) > _CC_IDLE_TIMEOUT
    ]
    for cid in stale:
        _sessions.pop(cid, None)
    return len(stale)


async def stop_session(chat_id: str) -> bool:
    session = _sessions.pop(chat_id, None)
    if not session:
        return False
    if session.task:
        session.task.cancel()
        try:
            await session.task
        except asyncio.CancelledError:
            pass
    if session.process:
        try:
            session.process.terminate()
            await asyncio.wait_for(session.process.wait(), timeout=5)
        except (TimeoutError, ProcessLookupError):
            try:
                session.process.kill()
            except ProcessLookupError:
                pass
    return True
