from __future__ import annotations

import asyncio
import json
import os
import re as _re
import subprocess
import time as _time
from collections import defaultdict
from datetime import datetime, timezone

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    create_sdk_mcp_server,
)
from claude_agent_sdk.types import AgentDefinition, HookMatcher, PostToolUseHookInput, PreToolUseHookInput, SyncHookJSONOutput
from loguru import logger

from . import workspace
from .config import Config
from .prompt_builder import build_system_prompt as _system_prompt
from .session_state import SessionState
from .tool_loader import load_custom_tools
from .tools_sdk import CUSTOM_TOOLS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AVAILABLE_MODELS: list[tuple[str, str]] = [
    ("claude-opus-4-6",           "Opus 4.6 — Most capable"),
    ("claude-sonnet-4-6",         "Sonnet 4.6 — Balanced (default)"),
    ("claude-haiku-4-5-20251001", "Haiku 4.5 — Fastest"),
]

AVAILABLE_EFFORTS: list[tuple[str, str]] = [
    ("low",    "Low — fast, minimal thinking (default)"),
    ("medium", "Medium — balanced thinking"),
    ("high",   "High — deeper reasoning"),
    ("max",    "Max — maximum thinking budget"),
]

# ---------------------------------------------------------------------------
# Session state (minimal)
# ---------------------------------------------------------------------------

_session_ids: dict[str, str] = {}        # chat_id -> last SDK session_id
_session_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_last_usage: dict[str, dict] = {}        # chat_id -> last usage dict

# ---------------------------------------------------------------------------
# Tool activity tracking (read by handlers.py for UX indicators)
# ---------------------------------------------------------------------------

_tool_activity: dict[str, str] = {}       # chat_id -> human-readable tool label
_tool_start_time: dict[str, float] = {}   # chat_id -> monotonic timestamp
_tools_used_this_turn: dict[str, set[str]] = {}  # chat_id -> all tool names used
_tool_timings: dict[str, list[tuple[str, float]]] = {}  # chat_id -> [(tool_name, elapsed_s)]
_pending_tool_starts: dict[str, dict[str, float]] = {}   # chat_id -> {tool_use_id: start_time}

_TOOL_LABELS: dict[str, str] = {
    "Bash": "running command",
    "WebSearch": "searching",
    "browse": "browsing",
    "browser_click": "browsing",
    "browser_type": "browsing",
    "browser_screenshot": "browsing",
    "browser_eval": "browsing",
    "Read": "reading files",
    "Write": "writing files",
}


def _tool_label(tool_name: str) -> str:
    if tool_name in _TOOL_LABELS:
        return _TOOL_LABELS[tool_name]
    if tool_name.startswith("mcp__indieclaw__telegram"):
        return "sending message"
    return tool_name


def get_tool_activity(chat_id: str) -> tuple[str, float] | None:
    label = _tool_activity.get(chat_id)
    if label is None:
        return None
    start = _tool_start_time.get(chat_id, _time.monotonic())
    return label, _time.monotonic() - start


def get_tools_used(chat_id: str) -> set[str]:
    return set(_tools_used_this_turn.get(chat_id, set()))


def _strip_tool_prefix(name: str) -> str:
    for prefix in ("mcp__indieclaw__", "mcp__dynamic__"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def get_tool_timings(chat_id: str) -> list[tuple[str, float]]:
    return list(_tool_timings.get(chat_id, []))


def clear_tool_activity(chat_id: str) -> None:
    _tool_activity.pop(chat_id, None)
    _tool_start_time.pop(chat_id, None)
    # _tools_used_this_turn is NOT cleared here — the handler needs it after run() returns.
    # It is cleared at the start of the next run() via _tools_used_this_turn.pop(chat_id, None).


_IGNORANCE_PATTERNS = _re.compile(
    r"I don'?t (know|have|recall|remember|see|have any context|have context)"
    r"|I'?m not sure what you'?re referring"
    r"|no context about"
    r"|I don'?t have any (information|context|record)",
    _re.IGNORECASE,
)

_MEMORY_TOOLS = {"Read", "mcp__indieclaw__search_sessions", "WebFetch"}


def _is_lazy_ignorance(reply: str, tools_used: set[str]) -> bool:
    """True if the reply claims ignorance but no memory/log tools were used."""
    if not _IGNORANCE_PATTERNS.search(reply):
        return False
    return not tools_used.intersection(_MEMORY_TOOLS)

# ---------------------------------------------------------------------------
# Dynamic MCP server (module-level, reloaded per message)
# ---------------------------------------------------------------------------

_dynamic_mcp_server = None


def reload_dynamic_tools() -> None:
    global _dynamic_mcp_server
    tools = load_custom_tools()
    _dynamic_mcp_server = (
        create_sdk_mcp_server(name="dynamic", version="1.0.0", tools=tools)
        if tools else None
    )
    logger.info("Loaded {} dynamic tools", len(tools))

# ---------------------------------------------------------------------------
# Model / effort / streaming config
# ---------------------------------------------------------------------------


def get_current_model() -> str:
    return Config.load().get("model")


async def set_model(model_id: str) -> None:
    cfg = Config.load()
    cfg.set("model", model_id)
    os.environ["INDIECLAW_MODEL"] = model_id
    for chat_id in list(_session_ids.keys()):
        await reset_session(chat_id)


def get_current_effort() -> str:
    return Config.load().get("effort")


async def set_effort(effort: str) -> None:
    cfg = Config.load()
    cfg.set("effort", effort)
    for chat_id in list(_session_ids.keys()):
        await reset_session(chat_id)


def get_streaming() -> bool:
    return Config.load().get("streaming")


async def set_streaming(enabled: bool) -> None:
    Config.load().set("streaming", enabled)

# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


async def reset_session(chat_id: str) -> None:
    _session_ids.pop(chat_id, None)
    _last_usage.pop(chat_id, None)
    try:
        from .browser import BrowserManager
        await BrowserManager.get().close_session(chat_id)
    except Exception as e:
        logger.warning("Failed to close browser session for {}: {}", chat_id, e)


def get_last_usage(chat_id: str) -> dict | None:
    return _last_usage.get(chat_id)


# ---------------------------------------------------------------------------
# Session logging
# ---------------------------------------------------------------------------


def session_log(chat_id: str, role: str, content: str | dict) -> None:
    try:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        path = workspace.HOME / "sessions" / f"{today}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": now.isoformat(),
            "chat_id": chat_id,
            "role": role,
            "content": content,
        }
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("session_log failed: {}", e)

# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


_RTK_PATHS = [
    "/home/claude-user/.local/bin/rtk",
    "/usr/local/bin/rtk",
]


def _rtk_bin() -> str | None:
    import shutil
    found = shutil.which("rtk")
    if found:
        return found
    for p in _RTK_PATHS:
        if os.path.isfile(p):
            return p
    return None


def _rtk_rewrite(cmd: str) -> tuple[str, bool]:
    """Rewrite a shell command via `rtk rewrite`. Returns (rewritten_cmd, changed)."""
    rtk = _rtk_bin()
    if not rtk:
        return cmd, False
    try:
        result = subprocess.run(
            [rtk, "rewrite", cmd],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode in (0, 3) and result.stdout.strip():
            rewritten = result.stdout.strip()
            return rewritten, rewritten != cmd
    except Exception as e:
        logger.debug("RTK rewrite failed: {}", e)
    return cmd, False


def _make_hooks(chat_id: str) -> dict:
    async def _on_tool_call(input_data: PreToolUseHookInput, tool_use_id: str | None, context) -> SyncHookJSONOutput:
        tool_name = input_data["tool_name"]
        logger.debug("Tool: {} ({})", tool_name, input_data["tool_use_id"])
        _tool_activity[chat_id] = _tool_label(tool_name)
        _tool_start_time[chat_id] = _time.monotonic()
        _tools_used_this_turn.setdefault(chat_id, set()).add(tool_name)
        _pending_tool_starts.setdefault(chat_id, {})[input_data["tool_use_id"]] = _time.monotonic()

        if tool_name == "Bash":
            cmd = (input_data.get("tool_input") or {}).get("command", "")
            if cmd:
                rewritten, changed = _rtk_rewrite(cmd)
                if changed:
                    return SyncHookJSONOutput(hookSpecificOutput={
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                        "permissionDecisionReason": "RTK auto-rewrite",
                        "updatedInput": {**input_data.get("tool_input", {}), "command": rewritten},
                    })

        return SyncHookJSONOutput()

    async def _on_tool_done(input_data: PostToolUseHookInput, tool_use_id: str | None, context) -> SyncHookJSONOutput:
        tool_use_id_val = input_data["tool_use_id"]
        starts = _pending_tool_starts.get(chat_id, {})
        start = starts.pop(tool_use_id_val, None)
        if start is not None:
            elapsed = round(_time.monotonic() - start, 2)
            bare_name = _strip_tool_prefix(input_data["tool_name"])
            _tool_timings.setdefault(chat_id, []).append((bare_name, elapsed))
        return SyncHookJSONOutput()

    return {
        "PreToolUse": [HookMatcher(hooks=[_on_tool_call])],
        "PostToolUse": [HookMatcher(hooks=[_on_tool_done])],
    }

# ---------------------------------------------------------------------------
# Native subagents
# ---------------------------------------------------------------------------


def _make_agents(chat_id: str) -> dict[str, AgentDefinition] | None:
    if chat_id.startswith("cron:"):
        return None
    return {
        "task-runner": AgentDefinition(
            description="Runs long autonomous tasks: research, file operations, web browsing, code generation. Use for anything that may take multiple tool-use turns.",
            prompt="You are a task runner for IndieClaw. Complete the delegated task autonomously. Use tools as needed. Be concise in your final response.",
            tools=[
                "Bash", "Read", "Write", "WebSearch", "WebFetch",
                "mcp__indieclaw__browse", "mcp__indieclaw__browser_click",
                "mcp__indieclaw__browser_eval", "mcp__indieclaw__browser_screenshot",
                "mcp__indieclaw__browser_type", "mcp__indieclaw__web_fetch",
                "mcp__indieclaw__read_skill", "mcp__indieclaw__search_sessions",
                "mcp__dynamic__*",
            ],
        ),
    }

# ---------------------------------------------------------------------------
# Tool selection & option building
# ---------------------------------------------------------------------------


def _select_tools_for_chat(chat_id: str, cfg: Config) -> list:
    if chat_id.startswith("cron:subconscious"):
        from .tools_sdk import reflect, telegram_send, update_subconscious
        return [telegram_send, update_subconscious, reflect]
    return [*CUSTOM_TOOLS]


def _select_model(chat_id: str, cfg: Config) -> str:
    model = cfg.get("model")
    if chat_id.startswith("cron:subconscious"):
        return cfg.get("subconscious_model") or model
    if chat_id.startswith("cron:"):
        return cfg.get("cron_model") or model
    return model


def _select_max_turns(chat_id: str, cfg: Config) -> int:
    if chat_id.startswith("cron:subconscious"):
        return 3
    return cfg.get("max_turns")


def _select_effort(chat_id: str, cfg: Config) -> str:
    if chat_id.startswith("cron:subconscious"):
        return "low"
    return cfg.get("effort")


def _make_options(chat_id: str, resume: str | None = None) -> ClaudeAgentOptions:
    cfg = Config.load()
    is_cron = chat_id.startswith("cron:")

    indieclaw_tools = _select_tools_for_chat(chat_id, cfg)
    indieclaw_server = create_sdk_mcp_server(name="indieclaw", version="1.0.0", tools=indieclaw_tools)
    indieclaw_tool_names = [f"mcp__indieclaw__{t.name}" for t in indieclaw_tools]

    if is_cron:
        allowed = ["Bash", "Read", "Write", "WebSearch", *indieclaw_tool_names]
    else:
        allowed = [*indieclaw_tool_names]

    mcp_servers = {"indieclaw": indieclaw_server}
    if _dynamic_mcp_server is not None and not chat_id.startswith("cron:subconscious"):
        mcp_servers["dynamic"] = _dynamic_mcp_server
        allowed.append("mcp__dynamic__*")

    return ClaudeAgentOptions(
        model=_select_model(chat_id, cfg),
        system_prompt=_system_prompt(slim=is_cron),
        allowed_tools=allowed,
        disallowed_tools=["WebFetch"],
        mcp_servers=mcp_servers,
        permission_mode="bypassPermissions",
        cwd=str(workspace.HOME),
        max_turns=_select_max_turns(chat_id, cfg),
        effort=_select_effort(chat_id, cfg),
        include_partial_messages=True,
        resume=resume if not is_cron else None,
        max_budget_usd=cfg.get("max_budget_usd") or None,
        hooks=_make_hooks(chat_id),
        agents=_make_agents(chat_id),
    )

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _timestamp_message(user_message: str) -> str:
    return f"[Current time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}]\n\n{user_message}"


_HUMAN_TURN_RE = _re.compile(r"\nHuman:\s*\[Current time:", _re.IGNORECASE)
_NOISE_SUFFIX_RE = _re.compile(r"\n+(No response requested\.|No response needed\.|No reply needed\.)\s*$", _re.IGNORECASE)


def _strip_hallucinated_turns(text: str) -> str:
    """Remove any model-generated fake human turns from the response."""
    m = _HUMAN_TURN_RE.search(text)
    if m:
        return text[:m.start()].rstrip()
    return text


def _strip_noise_suffix(text: str) -> str:
    """Strip trailing 'No response requested.' and similar phrases the model appends."""
    return _NOISE_SUFFIX_RE.sub("", text).rstrip()


def _extract_stream_delta(msg: StreamEvent) -> str | None:
    if msg.parent_tool_use_id is not None:
        return None
    event = msg.event
    if event.get("type") != "content_block_delta":
        return None
    delta = event.get("delta", {})
    if delta.get("type") != "text_delta":
        return None
    return delta.get("text", "") or None


def _record_result(chat_id: str, msg: ResultMessage) -> None:
    usage = msg.usage or {}
    session_log(chat_id, "result", {
        "turns": msg.num_turns,
        "duration_ms": msg.duration_ms,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
        "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
    })
    try:
        SessionState.load().record_turn(chat_id, msg)
    except Exception as e:
        logger.warning("SessionState.record_turn failed: {}", e)

# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------


def _collect_text(msg: AssistantMessage) -> list[str]:
    return [block.text for block in msg.content if isinstance(block, TextBlock)]


def _handle_result(chat_id: str, msg: ResultMessage) -> None:
    if msg.session_id:
        _session_ids[chat_id] = msg.session_id
    _last_usage[chat_id] = msg.usage or {}
    _last_usage[chat_id]["_model"] = getattr(msg, "model", "") or ""
    _record_result(chat_id, msg)


async def _run_once(chat_id: str, user_message: str, options) -> str:
    """Execute a single agent turn, returning the reply text."""
    cfg = Config.load()
    initial_timeout = cfg.get("agent_initial_timeout")
    stall = cfg.get("agent_stall_timeout")

    first_event = True
    try:
        loop = asyncio.get_event_loop()
        deadline = asyncio.timeout(initial_timeout)
        logger.debug("SDK run start for {} (initial={}s, stall={}s)", chat_id, initial_timeout, stall)
        async with deadline, ClaudeSDKClient(options=options) as client:
            await client.query(user_message)
            parts: list[str] = []
            async for msg in client.receive_response():
                if first_event:
                    logger.debug("First SDK event for {} after startup", chat_id)
                    first_event = False
                deadline.reschedule(loop.time() + stall)
                if isinstance(msg, AssistantMessage):
                    parts.extend(_collect_text(msg))
                elif isinstance(msg, SystemMessage) and msg.subtype == "compact_boundary":
                    logger.info("SDK compacted context for {}", chat_id)
                elif isinstance(msg, ResultMessage):
                    _handle_result(chat_id, msg)

        return "\n".join(parts) or "(no response)"
    except TimeoutError:
        phase = "initial" if first_event else "inter-event"
        timeout_val = initial_timeout if first_event else stall
        logger.warning("Agent stalled ({} phase, {}s) for {}", phase, timeout_val, chat_id)
        _session_ids.pop(chat_id, None)
        return f"Stalled — no events for {timeout_val}s ({phase}). Try again or /stop."
    except Exception as e:
        logger.exception("Agent error for {}: {}: {}", chat_id, type(e).__name__, e)
        return f"Something went wrong ({type(e).__name__}). Please try again."


async def run(chat_id: str, user_message: str) -> str:
    reload_dynamic_tools()
    lock = _session_locks[chat_id]
    async with lock:
        session_id = _session_ids.get(chat_id)
        options = _make_options(chat_id, resume=session_id)
        timestamped = _timestamp_message(user_message)
        session_log(chat_id, "user", user_message)
        _tools_used_this_turn.pop(chat_id, None)
        _tool_timings.pop(chat_id, None)
        _pending_tool_starts.pop(chat_id, None)

        reply = _strip_noise_suffix(_strip_hallucinated_turns(await _run_once(chat_id, timestamped, options)))

        # Lazy ignorance check: retry once with a nudge if agent claimed
        # ignorance without checking session logs or memory
        tools_used = _tools_used_this_turn.get(chat_id, set())
        if _is_lazy_ignorance(reply, tools_used):
            logger.info("Lazy ignorance detected for {}, retrying with nudge", chat_id)
            _tools_used_this_turn.pop(chat_id, None)
            nudge = "[System: You claimed ignorance without checking. Search session logs and read MEMORY.md before responding.]"
            options = _make_options(chat_id, resume=_session_ids.get(chat_id))
            reply = _strip_noise_suffix(_strip_hallucinated_turns(await _run_once(chat_id, nudge, options)))

    session_log(chat_id, "assistant", reply)
    clear_tool_activity(chat_id)
    return reply


async def run_streaming(chat_id: str, user_message: str):
    reload_dynamic_tools()
    lock = _session_locks[chat_id]
    async with lock:
        session_id = _session_ids.get(chat_id)
        options = _make_options(chat_id, resume=session_id)
        timestamped = _timestamp_message(user_message)
        session_log(chat_id, "user", user_message)
        _tool_timings.pop(chat_id, None)
        _pending_tool_starts.pop(chat_id, None)
        cfg = Config.load()
        initial_timeout = cfg.get("agent_initial_timeout")
        stall = cfg.get("agent_stall_timeout")

        first_event = True
        reply = "(no response)"
        try:
            loop = asyncio.get_event_loop()
            deadline = asyncio.timeout(initial_timeout)
            logger.debug("SDK run_streaming start for {} (initial={}s, stall={}s)", chat_id, initial_timeout, stall)
            async with deadline, ClaudeSDKClient(options=options) as client:
                await client.query(timestamped)
                parts: list[str] = []
                async for msg in client.receive_response():
                    if first_event:
                        logger.debug("First SDK event for {} after startup", chat_id)
                        first_event = False
                    deadline.reschedule(loop.time() + stall)
                    if isinstance(msg, StreamEvent) and (text := _extract_stream_delta(msg)):
                        yield ("text_delta", text)
                    elif isinstance(msg, AssistantMessage):
                        parts.extend(_collect_text(msg))
                    elif isinstance(msg, SystemMessage) and msg.subtype == "compact_boundary":
                        logger.info("SDK compacted context for {}", chat_id)
                    elif isinstance(msg, ResultMessage):
                        _handle_result(chat_id, msg)

            reply = _strip_noise_suffix(_strip_hallucinated_turns("\n".join(parts) or "(no response)"))
        except TimeoutError:
            phase = "initial" if first_event else "inter-event"
            timeout_val = initial_timeout if first_event else stall
            logger.warning("Agent stalled ({} phase, {}s) for {}", phase, timeout_val, chat_id)
            _session_ids.pop(chat_id, None)
            reply = f"Stalled — no events for {timeout_val}s ({phase}). Try again or /stop."
        except Exception as e:
            logger.exception("Agent error for {}: {}: {}", chat_id, type(e).__name__, e)
            reply = f"Something went wrong ({type(e).__name__}). Please try again."

    session_log(chat_id, "assistant", reply)
    clear_tool_activity(chat_id)
    yield ("done", reply)
