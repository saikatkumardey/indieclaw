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
    get_tool_timings,
    set_effort,
    set_model,
    set_streaming,
)
from .auth import is_allowed, require_allowed
from .browser import BrowserManager
from .config import Config
from .session_state import SessionState
from .tool_loader import load_custom_tools
from .tools_sdk import CUSTOM_TOOLS
from .version import check_remote_version as _check_remote_version
from .version import get_update_summary as _get_update_summary
from .version import local_version
from .version import local_version as _local_version


async def _reply_html(message, text: str) -> None:
    """Send HTML reply, falling back to plain text if parsing fails."""
    try:
        await message.reply_text(text, parse_mode="HTML")
    except Exception:
        await message.reply_text(text)


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return f"{n:,}"


def _last_turn_section(chat_id: str) -> str:
    usage = get_last_usage(chat_id)
    if not usage:
        return "\n\n<b>\U0001f4ca Last turn</b>\n  No recent activity."

    timings = get_tool_timings(chat_id)
    tool_str = ", ".join(f"{name} ({elapsed:.1f}s)" for name, elapsed in timings) if timings else "none"

    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_pct = f" ({_fmt_tokens(cache_read)} cached)" if cache_read else ""

    model = usage.get("_model", "")
    from .session_state import estimate_cost
    cost = estimate_cost(model, inp, out, cache_read, usage.get("cache_creation_input_tokens", 0))

    return (
        f"\n\n<b>\U0001f4ca Last turn</b>"
        f"\n  Tools: {tool_str}"
        f"\n  Tokens: {_fmt_tokens(inp)} in{cache_pct} / {_fmt_tokens(out)} out"
        f"\n  Cost: ~${cost:.3f}"
    )


def _today_section() -> str:
    usage = SessionState.load().get_usage_today()
    turns = usage.get("turns", 0)
    if turns == 0:
        return ""

    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_tokens", 0)
    cache_pct = f" ({_fmt_tokens(cache_read)} cached, {cache_read * 100 // inp}%)" if inp and cache_read else ""
    cost = usage.get("cost_usd", 0.0)

    models = usage.get("models", {})
    model_parts = []
    for m, count in sorted(models.items(), key=lambda x: -x[1]):
        short = m.split("-")[1] if "-" in m else m
        model_parts.append(f"{short} \u00d7{count}")
    model_str = ", ".join(model_parts) if model_parts else ""

    lines = [
        f"\n\n<b>\U0001f4ca Today</b> ({turns} turns)",
        f"\n  Tokens: {_fmt_tokens(inp)} in{cache_pct} / {_fmt_tokens(out)} out",
        f"\n  Cost: ~${cost:.2f}",
    ]
    if model_str:
        lines.append(f"\n  Models: {model_str}")
    return "".join(lines)


def _history_section() -> str:
    history = SessionState.load().get_usage_history()
    if not history:
        return ""

    total_turns = sum(h.get("turns", 0) for h in history)
    total_cost = sum(h.get("cost_usd", 0.0) for h in history)
    total_cache_read = sum(h.get("cache_read_tokens", 0) for h in history)
    total_input = sum(h.get("input_tokens", 0) for h in history)

    avg_cost = total_cost / total_turns if total_turns else 0
    cache_pct = total_cache_read * 100 // total_input if total_input else 0

    return (
        f"\n\n<b>\U0001f4ca Last {len(history)} days</b>"
        f"\n  Turns: {total_turns} | Cost: ~${total_cost:.2f}"
        f"\n  Avg: ${avg_cost:.3f}/turn | Cache: {cache_pct}%"
    )


@require_allowed
async def on_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "<b>IndieClaw</b> \u2014 your personal AI agent.\n\n"
        "Just send a message, photo, file, or voice note.\n\n"
        "<b>Chat</b>\n"
        "/btw \u2014 side question (no history)\n"
        "/streaming \u2014 toggle response streaming\n\n"
        "<b>Session</b>\n"
        "/reset \u2014 clear conversation history\n"
        "/stop \u2014 cancel the current agent run\n\n"
        "<b>Config</b>\n"
        "/model \u2014 switch Claude model (opus/sonnet/haiku)\n"
        "/effort \u2014 thinking effort level\n"
        "/status \u2014 current config and stats\n"
        "<b>System</b>\n"
        "/crons \u2014 scheduled jobs\n"
        "/restart \u2014 restart the bot\n"
        "/update \u2014 update and restart"
    )
    await _reply_html(update.message, text)


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

    chat_id = str(update.effective_chat.id)

    text = (
        f"<b>IndieClaw</b> v{local_version()}\n"
        f"<b>Model:</b> <code>{get_current_model()}</code> | <b>Effort:</b> {get_current_effort()}\n"
        f"<b>Browser:</b> {BrowserManager.get().backend}\n\n"
        f"<b>Tools:</b> 5 built-in + {len(CUSTOM_TOOLS) + 2} SDK + {len(dynamic_tools)} dynamic"
        f"{(' (' + dynamic_names + ')') if dynamic_tools else ''}\n"
        f"<b>Skills:</b> {skill_count} | <b>Memory:</b> {memory_lines} lines"
        f"{_last_turn_section(chat_id)}"
        f"{_today_section()}"
        f"{_history_section()}"
    )
    await _reply_html(update.message, text)



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
    await _reply_html(update.message, text)


_MODEL_ALIASES: dict[str, str] = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}


@require_allowed
async def on_model(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    raw = update.message.text or ""
    arg = raw.split(maxsplit=1)[1].strip().lower() if len(raw.split()) > 1 else ""

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
    prefix_colon = f"{prefix}:"
    if not (cb.data or "").startswith(prefix_colon):
        return
    if not is_allowed(update.effective_chat.id):
        await cb.edit_message_text("Not authorised.")
        return
    selected = cb.data[len(prefix_colon):]
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

    # Parse --branch <name> from message text
    text = update.message.text or ""
    parts = text.split()
    branch = None
    for i, p in enumerate(parts):
        if p == "--branch" and i + 1 < len(parts):
            branch = parts[i + 1]
            break

    old_version = _local_version()
    source = os.getenv("INDIECLAW_SOURCE", "git+https://github.com/saikatkumardey/indieclaw")
    if branch:
        source = f"{source}@{branch}"

    placeholder = await update.message.reply_text("Checking for updates…")

    async def _edit(text: str) -> None:
        try:
            await placeholder.edit_text(text)
        except Exception:
            logger.debug("failed to edit placeholder text", exc_info=True)

    if not branch:
        remote = await asyncio.to_thread(_check_remote_version, source)
        if remote and remote == old_version:
            await _edit(f"Already on latest (v{old_version}).")
            return

    label = f"branch `{branch}`" if branch else "latest"
    await _edit(f"Installing {label}…")
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

    from . import workspace
    workspace.set_branch(branch)  # None clears the file

    if branch:
        await _edit(f"Installed from branch `{branch}`. Restarting…")
    else:
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


