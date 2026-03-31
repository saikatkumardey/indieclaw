from __future__ import annotations

import asyncio
import html as _html
import re
from pathlib import Path

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from . import workspace
from .agent import (
    get_streaming,
    get_tool_activity,
    reset_session,
    session_log,
)
from .agent import (
    run as agent_run,
)
from .agent import (
    run_streaming as agent_run_streaming,
)
from .auth import require_allowed
from .handlers_commands import (  # noqa: F401
    on_crons,
    on_effort,
    on_effort_callback,
    on_help,
    on_model,
    on_model_callback,
    on_restart,
    on_status,
    on_streaming,
    on_update,
)
from .tools import MAX_TG_MSG

_RE_CODE_SPLIT = re.compile(r"(```[\s\S]*?```|`[^`]+`)")
_RE_BOLD_STAR = re.compile(r"\*\*(.+?)\*\*")
_RE_BOLD_UNDER = re.compile(r"__(.+?)__")
_RE_ITALIC = re.compile(r"(?<![<b])\*(.+?)\*(?![>])")
_RE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_RE_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_RE_BULLET = re.compile(r"^(\s*)-\s+", re.MULTILINE)
_RE_STRIP_HTML = re.compile(r"<[^>]+>")

_DEBOUNCE_SECONDS = 1.5
_debounce_buffers: dict[str, dict] = {}
_active_runs: dict[str, asyncio.Task] = {}  # chat_id -> running agent task
_pending_followups: dict[str, list[str]] = {}  # chat_id -> queued messages


async def flush_debounce(chat_id: str) -> None:
    """Wait for any pending debounce task to complete. Used by tests."""
    buf = _debounce_buffers.get(chat_id)
    if buf and buf["task"] and not buf["task"].done():
        await buf["task"]



def _to_telegram_html(text: str) -> str:
    """Convert CommonMark to Telegram-safe HTML."""
    parts = _RE_CODE_SPLIT.split(text)
    result = []
    for part in parts:
        if part.startswith("```"):
            inner = part[3:]
            if inner.startswith("\n"):
                inner = inner[1:]
            first_nl = inner.find("\n")
            if first_nl >= 0:
                first_line = inner[:first_nl].strip()
                if first_line and " " not in first_line and not first_line.startswith(("<", "{", "[", "#", "-", "/", "(")) and first_line.isalpha():
                    inner = inner[first_nl + 1:]
            inner = inner.rstrip("`").rstrip()
            result.append(f"<pre>{_html.escape(inner, quote=False)}</pre>")
        elif part.startswith("`"):
            inner = part[1:-1]
            result.append(f"<code>{_html.escape(inner, quote=False)}</code>")
        else:
            part = _html.escape(part)
            part = _RE_BOLD_STAR.sub(r"<b>\1</b>", part)
            part = _RE_BOLD_UNDER.sub(r"<b>\1</b>", part)
            part = _RE_ITALIC.sub(r"<i>\1</i>", part)
            part = _RE_LINK.sub(r'<a href="\2">\1</a>', part)
            part = _RE_HEADING.sub(r"<b>\1</b>", part)
            part = _RE_BULLET.sub("\\1\u2022 ", part)
            result.append(part)
    return "".join(result)


def _is_tool_noise(reply: str) -> bool:
    """Return True if the reply is a default tool-only response with no real content."""
    return reply == "(no response)" or reply.startswith("Done. (used:")


def _format_activity(label: str, elapsed: float) -> str:
    """Format tool activity status for display."""
    if elapsed >= 3.0:
        return f"\U0001f527 {label}\u2026 ({elapsed:.0f}s)"
    return f"\U0001f527 {label}\u2026"


_ERROR_MESSAGES = {
    asyncio.TimeoutError: "Timed out. Try again, or /reset if it keeps happening.",
    PermissionError: "Permission denied. Try /restart.",
    ConnectionError: "Connection error. Check your network and try again.",
}


async def _notify_error(bot, message, chat_id: str, e: Exception) -> None:
    """Send an error notification regardless of whether this is a message or reaction."""
    text = _classify_error(e)
    if message:
        await message.reply_text(text)
        return
    try:
        await bot.send_message(chat_id=int(chat_id), text=text)
    except Exception:
        logger.debug("failed to send error notification for chat_id={}", chat_id)


def _classify_error(e: Exception) -> str:
    for cls in type(e).__mro__:
        if cls in _ERROR_MESSAGES:
            return _ERROR_MESSAGES[cls]
    if type(e).__name__ == "TimeoutExpired":
        return "Timed out. Try again, or /reset if it keeps happening."
    return "Something went wrong. Try again, or /reset if it persists."


class _TypingLoop:
    """Keep the 'typing...' indicator alive and show tool activity."""

    def __init__(self, bot, chat_id: str, interval: float = 2.0):
        self._bot = bot
        self._chat_id = chat_id
        self._interval = interval
        self._task: asyncio.Task | None = None

    async def _loop(self):
        try:
            while True:
                activity = get_tool_activity(self._chat_id)
                if activity:
                    label, elapsed = activity
                    status = _format_activity(label, elapsed)
                    try:
                        await self._bot.send_message_draft(
                            chat_id=int(self._chat_id), draft_id=_DRAFT_ID, text=status,
                        )
                    except Exception:
                        logger.debug("send_message_draft failed in typing loop", exc_info=True)
                try:
                    await self._bot.send_chat_action(chat_id=self._chat_id, action="typing")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await asyncio.sleep(5)
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            pass

    async def __aenter__(self):
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *exc):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


def _strip_html(text: str) -> str:
    """Remove HTML tags so the text is safe as plain text."""
    return _RE_STRIP_HTML.sub("", text)


async def _reply_html(message, text: str) -> None:
    """Reply with HTML, falling back to stripped plain text on failure."""
    try:
        await message.reply_text(text, parse_mode="HTML")
    except Exception:
        await message.reply_text(_strip_html(text))


async def _reply_chunked(message, text: str) -> None:
    """Send text in <=MAX_TG_MSG-char chunks with HTML, falling back to plain text."""
    formatted = _to_telegram_html(text)
    if not formatted:
        return
    chunks = [formatted[i : i + MAX_TG_MSG] for i in range(0, len(formatted), MAX_TG_MSG)]
    for chunk in chunks:
        await _reply_html(message, chunk)


async def _send_reply(bot, message, chat_id: str, reply: str) -> None:
    """Send the agent reply via the appropriate channel."""
    if message:
        await _reply_chunked(message, reply)
    else:
        fmt = _to_telegram_html(reply)
        try:
            await bot.send_message(chat_id=chat_id, text=fmt, parse_mode="HTML")
        except Exception:
            await bot.send_message(chat_id=chat_id, text=fmt)


_DRAFT_INTERVAL = 0.5  # minimum seconds between draft updates
_DRAFT_ID = 1  # constant draft_id; same ID = animated updates


async def _draft_sender(bot, chat_id: str, accumulated: list[str], done_event: asyncio.Event) -> None:
    while not done_event.is_set():
        draft_text = ""
        activity = get_tool_activity(chat_id)
        if activity:
            label, elapsed = activity
            draft_text = _format_activity(label, elapsed)
        if accumulated:
            content = "".join(accumulated)[:MAX_TG_MSG - 200]
            if draft_text:
                draft_text = f"{draft_text}\n\n{content}"
            else:
                draft_text = content
        if draft_text:
            try:
                await bot.send_message_draft(chat_id=int(chat_id), draft_id=_DRAFT_ID, text=draft_text)
            except Exception:
                logger.debug("send_message_draft failed", exc_info=True)
        try:
            await asyncio.wait_for(done_event.wait(), timeout=_DRAFT_INTERVAL)
        except TimeoutError:
            pass


async def _run_agent_and_reply_streaming(
    bot, message, chat_id: str, agent_msg: str,
) -> None:
    accumulated: list[str] = []
    done_event = asyncio.Event()
    sender_task = asyncio.create_task(_draft_sender(bot, chat_id, accumulated, done_event))
    try:
        async for event_type, data in agent_run_streaming(chat_id=chat_id, user_message=agent_msg):
            if event_type == "text_delta":
                accumulated.append(data)
            elif event_type == "done":
                done_event.set()
                if not data or _is_tool_noise(data):
                    return
                await _send_reply(bot, message, chat_id, data)
    except asyncio.CancelledError:
        if message:
            await message.reply_text("Stopped.")
    except Exception as e:
        logger.exception("Streaming error: {}", e)
        if message:
            await message.reply_text(_classify_error(e))
    finally:
        done_event.set()
        sender_task.cancel()
        try:
            await sender_task
        except asyncio.CancelledError:
            pass
        # Clear the draft banner so it doesn't linger as a "pinned message"
        try:
            await bot.send_message_draft(chat_id=int(chat_id), draft_id=_DRAFT_ID, text="\u200b")
        except Exception:
            logger.debug("draft clear failed")


async def _drain_followups(bot, chat_id: str) -> None:
    """Process queued follow-up messages after the active run completes."""
    queued = _pending_followups.pop(chat_id, None)
    if not queued:
        return
    combined = "\n".join(queued)
    logger.info("Processing {} queued message(s) for {}", len(queued), chat_id)
    agent_msg = f"[chat_id={chat_id}]\n{combined}"
    await _run_agent_and_reply(bot, None, chat_id, agent_msg)


async def _run_agent_and_reply(
    bot, message, chat_id: str, agent_msg: str,
) -> None:
    """Run agent and send reply. Shared by on_message, on_reaction, _handle_upload."""
    _active_runs[chat_id] = asyncio.current_task()
    try:
        # Use streaming for interactive sessions when enabled
        if message and not chat_id.startswith("cron:") and get_streaming():
            await _run_agent_and_reply_streaming(
                bot, message, chat_id, agent_msg,
            )
            return

        try:
            async with _TypingLoop(bot, chat_id):
                reply = await agent_run(chat_id=chat_id, user_message=agent_msg)
            if not reply or _is_tool_noise(reply):
                await _send_reply(bot, message, chat_id, "\u2705 Done.")
                return
            await _send_reply(bot, message, chat_id, reply)
        except asyncio.CancelledError:
            if message:
                await message.reply_text("Stopped.")
        except Exception as e:
            logger.exception("Error: {}", e)
            await _notify_error(bot, message, chat_id, e)
    finally:
        _active_runs.pop(chat_id, None)
        await _drain_followups(bot, chat_id)


async def on_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    uid = user.id if user else "unknown"
    from .auth import is_allowed
    from .version import local_version
    ver = local_version()

    if update.effective_chat and is_allowed(update.effective_chat.id):
        text = (
            f"\U0001f44b <b>IndieClaw</b> v{ver}\n\n"
            "Send me a message, photo, file, or voice note.\n"
            "I can run commands, browse the web, and learn new skills.\n\n"
            f"Your Telegram user ID: <code>{uid}</code>\n\n"
            "/help \u2014 see all commands"
        )
    else:
        text = (
            f"\U0001f44b <b>IndieClaw</b> v{ver}\n\n"
            f"Your Telegram user ID: <code>{uid}</code>\n\n"
            "Copy this ID and paste it during <code>indieclaw setup</code> "
            "to connect this bot to your account."
        )
    try:
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception:
        await update.message.reply_text(_strip_html(text))


@require_allowed
async def on_reset(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    session_log(chat_id, "system", "SESSION_RESET")
    await reset_session(chat_id)
    await update.message.reply_text("\u2705 Session reset. Starting fresh.")


@require_allowed
async def on_stop(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stop — cancel the currently running agent turn."""
    chat_id = str(update.effective_chat.id)
    task = _active_runs.get(chat_id)
    if task and not task.done():
        task.cancel()
        await update.message.reply_text("\u23f9 Stopping current run.")
    else:
        await update.message.reply_text("Nothing running.")




@require_allowed
async def on_btw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /btw — quick side question via claude -p, no session or tools."""
    import subprocess as _sp

    msg = update.message
    chat_id = str(update.effective_chat.id)
    text = (msg.text or "").split(None, 1)[1] if len((msg.text or "").split(None, 1)) > 1 else ""
    if not text.strip():
        await msg.reply_text("Usage: /btw <question>\nQuick side question — no tools, no history.")
        return

    from .config import Config
    system = "You are a helpful assistant. Be concise and direct. Use standard Markdown formatting (**bold**, *italic*). No headers."
    btw_model = Config.load().get("btw_model")

    try:
        async with _TypingLoop(context.bot, chat_id):
            result = await asyncio.to_thread(
                _sp.run,
                [
                    "claude", "-p",
                    "--model", btw_model,
                    "--system-prompt", system,
                    "--allowedTools", "WebSearch",
                ],
                input=text, capture_output=True, text=True, timeout=120,
            )
        reply = result.stdout.strip() if result.returncode == 0 else f"Error: {result.stderr[:300]}"
        btw_reply = f"\U0001f4ac {reply}" if reply else "(no response)"
        await _reply_chunked(msg, btw_reply)
    except Exception as e:
        logger.exception("Error handling /btw: {}", e)
        await msg.reply_text(_classify_error(e))


@require_allowed
async def on_cc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cc — start or interact with a live Claude Code session."""
    from .claude_code import (
        continue_session,
        get_busy_hint,
        get_cc_commands,
        get_session_info,
        get_stop_summary,
        has_active_session,
        is_session_busy,
        queue_message,
        start_session,
        stop_session,
    )

    msg = update.message
    chat_id = str(update.effective_chat.id)
    parts = (msg.text or "").split(None, 1)
    prompt = parts[1] if len(parts) > 1 else ""

    if prompt.strip().lower() == "stop":
        summary = get_stop_summary(chat_id)
        stopped = await stop_session(chat_id)
        await msg.reply_text(f"\U0001f4bb CC stopped.\n{summary}" if stopped else "No active CC session.")
        return

    if not prompt.strip():
        info = get_session_info(chat_id)
        if info:
            await msg.reply_text(info, parse_mode="HTML")
        else:
            await msg.reply_text(
                "<b>💻 Claude Code</b>\n\n"
                "/cc &lt;prompt&gt; — start a session\n"
                "/cc stop — end session\n\n"
                "Messages route to CC while a session is active.",
                parse_mode="HTML",
            )
        return

    # Forward CC slash commands (e.g. /cc compact → /compact)
    cmd = prompt.strip().split()[0].lower()
    if cmd in get_cc_commands(chat_id) and has_active_session(chat_id):
        prompt = f"/{cmd}"

    if has_active_session(chat_id):
        if is_session_busy(chat_id):
            hint = get_busy_hint(chat_id)
            if queue_message(chat_id, prompt):
                await msg.reply_text(
                    f"💻 Queued — CC is {hint}. Will run when this turn finishes.\n"
                    "<i>Only the latest queued message is kept.</i>",
                    parse_mode="HTML",
                )
            else:
                await msg.reply_text(f"💻 CC is {hint}… /cc stop to cancel.")
            return
        continued = await continue_session(chat_id, prompt, context.bot)
        if not continued:
            # Session exists but can't resume (no session_id) — start fresh
            await start_session(chat_id, prompt, context.bot)
        return

    await start_session(chat_id, prompt, context.bot)


@require_allowed
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.edited_message or update.message
    chat_id = str(update.effective_chat.id)
    text = msg.text or ""
    is_edit = update.edited_message is not None
    logger.info("{} [{}]: {}", "Edit" if is_edit else "Incoming", chat_id, text[:80])

    # Route to CC session if one is active (no debounce)
    from .claude_code import continue_session, get_busy_hint, has_active_session, is_session_busy
    if has_active_session(chat_id):
        if is_session_busy(chat_id):
            hint = get_busy_hint(chat_id)
            await msg.reply_text(f"💻 CC is {hint}… wait or /cc stop.")
            return
        continued = await continue_session(chat_id, text, context.bot)
        if continued:
            return

    # If agent is already running, queue the message instead of competing
    active = _active_runs.get(chat_id)
    if active and not active.done():
        _pending_followups.setdefault(chat_id, []).append(text)
        await msg.reply_text("Queued — will process after current run.")
        return

    # Debounce: accumulate rapid messages, process after pause
    buf = _debounce_buffers.get(chat_id)
    if buf is None:
        buf = {"messages": [], "task": None, "last_msg": msg, "bot": context.bot}
        _debounce_buffers[chat_id] = buf

    buf["messages"].append(text)
    buf["last_msg"] = msg
    buf["bot"] = context.bot

    # Show typing indicator immediately so user knows we're processing
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    except Exception:
        logger.debug("typing indicator failed for {}", chat_id)

    # Cancel previous debounce timer if still waiting
    if buf["task"] is not None and not buf["task"].done():
        buf["task"].cancel()

    async def _flush():
        from .config import Config
        delay = Config.load().get("debounce_seconds", _DEBOUNCE_SECONDS)
        await asyncio.sleep(delay)
        pending = _debounce_buffers.pop(chat_id, None)
        if not pending or not pending["messages"]:
            return
        combined = "\n".join(pending["messages"])
        last_msg = pending["last_msg"]
        bot = pending["bot"]
        agent_msg = f"[chat_id={chat_id} message_id={last_msg.message_id}]\n{combined}"
        await _run_agent_and_reply(bot, last_msg, chat_id, agent_msg)

    buf["task"] = asyncio.create_task(_flush())


def _extract_reaction_emojis(added: list) -> list[str]:
    return [
        r.emoji if hasattr(r, "emoji") else f"(custom:{r.custom_emoji_id})"
        for r in added
        if hasattr(r, "emoji") or hasattr(r, "custom_emoji_id")
    ]


@require_allowed
async def on_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle message reactions — pass them to the agent as feedback."""
    reaction = update.message_reaction
    if not reaction:
        return
    chat_id = str(reaction.chat.id)
    added = [r for r in (reaction.new_reaction or []) if r not in (reaction.old_reaction or [])]
    emojis = _extract_reaction_emojis(added)
    if not emojis:
        return
    emoji_str = " ".join(emojis)
    logger.info("Reaction [{}]: {}", chat_id, emoji_str)
    active = _active_runs.get(chat_id)
    if active and not active.done():
        _pending_followups.setdefault(chat_id, []).append(f"[User reacted with: {emoji_str}]")
        return
    await _run_agent_and_reply(context.bot, None, chat_id, f"[User reacted to a previous message with: {emoji_str}]")


@require_allowed
async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voice messages — download, tell the agent about it."""
    chat_id = str(update.effective_chat.id)
    voice = update.message.voice
    caption = update.message.caption or ""
    try:
        file = await context.bot.get_file(voice.file_id)
        dest = workspace.UPLOADS_DIR / f"{voice.file_unique_id}.ogg"
        await file.download_to_drive(str(dest))
    except Exception as e:
        logger.exception("Error downloading voice: {}", e)
        await update.message.reply_text(_classify_error(e))
        return
    agent_msg = (
        f"[chat_id={chat_id} message_id={update.message.message_id}]\n"
        f"[User sent a voice message ({voice.duration}s). Saved to: {dest}]\n\n{caption}"
    )
    await _run_agent_and_reply(context.bot, update.message, chat_id, agent_msg)


@require_allowed
async def on_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle video messages — download and pass to agent."""
    chat_id = str(update.effective_chat.id)
    video = update.message.video or update.message.animation
    caption = update.message.caption or ""
    try:
        file = await context.bot.get_file(video.file_id)
        ext = "mp4" if update.message.video else "gif"
        dest = workspace.UPLOADS_DIR / f"{video.file_unique_id}.{ext}"
        await file.download_to_drive(str(dest))
    except Exception as e:
        logger.exception("Error downloading video: {}", e)
        await update.message.reply_text(_classify_error(e))
        return
    duration = getattr(video, "duration", 0)
    agent_msg = (
        f"[chat_id={chat_id} message_id={update.message.message_id}]\n"
        f"[User sent a video ({duration}s). Saved to: {dest}]\n\n{caption}"
    )
    await _run_agent_and_reply(context.bot, update.message, chat_id, agent_msg)


@require_allowed
async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    doc = update.message.document
    caption = update.message.caption or ""
    try:
        file = await context.bot.get_file(doc.file_id)
        raw_name = doc.file_name or f"{doc.file_unique_id}.bin"
        safe_name = Path(raw_name).name
        dest = workspace.UPLOADS_DIR / safe_name
        await file.download_to_drive(str(dest))
    except Exception as e:
        logger.exception("Error downloading document: {}", e)
        await update.message.reply_text(_classify_error(e))
        return
    mime = doc.mime_type or "application/octet-stream"
    agent_msg = f"[chat_id={chat_id} message_id={update.message.message_id}]\n[User sent file '{safe_name}' ({mime}). Saved to: {dest}]\n\n{caption}"
    await _run_agent_and_reply(context.bot, update.message, chat_id, agent_msg)


@require_allowed
async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    caption = update.message.caption or ""
    if not update.message.photo:
        await update.message.reply_text("No photo data received.")
        return
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        dest = workspace.UPLOADS_DIR / f"{photo.file_unique_id}.jpg"
        await file.download_to_drive(str(dest))
    except Exception as e:
        logger.exception("Error downloading photo: {}", e)
        await update.message.reply_text(_classify_error(e))
        return
    agent_msg = f"[chat_id={chat_id} message_id={update.message.message_id}]\n[User sent a photo. Saved to: {dest}]\n\n{caption}"
    await _run_agent_and_reply(context.bot, update.message, chat_id, agent_msg)
