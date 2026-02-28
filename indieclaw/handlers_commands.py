from __future__ import annotations

import asyncio
import html as _html
import os

import yaml
from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from . import workspace
from .agent import (
    AVAILABLE_EFFORTS,
    AVAILABLE_MODELS,
    get_current_effort,
    get_current_model,
    get_last_usage,
    get_streaming,
    set_effort,
    set_model,
    set_streaming,
)
from .auth import is_allowed, require_allowed
from .session_state import SessionState
from .tool_loader import load_custom_tools
from .tools_sdk import CUSTOM_TOOLS
from .version import check_remote_version as _check_remote_version
from .version import get_update_summary as _get_update_summary
from .version import local_version as _local_version


def _last_turn_stats(chat_id: str) -> str:
    usage = get_last_usage(chat_id)
    if not usage:
        return ""
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_write = usage.get("cache_creation_input_tokens", 0)
    cache_str = f" | cache down {cache_read} up {cache_write}" if (cache_read or cache_write) else ""
    return f"\nLast turn: {inp}in/{out}out{cache_str}"


@require_allowed
async def on_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "<b>IndieClaw</b> \u2014 your personal AI agent.\n\n"
        "Just send a message, photo, file, or voice note.\n\n"
        "<b>Chat</b>\n"
        "/btw \u2014 side question (no history)\n"
        "/cc \u2014 live Claude Code session\n"
        "/streaming \u2014 toggle response streaming\n\n"
        "<b>Session</b>\n"
        "/reset \u2014 clear conversation history\n\n"
        "<b>Config</b>\n"
        "/model \u2014 switch Claude model (opus/sonnet/haiku)\n"
        "/effort \u2014 thinking effort level\n"
        "/status \u2014 current config and stats\n\n"
        "<b>System</b>\n"
        "/crons \u2014 scheduled jobs\n"
        "/restart \u2014 restart the bot\n"
        "/update \u2014 update and restart"
    )
    try:
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception:
        await update.message.reply_text(text)


@require_allowed
async def on_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    dynamic_tools = load_custom_tools()
    skills_dir = workspace.SKILLS_DIR
    skill_count = sum(1 for d in skills_dir.iterdir() if d.is_dir()) if skills_dir.exists() else 0
    try:
        memory_lines = len(workspace.MEMORY.read_text().splitlines())
    except FileNotFoundError:
        memory_lines = 0
    dynamic_names = ", ".join(t.name for t in dynamic_tools) if dynamic_tools else "none"
    cost_line = _last_turn_stats(str(update.effective_chat.id))
    usage_today = SessionState.load().get_usage_today()
    today_line = f"\nToday: {usage_today['input_tokens']}in/{usage_today['output_tokens']}out | {usage_today['turns']} turns"
    from .browser import BrowserManager
    from .version import local_version
    text = (
        f"<b>IndieClaw</b> v{local_version()}\n\n"
        f"<b>Model:</b> <code>{get_current_model()}</code>\n"
        f"<b>Effort:</b> {get_current_effort()}\n"
        f"<b>Browser:</b> {BrowserManager.get().backend}\n\n"
        f"<b>Tools:</b> 5 built-in + {len(CUSTOM_TOOLS) + 2} SDK + {len(dynamic_tools)} dynamic"
        f"{(' (' + dynamic_names + ')') if dynamic_tools else ''}\n"
        f"<b>Skills:</b> {skill_count}\n"
        f"<b>Memory:</b> {memory_lines} lines"
        f"{cost_line}"
        f"{today_line}"
    )
    try:
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception:
        await update.message.reply_text(text)



@require_allowed
async def on_crons(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    crons_path = workspace.CRONS
    if not crons_path.exists():
        await update.message.reply_text("No crons.yaml found.")
        return
    data = yaml.safe_load(crons_path.read_text()) or {}
    jobs = data.get("jobs", [])
    if not jobs:
        await update.message.reply_text("No scheduled jobs.")
        return
    lines = []
    for job in jobs:
        jid = job.get("id", "?")
        cron = job.get("cron", "?")
        prompt = _html.escape(job.get("prompt", "")[:60])
        lines.append(f"\u23f0 <b>{jid}</b> <code>{cron}</code>\n   {prompt}")
    text = f"<b>Scheduled jobs</b> ({len(jobs)})\n\n" + "\n\n".join(lines)
    try:
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception:
        await update.message.reply_text(text)


_MODEL_ALIASES: dict[str, str] = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}


@require_allowed
async def on_model(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    arg = (update.message.text or "").split(maxsplit=1)[1].strip().lower() if len((update.message.text or "").split()) > 1 else ""

    if arg:
        model_id = _MODEL_ALIASES.get(arg) or arg
        if model_id not in {mid for mid, _ in AVAILABLE_MODELS}:
            names = ", ".join(_MODEL_ALIASES.keys())
            await update.message.reply_text(f"Unknown model. Try: {names}")
            return
        await set_model(model_id)
        label = next((lbl for mid, lbl in AVAILABLE_MODELS if mid == model_id), model_id)
        await update.message.reply_text(
            f"Switched to <b>{label}</b>\n<code>{model_id}</code>",
            parse_mode="HTML",
        )
        return

    current = get_current_model()
    keyboard = [
        [InlineKeyboardButton(
            f"{'✓ ' if mid == current else ''}{lbl}",
            callback_data=f"model:{mid}",
        )]
        for mid, lbl in AVAILABLE_MODELS
    ]
    await update.message.reply_text(
        "Select a Claude model:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _handle_selection_callback(
    update: Update, prefix: str, choices: list[tuple[str, str]],
    apply_fn, confirm_msg: str,
) -> None:
    """Generic handler for inline keyboard selection callbacks (model, effort, etc.)."""
    cb = update.callback_query
    await cb.answer()
    if not (cb.data or "").startswith(f"{prefix}:"):
        return
    if not is_allowed(update.effective_chat.id):
        await cb.edit_message_text("Not authorised.")
        return
    selected = cb.data[len(f"{prefix}:"):]
    if selected not in {cid for cid, _ in choices}:
        await cb.edit_message_text(f"Unknown {prefix}.")
        return
    await apply_fn(selected)
    label = next((lbl for cid, lbl in choices if cid == selected), selected)
    await cb.edit_message_text(
        f"\u2713 {confirm_msg.format(label=label, selected=selected)}",
        parse_mode="HTML",
    )


async def on_model_callback(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_selection_callback(
        update, "model", AVAILABLE_MODELS, set_model,
        "Switched to <b>{label}</b>\n<code>{selected}</code>\n\nAll sessions reset \u2014 next message uses the new model.",
    )


@require_allowed
async def on_effort(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    current = get_current_effort()
    keyboard = [
        [InlineKeyboardButton(
            f"{'✓ ' if eid == current else ''}{lbl}",
            callback_data=f"effort:{eid}",
        )]
        for eid, lbl in AVAILABLE_EFFORTS
    ]
    await update.message.reply_text(
        "Select thinking effort level:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def on_effort_callback(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_selection_callback(
        update, "effort", AVAILABLE_EFFORTS, set_effort,
        "Effort set to <b>{label}</b>\n\nAll sessions reset \u2014 next message uses the new effort level.",
    )


@require_allowed
async def on_restart(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    import signal
    await update.message.reply_text("Restarting…")
    os.kill(os.getpid(), signal.SIGTERM)


@require_allowed
async def on_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    import signal
    import subprocess as _subprocess

    old_version = _local_version()
    source = os.getenv("INDIECLAW_SOURCE", "git+https://github.com/saikatkumardey/indieclaw")

    placeholder = await update.message.reply_text("Checking for updates…")

    async def _edit(text: str) -> None:
        try:
            await placeholder.edit_text(text)
        except Exception:
            logger.debug("failed to edit placeholder text", exc_info=True)

    remote = await asyncio.to_thread(_check_remote_version, source)
    if remote and remote == old_version:
        await _edit(f"Already on latest (v{old_version}).")
        return

    await _edit("Update available — installing…")
    try:
        result = await asyncio.to_thread(
            _subprocess.run,
            ["uv", "tool", "install", "--upgrade", source],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as e:
        await _edit(f"Update failed: {e}")
        return

    if result.returncode != 0:
        await _edit(f"Update failed:\n{result.stderr[:500]}")
        return

    summary = await asyncio.to_thread(_get_update_summary, source, old_version)

    await _edit(f"Updated. Restarting…\n\n{summary}")
    os.kill(os.getpid(), signal.SIGTERM)


@require_allowed
async def on_streaming(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    current = get_streaming()
    new_val = not current
    await set_streaming(new_val)
    if new_val:
        await update.message.reply_text("Streaming ON \u2014 responses appear as they're generated.")
    else:
        await update.message.reply_text("Streaming OFF \u2014 responses sent when complete.")
