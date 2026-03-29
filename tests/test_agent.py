"""Agent tests — mock SDK client to avoid network calls."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _patch_workspace(tmp_path, monkeypatch):
    import indieclaw.workspace as ws
    monkeypatch.setattr(ws, "HOME", tmp_path)
    for name, attr in [("SOUL.md", "SOUL"), ("USER.md", "USER"),
                        ("MEMORY.md", "MEMORY"), ("skills", "SKILLS_DIR")]:
        monkeypatch.setattr(ws, attr, tmp_path / name)
    (tmp_path / "sessions").mkdir(exist_ok=True)
    (tmp_path / "SOUL.md").write_text("## Identity\nNot set yet")
    (tmp_path / "USER.md").write_text("Not set yet")


def _make_fake_receive(text="OK"):
    from claude_agent_sdk import AssistantMessage, TextBlock
    async def _recv():
        msg = MagicMock(spec=AssistantMessage)
        block = MagicMock(spec=TextBlock)
        block.text = text
        msg.content = [block]
        yield msg
    return _recv


def test_system_prompt_contains_soul(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    from indieclaw.agent import _system_prompt
    prompt = _system_prompt()
    assert isinstance(prompt, str)
    assert "SOUL" in prompt


@pytest.mark.asyncio
async def test_run_returns_string(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    import indieclaw.agent as ag

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.receive_response = MagicMock(return_value=_make_fake_receive("Hello, world!")())

    with patch("indieclaw.agent.ClaudeSDKClient", return_value=mock_client), \
         patch("indieclaw.agent.reload_dynamic_tools"):
        result = await ag.run(chat_id="test-chat", user_message="hi")
        assert isinstance(result, str)
        assert "Hello" in result
        mock_client.query.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_id_stored_for_resume(tmp_path, monkeypatch):
    """session_id from ResultMessage is stored for future resume."""
    _patch_workspace(tmp_path, monkeypatch)
    from claude_agent_sdk import ResultMessage

    import indieclaw.agent as ag

    result_msg = MagicMock(spec=ResultMessage)
    result_msg.session_id = "sess-abc-123"
    result_msg.num_turns = 1
    result_msg.duration_ms = 100
    result_msg.usage = {"input_tokens": 10, "output_tokens": 5}

    async def _recv():
        yield MagicMock(spec=ag.AssistantMessage, content=[MagicMock(spec=ag.TextBlock, text="hi")])
        yield result_msg

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.receive_response = MagicMock(return_value=_recv())

    with patch("indieclaw.agent.ClaudeSDKClient", return_value=mock_client), \
         patch("indieclaw.agent.reload_dynamic_tools"):
        try:
            await ag.run(chat_id="resume-test", user_message="hello")
            assert ag._session_ids.get("resume-test") == "sess-abc-123"
        finally:
            ag._session_ids.pop("resume-test", None)


@pytest.mark.asyncio
async def test_resume_id_passed_to_options(tmp_path, monkeypatch):
    """When a session_id exists, it is passed as resume to _make_options."""
    _patch_workspace(tmp_path, monkeypatch)
    import indieclaw.agent as ag

    ag._session_ids["resume-opts"] = "sess-prev-456"

    captured_options = []
    original_make = ag._make_options

    def spy_make_options(chat_id, resume=None):
        opts = original_make(chat_id, resume=resume)
        captured_options.append(opts)
        return opts

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.receive_response = MagicMock(return_value=_make_fake_receive("ok")())

    with patch("indieclaw.agent._make_options", side_effect=spy_make_options), \
         patch("indieclaw.agent.ClaudeSDKClient", return_value=mock_client), \
         patch("indieclaw.agent.reload_dynamic_tools"):
        try:
            await ag.run(chat_id="resume-opts", user_message="hi")
            assert len(captured_options) == 1
            assert captured_options[0].resume == "sess-prev-456"
        finally:
            ag._session_ids.pop("resume-opts", None)


@pytest.mark.asyncio
async def test_cron_does_not_resume(tmp_path, monkeypatch):
    """Cron sessions should not resume — they're stateless."""
    _patch_workspace(tmp_path, monkeypatch)
    import indieclaw.agent as ag

    ag._session_ids["cron:test-job"] = "sess-cron-789"

    captured_options = []
    original_make = ag._make_options

    def spy_make_options(chat_id, resume=None):
        opts = original_make(chat_id, resume=resume)
        captured_options.append(opts)
        return opts

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.receive_response = MagicMock(return_value=_make_fake_receive("ok")())

    with patch("indieclaw.agent._make_options", side_effect=spy_make_options), \
         patch("indieclaw.agent.ClaudeSDKClient", return_value=mock_client), \
         patch("indieclaw.agent.reload_dynamic_tools"):
        try:
            await ag.run(chat_id="cron:test-job", user_message="run")
            assert captured_options[0].resume is None
        finally:
            ag._session_ids.pop("cron:test-job", None)


@pytest.mark.asyncio
async def test_reset_clears_session_id(tmp_path, monkeypatch):
    """reset_session should clear session_id and usage."""
    _patch_workspace(tmp_path, monkeypatch)
    import indieclaw.agent as ag

    ag._session_ids["clear-test"] = "sess-to-clear"
    ag._last_usage["clear-test"] = {"input_tokens": 100}
    await ag.reset_session("clear-test")
    assert "clear-test" not in ag._session_ids
    assert "clear-test" not in ag._last_usage


@pytest.mark.asyncio
async def test_error_returns_generic_message(tmp_path, monkeypatch):
    """run() should return a generic error message, not the raw exception."""
    _patch_workspace(tmp_path, monkeypatch)
    import indieclaw.agent as ag

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.query.side_effect = ValueError("secret internal detail")

    with patch("indieclaw.agent.ClaudeSDKClient", return_value=mock_client), \
         patch("indieclaw.agent.reload_dynamic_tools"):
        result = await ag.run(chat_id="error-test", user_message="hi")
        assert "secret internal detail" not in result
        assert "wrong" in result.lower() or "try again" in result.lower()


def test_native_agents_in_options(tmp_path, monkeypatch):
    """Interactive chats get agents, cron jobs don't."""
    _patch_workspace(tmp_path, monkeypatch)
    import indieclaw.agent as ag

    interactive_opts = ag._make_options("user-chat", resume=None)
    assert interactive_opts.agents is not None
    assert "task-runner" in interactive_opts.agents

    cron_opts = ag._make_options("cron:some-job", resume=None)
    assert cron_opts.agents is None


def test_subconscious_options_slim(tmp_path, monkeypatch):
    """Subconscious should get only telegram_send, update_subconscious, reflect — not the full tool set."""
    _patch_workspace(tmp_path, monkeypatch)
    import indieclaw.agent as ag

    opts = ag._make_options("cron:subconscious")
    assert "mcp__indieclaw__telegram_send" in opts.allowed_tools
    assert "mcp__indieclaw__update_subconscious" in opts.allowed_tools
    assert "mcp__indieclaw__reflect" in opts.allowed_tools
    assert "mcp__indieclaw__browse" not in opts.allowed_tools
    assert opts.max_turns == 3


# ---------------------------------------------------------------------------
# Tool activity tracking
# ---------------------------------------------------------------------------

class TestToolActivity:
    def test_get_tool_activity_returns_none_when_idle(self):
        from indieclaw.agent import get_tool_activity
        result = get_tool_activity("no-such-chat")
        assert result is None

    def test_tool_activity_set_and_read(self):
        import time

        from indieclaw.agent import _tool_activity, _tool_start_time, get_tool_activity
        _tool_activity["test-chat"] = "searching"
        _tool_start_time["test-chat"] = time.monotonic() - 5.0
        result = get_tool_activity("test-chat")
        assert result is not None
        label, elapsed = result
        assert label == "searching"
        assert elapsed >= 4.0
        _tool_activity.pop("test-chat", None)
        _tool_start_time.pop("test-chat", None)


def test_initial_timeout_config_default():
    from indieclaw.config import Config
    cfg = Config()
    assert cfg.get("agent_initial_timeout") == 300
    assert cfg.get("agent_stall_timeout") == 120


@pytest.mark.asyncio
async def test_run_uses_initial_timeout_before_first_event(tmp_path, monkeypatch):
    """run() should use agent_initial_timeout before first event, not agent_stall_timeout."""
    _patch_workspace(tmp_path, monkeypatch)
    import indieclaw.agent as ag

    # Patch config: initial=0.05s (50ms), stall=999s
    # If run() uses stall timeout for initial wait, this won't timeout quickly
    import indieclaw.config as cfg_mod
    monkeypatch.setattr(cfg_mod.Config, "DEFAULTS", {
        **ag.Config.DEFAULTS,
        "agent_initial_timeout": 0.05,
        "agent_stall_timeout": 999,
    })
    # Invalidate config cache so new DEFAULTS take effect
    monkeypatch.setattr(cfg_mod, "_cache", None)

    async def _hang_forever():
        await asyncio.sleep(9999)
        yield  # never reached

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.receive_response = MagicMock(return_value=_hang_forever())

    with patch("indieclaw.agent.ClaudeSDKClient", return_value=mock_client), \
         patch("indieclaw.agent.reload_dynamic_tools"):
        result = await ag.run(chat_id="timeout-test", user_message="hi")
        assert "Stalled" in result or "no progress" in result.lower()


@pytest.mark.asyncio
async def test_stall_clears_session_id(tmp_path, monkeypatch):
    """After a stall timeout, session_id should be cleared so next attempt starts fresh."""
    _patch_workspace(tmp_path, monkeypatch)
    import indieclaw.agent as ag
    import indieclaw.config as cfg_mod

    # Pre-set a session ID to simulate an ongoing session
    ag._session_ids["stall-clear"] = "sess-old-123"

    monkeypatch.setattr(cfg_mod.Config, "DEFAULTS", {
        **ag.Config.DEFAULTS,
        "agent_initial_timeout": 0.05,
        "agent_stall_timeout": 0.05,
    })
    monkeypatch.setattr(cfg_mod, "_cache", None)

    async def _hang():
        await asyncio.sleep(9999)
        yield

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.receive_response = MagicMock(return_value=_hang())

    with patch("indieclaw.agent.ClaudeSDKClient", return_value=mock_client), \
         patch("indieclaw.agent.reload_dynamic_tools"):
        try:
            result = await ag.run(chat_id="stall-clear", user_message="hi")
            assert "Stalled" in result
            # Session ID must be cleared
            assert "stall-clear" not in ag._session_ids
        finally:
            ag._session_ids.pop("stall-clear", None)


# ---------------------------------------------------------------------------
# Lazy ignorance detection
# ---------------------------------------------------------------------------

class TestLazyIgnoranceCheck:
    def test_ignorance_detected_without_memory_tools(self):
        from indieclaw.agent import _is_lazy_ignorance
        # Claims ignorance, used no memory-related tools
        assert _is_lazy_ignorance("I don't have context about that.", {"Bash", "Write"}) is True

    def test_ignorance_not_flagged_when_memory_checked(self):
        from indieclaw.agent import _is_lazy_ignorance
        # Claims ignorance but DID check memory
        assert _is_lazy_ignorance("I don't have context about that.", {"Read", "Bash"}) is False

    def test_ignorance_not_flagged_when_sessions_searched(self):
        from indieclaw.agent import _is_lazy_ignorance
        assert _is_lazy_ignorance("I don't recall that.", {"mcp__indieclaw__search_sessions"}) is False

    def test_normal_reply_not_flagged(self):
        from indieclaw.agent import _is_lazy_ignorance
        assert _is_lazy_ignorance("Here's what I found in the logs.", set()) is False

    def test_tools_used_tracked_per_turn(self, tmp_path, monkeypatch):
        """_tools_used_this_turn should accumulate all tool names during a run."""
        _patch_workspace(tmp_path, monkeypatch)
        import indieclaw.agent as ag

        # Simulate PreToolUse hook firing
        ag._tools_used_this_turn["track-test"] = set()
        ag._tools_used_this_turn["track-test"].add("Bash")
        ag._tools_used_this_turn["track-test"].add("Read")
        ag._tools_used_this_turn["track-test"].add("Bash")  # duplicate
        assert ag._tools_used_this_turn["track-test"] == {"Bash", "Read"}
        ag._tools_used_this_turn.pop("track-test", None)

    @pytest.mark.asyncio
    async def test_lazy_reply_triggers_retry(self, tmp_path, monkeypatch):
        """If agent claims ignorance without checking memory, run() should retry."""
        _patch_workspace(tmp_path, monkeypatch)
        import indieclaw.agent as ag

        call_count = 0

        async def _fake_recv_lazy():
            """First call: lazy ignorance. Second call: real answer."""
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                msg = MagicMock(spec=ag.AssistantMessage)
                block = MagicMock(spec=ag.TextBlock)
                block.text = "I don't have any context about that conversation."
                msg.content = [block]
                yield msg
            else:
                msg = MagicMock(spec=ag.AssistantMessage)
                block = MagicMock(spec=ag.TextBlock)
                block.text = "Based on the session logs, here's what happened."
                msg.content = [block]
                yield msg

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.receive_response = MagicMock(side_effect=lambda: _fake_recv_lazy())

        with patch("indieclaw.agent.ClaudeSDKClient", return_value=mock_client), \
             patch("indieclaw.agent.reload_dynamic_tools"):
            result = await ag.run(chat_id="lazy-test", user_message="what did we discuss?")
            assert "session logs" in result
            assert call_count == 2  # retried once

    @pytest.mark.asyncio
    async def test_genuine_ignorance_not_retried_twice(self, tmp_path, monkeypatch):
        """If retry also claims ignorance, accept it (don't loop forever)."""
        _patch_workspace(tmp_path, monkeypatch)
        import indieclaw.agent as ag

        def _always_ignorant():
            async def _gen():
                msg = MagicMock(spec=ag.AssistantMessage)
                block = MagicMock(spec=ag.TextBlock)
                block.text = "I don't know about that."
                msg.content = [block]
                yield msg
            return _gen()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.receive_response = MagicMock(side_effect=lambda: _always_ignorant())

        with patch("indieclaw.agent.ClaudeSDKClient", return_value=mock_client), \
             patch("indieclaw.agent.reload_dynamic_tools"):
            result = await ag.run(chat_id="genuine-test", user_message="what is X?")
            # Should return the ignorance reply after max 1 retry
            assert "don't know" in result.lower()
            # query called at most 2 times (original + 1 retry)
            assert mock_client.query.await_count <= 2
