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
    get_tools_used,
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

# on_agent_callback is defined below (needs _run_agent_and_reply from this module)
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
_followup_timers: dict[str, asyncio.Task] = {}  # chat_id -> scheduled follow-up task
_FOLLOWUP_DELAY = 600  # seconds

_PROMISE_RE = re.compile(
    r"\b(I'?ll|I will|let me|will do|going to|I can do that|I'?ll get back|I'?ll set|I'?ll look|I'?ll check|I'?ll take care)\b",
    re.IGNORECASE,
)


def _detect_promise(text: str) -> str | None:
    """Return a short snippet if the text contains a promise, else None."""
    m = _PROMISE_RE.search(text)
    if not m:
        return None
    # Find the sentence containing the match
    for sentence in re.split(r"[.!?\n]", text):
        if _PROMISE_RE.search(sentence):
            snippet = sentence.strip()
            return snippet[:120] if snippet else None


def _cancel_followup_timer(chat_id: str) -> None:
    task = _followup_timers.pop(chat_id, None)
    if task and not task.done():
        task.cancel()


def _maybe_schedule_followup(bot, chat_id: str, reply: str) -> None:
    snippet = _detect_promise(reply)
    if not snippet:
        return
    _cancel_followup_timer(chat_id)

    async def _fire():
        await asyncio.sleep(_FOLLOWUP_DELAY)
        _followup_timers.pop(chat_id, None)
        prompt = (
            f"[System: {_FOLLOWUP_DELAY // 60} minutes ago you told the user: \"{snippet}\". "
            "Check whether you've completed this. If done, send a brief confirmation. "
            "If not, complete it now and report back.]"
        )
        await _run_agent_and_reply(bot, None, chat_id, prompt)

    def _on_followup_done(t: asyncio.Task) -> None:
        if not t.cancelled() and t.exception():
            logger.warning("Follow-up task failed for {}: {}", chat_id, t.exception())

    task = asyncio.create_task(_fire())
    task.add_done_callback(_on_followup_done)
    _followup_timers[chat_id] = task


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


_TOOL_NOISE_PHRASES = {
    "(no response)",
    "No response requested.",
    "No response needed.",
    "No reply needed.",
    "(No message — standing by.)",
}

def _should_suppress_reply(reply: str, chat_id: str) -> bool:
    """Return True if the reply should not be sent to the user."""
    if not reply or reply in _TOOL_NOISE_PHRASES or reply.startswith("Done. (used:"):
        return True
    return any(t.endswith("telegram_send") for t in get_tools_used(chat_id))


def _format_activity(label: str, elapsed: float) -> str:
    """Format tool activity status for display."""
    if elapsed >= 3.0:
        return f"\U0001f527 {label}\u2026 ({elapsed:.0f}s)"
    return f"\U0001f527 {label}\u2026"


_ERROR_MESSAGES = {
    asyncio.TimeoutError: "Timed out — no response from Claude. Try again, or /reset if it keeps happening.",
    PermissionError: "Permission denied. Try /restart.",
    ConnectionError: "Connection error. Check your network and try again.",
    OSError: "System error. Try again, or /restart if it persists.",
}

_ERROR_PATTERNS: list[tuple[str, str]] = [
    ("rate limit", "Rate limited — Claude API is busy. Wait a minute and try again."),
    ("429", "Rate limited — too many requests. Wait a minute and try again."),
    ("overloaded", "Claude is overloaded. Try again in a few minutes, or switch to a faster model with /model."),
    ("529", "Claude is overloaded. Try again in a few minutes."),
    ("401", "Authentication failed. Your API key or login may have expired. Check `indieclaw setup-token`."),
    ("authentication", "Authentication failed. Run `indieclaw setup-token` to refresh credentials."),
    ("403", "Access denied. Your account may not have access to this model. Try /model to switch."),
    ("insufficient_quota", "API quota exceeded. Check your Anthropic billing."),
    ("context_length", "Message too long for the model. Try /reset to start fresh, or send a shorter message."),
    ("invalid_api_key", "Invalid API key. Run `indieclaw setup-token` to fix."),
]


def _classify_error(e: Exception) -> str:
    for cls in type(e).__mro__:
        if cls in _ERROR_MESSAGES:
            return _ERROR_MESSAGES[cls]
    if type(e).__name__ == "TimeoutExpired":
        return "Timed out — command took too long. Try again, or /reset if it keeps happening."
    err_str = str(e).lower()
    for pattern, message in _ERROR_PATTERNS:
        if pattern in err_str:
            return message
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


async def _reply_html(message, text: str) -> None:
    """Reply with HTML, falling back to stripped plain text on failure."""
    try:
        await message.reply_text(text, parse_mode="HTML")
    except Exception:
        await message.reply_text(_RE_STRIP_HTML.sub("", text))


async def _reply_chunked(message, text: str) -> None:
    """Send text in <=MAX_TG_MSG-char chunks with HTML, falling back to plain text."""
    formatted = _to_telegram_html(text)
    if not formatted:
        return
    for i in range(0, len(formatted), MAX_TG_MSG):
        await _reply_html(message, formatted[i : i + MAX_TG_MSG])


async def _send_reply(bot, message, chat_id: str, reply: str) -> None:
    """Send the agent reply via the appropriate channel."""
    if message:
        await _reply_chunked(message, reply)
    else:
        fmt = _to_telegram_html(reply)
        try:
            await bot.send_message(chat_id=chat_id, text=fmt, parse_mode="HTML")
        except Exception as html_err:
            logger.debug("HTML send failed, falling back to plain text: {}", html_err)
            await bot.send_message(chat_id=chat_id, text=_RE_STRIP_HTML.sub("", fmt))


_PREVIEW_INTERVAL = 1.0  # seconds between live preview edits


async def _preview_sender(bot, chat_id: str, accumulated: list[str], done_event: asyncio.Event, placeholder_id: list) -> None:
    """Edit the placeholder message with streamed content + tool activity."""
    last_text = ""
    while not done_event.is_set():
        preview = ""
        activity = get_tool_activity(chat_id)
        if activity:
            label, elapsed = activity
            preview = _format_activity(label, elapsed)
        if accumulated:
            content = "".join(accumulated)[:MAX_TG_MSG - 200]
            preview = f"{preview}\n\n{content}" if preview else content
        if preview and preview != last_text and placeholder_id:
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=placeholder_id[0], text=preview)
                last_text = preview
            except Exception:
                logger.debug("preview edit failed", exc_info=True)
        try:
            await asyncio.wait_for(done_event.wait(), timeout=_PREVIEW_INTERVAL)
        except TimeoutError:
            pass


async def _run_agent_and_reply_streaming(
    bot, message, chat_id: str, agent_msg: str,
) -> None:
    placeholder_id: list[int] = []
    try:
        ph = await bot.send_message(chat_id=chat_id, text="\U0001f527 working\u2026")
        placeholder_id.append(ph.message_id)
    except Exception:
        logger.debug("placeholder send failed", exc_info=True)

    accumulated: list[str] = []
    done_event = asyncio.Event()
    sender_task = asyncio.create_task(_preview_sender(bot, chat_id, accumulated, done_event, placeholder_id))
    try:
        async for event_type, data in agent_run_streaming(chat_id=chat_id, user_message=agent_msg):
            if event_type == "text_delta":
                accumulated.append(data)
            elif event_type == "done":
                done_event.set()
                if _should_suppress_reply(data, chat_id):
                    return
                await _send_reply(bot, message, chat_id, data)
                _maybe_schedule_followup(bot, chat_id, data)
    except asyncio.CancelledError:
        if message and chat_id not in _debounce_buffers:
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
        if placeholder_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=placeholder_id[0])
            except Exception:
                logger.debug("placeholder delete failed", exc_info=True)


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
    _active_runs[chat_id] = asyncio.current_task()
    try:
        if message and not chat_id.startswith("cron:") and get_streaming():
            await _run_agent_and_reply_streaming(
                bot, message, chat_id, agent_msg,
            )
            return

        try:
            async with _TypingLoop(bot, chat_id):
                reply = await agent_run(chat_id=chat_id, user_message=agent_msg)
            # Release lock immediately after agent_run() returns — don't hold it
            # while background sub-agents are still running (they keep the Claude
            # session alive, which would block new messages from being processed).
            if _active_runs.get(chat_id) is asyncio.current_task():
                _active_runs.pop(chat_id, None)
                await _drain_followups(bot, chat_id)
            is_noise = not reply or reply in _TOOL_NOISE_PHRASES or reply.startswith("Done. (used:")
            sent_via_tool = any(t.endswith("telegram_send") for t in get_tools_used(chat_id))
            if is_noise:
                if not sent_via_tool:
                    await _send_reply(bot, message, chat_id, "\u2705 Done.")
                return
            if sent_via_tool:
                return
            await _send_reply(bot, message, chat_id, reply)
            _maybe_schedule_followup(bot, chat_id, reply)
        except asyncio.CancelledError:
            # If debounce buffer exists for this chat, we're being interrupted
            # by a new message — stay silent, the restart will handle it.
            if message and chat_id not in _debounce_buffers:
                await message.reply_text("Stopped.")
        except Exception as e:
            logger.exception("Error: {}", e)
            err_text = _classify_error(e)
            if message:
                await message.reply_text(err_text)
            else:
                try:
                    await bot.send_message(chat_id=int(chat_id), text=err_text)
                except Exception:
                    logger.debug("failed to send error notification for chat_id={}", chat_id)
    finally:
        # Fallback cleanup in case the early release above didn't run (e.g. exception path)
        if _active_runs.get(chat_id) is asyncio.current_task():
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
    await _reply_html(update.message, text)


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
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.edited_message or update.message
    chat_id = str(update.effective_chat.id)
    text = msg.text or ""
    is_edit = update.edited_message is not None
    logger.info("{} [{}]: {}", "Edit" if is_edit else "Incoming", chat_id, text[:80])

    _cancel_followup_timer(chat_id)

    # If agent is already running, either interrupt or queue based on config
    active = _active_runs.get(chat_id)
    if active and not active.done():
        from .config import Config
        interrupt = Config.load().get("interrupt_on_message", False)
        if interrupt:
            active.cancel()
            _active_runs.pop(chat_id, None)
            prior = _pending_followups.pop(chat_id, [])
            buf = _debounce_buffers.get(chat_id)
            if buf is None:
                buf = {"messages": [], "task": None, "last_msg": msg, "bot": context.bot}
                _debounce_buffers[chat_id] = buf
            buf["messages"].extend(prior)
        else:
            _pending_followups.setdefault(chat_id, []).append(text)
            return

    buf = _debounce_buffers.get(chat_id)
    if buf is None:
        buf = {"messages": [], "task": None, "last_msg": msg, "bot": context.bot}
        _debounce_buffers[chat_id] = buf

    buf["messages"].append(text)
    buf["last_msg"] = msg
    buf["bot"] = context.bot

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    except Exception:
        logger.debug("typing indicator failed for {}", chat_id)

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


@require_allowed
async def on_agent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard button taps from telegram_send_buttons."""
    cb = update.callback_query
    await cb.answer()
    data = (cb.data or "")
    if data.startswith("agent:"):
        data = data[len("agent:"):]
    chat_id = str(update.effective_chat.id)
    # Remove the keyboard so the button can't be tapped twice
    try:
        await cb.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        logger.debug("Could not remove inline keyboard: {}", e)
    agent_msg = (
        f"[chat_id={chat_id} message_id={cb.message.message_id}]\n"
        f"[User tapped button: {data}]"
    )
    await _run_agent_and_reply(context.bot, None, chat_id, agent_msg)


@require_allowed
async def on_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle message reactions — pass them to the agent as feedback."""
    reaction = update.message_reaction
    if not reaction:
        return
    chat_id = str(reaction.chat.id)
    added = [r for r in (reaction.new_reaction or []) if r not in (reaction.old_reaction or [])]
    emojis = [
        getattr(r, "emoji", None) or f"(custom:{r.custom_emoji_id})"
        for r in added
        if hasattr(r, "emoji") or hasattr(r, "custom_emoji_id")
    ]
    if not emojis:
        return
    emoji_str = " ".join(emojis)
    logger.info("Reaction [{}]: {}", chat_id, emoji_str)
    active = _active_runs.get(chat_id)
    if active and not active.done():
        _pending_followups.setdefault(chat_id, []).append(f"[User reacted with: {emoji_str}]")
        return
    await _run_agent_and_reply(context.bot, None, chat_id, f"[User reacted to a previous message with: {emoji_str}]")


async def _transcribe_voice(path: str) -> str | None:
    """Transcribe a voice file via openai-whisper. Returns transcript or None on failure."""
    import subprocess as _sp
    script = (
        "import whisper, sys; "
        f"model = whisper.load_model('tiny'); "
        f"print(model.transcribe({path!r})['text'].strip())"
    )
    try:
        result = await asyncio.to_thread(
            _sp.run,
            ["uv", "run", "--with", "openai-whisper", "python3", "-c", script],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        if result.stderr:
            logger.warning("Whisper stderr: {}", result.stderr[:200])
    except (OSError, _sp.TimeoutExpired) as e:
        logger.warning("Voice transcription failed: {}", e)
    return None


async def _download_media(bot, file_id: str, dest: Path) -> Path | None:
    """Download a Telegram file to *dest*. Returns dest on success, None on failure."""
    try:
        file = await bot.get_file(file_id)
        await file.download_to_drive(str(dest))
        return dest
    except Exception as e:
        logger.exception("Error downloading media: {}", e)
        return None


@require_allowed
async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voice messages — download, tell the agent about it."""
    chat_id = str(update.effective_chat.id)
    voice = update.message.voice
    caption = update.message.caption or ""
    dest = workspace.UPLOADS_DIR / f"{voice.file_unique_id}.ogg"
    if not await _download_media(context.bot, voice.file_id, dest):
        await update.message.reply_text("Failed to download voice message.")
        return
    transcript = await _transcribe_voice(str(dest))
    detail = f"{transcript}" if transcript else f"Saved to: {dest}"
    agent_msg = (
        f"[chat_id={chat_id} message_id={update.message.message_id}]\n"
        f"[User sent a voice message ({voice.duration}s): {detail}]\n\n{caption}"
    )
    await _run_agent_and_reply(context.bot, update.message, chat_id, agent_msg)


@require_allowed
async def on_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle video messages — download and pass to agent."""
    chat_id = str(update.effective_chat.id)
    video = update.message.video or update.message.animation
    caption = update.message.caption or ""
    ext = "mp4" if update.message.video else "gif"
    dest = workspace.UPLOADS_DIR / f"{video.file_unique_id}.{ext}"
    if not await _download_media(context.bot, video.file_id, dest):
        await update.message.reply_text("Failed to download video.")
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
    safe_name = Path(doc.file_name or f"{doc.file_unique_id}.bin").name
    dest = workspace.UPLOADS_DIR / safe_name
    if not await _download_media(context.bot, doc.file_id, dest):
        await update.message.reply_text("Failed to download document.")
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
    photo = update.message.photo[-1]
    dest = workspace.UPLOADS_DIR / f"{photo.file_unique_id}.jpg"
    if not await _download_media(context.bot, photo.file_id, dest):
        await update.message.reply_text("Failed to download photo.")
        return
    agent_msg = f"[chat_id={chat_id} message_id={update.message.message_id}]\n[User sent a photo. Saved to: {dest}]\n\n{caption}"
    await _run_agent_and_reply(context.bot, update.message, chat_id, agent_msg)
