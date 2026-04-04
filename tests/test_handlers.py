"""Handler tests — Telegram message handling, chunking, markdown conversion."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def _no_debounce(monkeypatch):
    """Zero debounce delay so tests don't wait."""
    import indieclaw.handlers as _h
    monkeypatch.setattr(_h, "_DEBOUNCE_SECONDS", 0)


# ---------------------------------------------------------------------------
# _to_telegram_html
# ---------------------------------------------------------------------------

class TestToTelegramHtml:
    def test_bold_conversion(self):
        from indieclaw.handlers import _to_telegram_html
        assert _to_telegram_html("**hello**") == "<b>hello</b>"

    def test_italic_conversion(self):
        from indieclaw.handlers import _to_telegram_html
        assert _to_telegram_html("*hello*") == "<i>hello</i>"

    def test_heading_conversion(self):
        from indieclaw.handlers import _to_telegram_html
        assert _to_telegram_html("## Heading") == "<b>Heading</b>"

    def test_no_change_plain_text(self):
        from indieclaw.handlers import _to_telegram_html
        text = "Just some plain text"
        assert _to_telegram_html(text) == text

    def test_inline_code(self):
        from indieclaw.handlers import _to_telegram_html
        assert _to_telegram_html("use `foo()`") == "use <code>foo()</code>"

    def test_code_block(self):
        from indieclaw.handlers import _to_telegram_html
        result = _to_telegram_html("```\nprint('hi')\n```")
        assert "<pre>" in result
        assert "print('hi')" in result

    def test_link_conversion(self):
        from indieclaw.handlers import _to_telegram_html
        result = _to_telegram_html("[click](https://example.com)")
        assert result == '<a href="https://example.com">click</a>'

    def test_bullet_list_conversion(self):
        from indieclaw.handlers import _to_telegram_html
        result = _to_telegram_html("- item one\n- item two")
        assert "\u2022 item one" in result
        assert "\u2022 item two" in result

    def test_nested_bullet_preserves_indent(self):
        from indieclaw.handlers import _to_telegram_html
        result = _to_telegram_html("- top\n  - nested")
        assert "\u2022 top" in result
        assert "  \u2022 nested" in result

    def test_html_escaping(self):
        from indieclaw.handlers import _to_telegram_html
        result = _to_telegram_html("a < b & c > d")
        assert "&lt;" in result
        assert "&amp;" in result
        assert "&gt;" in result

    def test_code_block_not_transformed(self):
        from indieclaw.handlers import _to_telegram_html
        result = _to_telegram_html("```\n- not a bullet\n**not bold**\n```")
        assert "\u2022" not in result
        assert "<b>" not in result

    def test_inline_code_not_transformed(self):
        from indieclaw.handlers import _to_telegram_html
        result = _to_telegram_html("`**stay raw**`")
        assert "<b>" not in result
        assert "**stay raw**" in result

    def test_mixed_bold_and_heading(self):
        from indieclaw.handlers import _to_telegram_html
        result = _to_telegram_html("## Title\n\n**bold** word")
        assert "<b>Title</b>" in result
        assert "<b>bold</b>" in result


# ---------------------------------------------------------------------------
# _reply_chunked
# ---------------------------------------------------------------------------

class TestReplyChunked:
    @pytest.mark.asyncio
    async def test_short_message_single_chunk(self):
        from indieclaw.handlers import _reply_chunked
        msg = AsyncMock()
        await _reply_chunked(msg, "hello")
        msg.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_long_message_splits_at_max(self):
        from indieclaw.handlers import _reply_chunked
        from indieclaw.tools import MAX_TG_MSG
        msg = AsyncMock()
        text = "a" * (MAX_TG_MSG + 100)
        await _reply_chunked(msg, text)
        assert msg.reply_text.await_count == 2

    @pytest.mark.asyncio
    async def test_exact_boundary(self):
        from indieclaw.handlers import _reply_chunked
        from indieclaw.tools import MAX_TG_MSG
        msg = AsyncMock()
        text = "b" * MAX_TG_MSG
        await _reply_chunked(msg, text)
        assert msg.reply_text.await_count == 1

    @pytest.mark.asyncio
    async def test_html_failure_falls_back_to_plain(self):
        from indieclaw.handlers import _reply_chunked
        msg = AsyncMock()
        call_count = 0
        async def _side_effect(text, **kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("parse_mode") == "HTML":
                raise Exception("Bad HTML")
        msg.reply_text = AsyncMock(side_effect=_side_effect)
        await _reply_chunked(msg, "hello")
        assert call_count == 2  # first try HTML, then plain


# ---------------------------------------------------------------------------
# on_message
# ---------------------------------------------------------------------------

def _make_update(chat_id="123", text="hi"):
    update = MagicMock()
    update.effective_chat.id = int(chat_id)
    update.edited_message = None
    update.message.text = text
    # reply_text returns a placeholder message with edit_text for inline editing
    placeholder = MagicMock()
    placeholder.edit_text = AsyncMock()
    placeholder.message_id = 99
    update.message.reply_text = AsyncMock(return_value=placeholder)
    update.message.message_id = 42
    return update


def _make_context(chat_id="123"):
    ctx = MagicMock()
    ctx.bot.send_chat_action = AsyncMock()
    ctx.bot.send_message = AsyncMock()
    ctx.bot.edit_message_text = AsyncMock()
    ctx.bot.delete_message = AsyncMock()
    return ctx


class TestOnMessage:
    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import on_message
        update = _make_update()
        ctx = _make_context()
        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, return_value="Reply!"), \
             patch("indieclaw.handlers.get_streaming", return_value=False):
            await on_message(update, ctx)
            from indieclaw.handlers import flush_debounce
            await flush_debounce("123")
        # Reply sent directly (no placeholder)
        update.message.reply_text.assert_awaited()
        calls = update.message.reply_text.await_args_list
        assert any("Reply!" in str(c) for c in calls)

    @pytest.mark.asyncio
    async def test_agent_error_sends_fallback(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import on_message
        update = _make_update()
        ctx = _make_context()
        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, side_effect=RuntimeError("boom")), \
             patch("indieclaw.handlers.get_streaming", return_value=False):
            await on_message(update, ctx)
            from indieclaw.handlers import flush_debounce
            await flush_debounce("123")
        # Error sent directly (no placeholder)
        update.message.reply_text.assert_awaited()
        calls = update.message.reply_text.await_args_list
        assert any("wrong" in str(c).lower() for c in calls)

    @pytest.mark.asyncio
    async def test_not_allowed_user_ignored(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "999")
        from indieclaw.handlers import on_message
        update = _make_update(chat_id="123")
        ctx = _make_context()
        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock) as mock_run:
            await on_message(update, ctx)
        mock_run.assert_not_awaited()


# ---------------------------------------------------------------------------
# on_photo / on_document
# ---------------------------------------------------------------------------

class TestOnPhoto:
    @pytest.mark.asyncio
    async def test_downloads_and_passes_to_agent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        import indieclaw.workspace as ws
        monkeypatch.setattr(ws, "UPLOADS_DIR", tmp_path)

        from indieclaw.handlers import on_photo
        update = MagicMock()
        update.effective_chat.id = 123
        update.message.caption = "Look at this"
        photo = MagicMock()
        photo.file_id = "abc"
        photo.file_unique_id = "photo123"
        update.message.photo = [photo]
        update.message.reply_text = AsyncMock()

        ctx = MagicMock()
        ctx.bot.send_chat_action = AsyncMock()
        file_mock = AsyncMock()
        ctx.bot.get_file = AsyncMock(return_value=file_mock)

        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, return_value="Saw it"), \
             patch("indieclaw.handlers.get_streaming", return_value=False):
            await on_photo(update, ctx)
        file_mock.download_to_drive.assert_awaited_once()
        update.message.reply_text.assert_awaited()


class TestOnDocument:
    @pytest.mark.asyncio
    async def test_downloads_and_passes_to_agent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        import indieclaw.workspace as ws
        monkeypatch.setattr(ws, "UPLOADS_DIR", tmp_path)

        from indieclaw.handlers import on_document
        update = MagicMock()
        update.effective_chat.id = 123
        update.message.caption = "Here's a file"
        doc = MagicMock()
        doc.file_id = "def"
        doc.file_unique_id = "doc456"
        doc.file_name = "report.pdf"
        doc.mime_type = "application/pdf"
        update.message.document = doc
        update.message.reply_text = AsyncMock()

        ctx = MagicMock()
        ctx.bot.send_chat_action = AsyncMock()
        file_mock = AsyncMock()
        ctx.bot.get_file = AsyncMock(return_value=file_mock)

        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, return_value="Got it"), \
             patch("indieclaw.handlers.get_streaming", return_value=False):
            await on_document(update, ctx)
        file_mock.download_to_drive.assert_awaited_once()
        update.message.reply_text.assert_awaited()


# ---------------------------------------------------------------------------
# Phase 3.1: Error classification
# ---------------------------------------------------------------------------

class TestErrorClassification:
    @pytest.mark.asyncio
    async def test_timeout_error_message(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import on_message
        update = _make_update()
        ctx = _make_context()
        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, side_effect=TimeoutError()), \
             patch("indieclaw.handlers.get_streaming", return_value=False):
            await on_message(update, ctx)
            from indieclaw.handlers import flush_debounce
            await flush_debounce("123")
        calls = update.message.reply_text.await_args_list
        msg = calls[-1][0][0]
        assert "timed out" in msg.lower() or "timeout" in msg.lower()

    @pytest.mark.asyncio
    async def test_permission_error_message(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import on_message
        update = _make_update()
        ctx = _make_context()
        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, side_effect=PermissionError("denied")), \
             patch("indieclaw.handlers.get_streaming", return_value=False):
            await on_message(update, ctx)
            from indieclaw.handlers import flush_debounce
            await flush_debounce("123")
        calls = update.message.reply_text.await_args_list
        msg = calls[-1][0][0]
        assert "permission" in msg.lower()

    @pytest.mark.asyncio
    async def test_connection_error_message(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import on_message
        update = _make_update()
        ctx = _make_context()
        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, side_effect=ConnectionError("no network")), \
             patch("indieclaw.handlers.get_streaming", return_value=False):
            await on_message(update, ctx)
            from indieclaw.handlers import flush_debounce
            await flush_debounce("123")
        calls = update.message.reply_text.await_args_list
        msg = calls[-1][0][0]
        assert "connection" in msg.lower()

    @pytest.mark.asyncio
    async def test_generic_error_message(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import on_message
        update = _make_update()
        ctx = _make_context()
        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, side_effect=RuntimeError("wat")), \
             patch("indieclaw.handlers.get_streaming", return_value=False):
            await on_message(update, ctx)
            from indieclaw.handlers import flush_debounce
            await flush_debounce("123")
        calls = update.message.reply_text.await_args_list
        msg = calls[-1][0][0]
        assert "wrong" in msg.lower()
        assert "wat" not in msg  # should not leak internal error


# ---------------------------------------------------------------------------
# Typing indicator during debounce
# ---------------------------------------------------------------------------

class TestDebounceTyping:
    @pytest.mark.asyncio
    async def test_typing_indicator_sent_during_debounce(self, monkeypatch):
        """User should see 'typing...' immediately, not wait through debounce."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import on_message
        update = _make_update()
        ctx = _make_context()
        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, return_value="Reply!"), \
             patch("indieclaw.handlers.get_streaming", return_value=False):
            await on_message(update, ctx)
            from indieclaw.handlers import flush_debounce
            await flush_debounce("123")
        # Typing indicator should have been sent at least once
        ctx.bot.send_chat_action.assert_awaited()


# ---------------------------------------------------------------------------
# Voice message handler
# ---------------------------------------------------------------------------

class TestVoiceHandler:
    @pytest.mark.asyncio
    async def test_voice_message_gets_reply(self, monkeypatch):
        """Voice messages should get a helpful response, not be silently dropped."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import on_voice
        update = MagicMock()
        update.effective_chat.id = 123
        update.message.voice.file_id = "abc123"
        update.message.voice.file_unique_id = "uniq"
        update.message.voice.duration = 5
        update.message.caption = None
        update.message.reply_text = AsyncMock()
        ctx = _make_context()
        await on_voice(update, ctx)
        update.message.reply_text.assert_awaited_once()



# ---------------------------------------------------------------------------
# on_update — restart behavior
# ---------------------------------------------------------------------------

class TestOnUpdate:
    @pytest.mark.asyncio
    async def test_same_version_no_restart(self, monkeypatch):
        """When remote version == local version, should NOT restart."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import on_update
        update = _make_update(text="/update")
        ctx = _make_context()

        monkeypatch.setattr("indieclaw.handlers_commands._local_version", lambda: "0.5.0")

        # Mock requests.get response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = 'version = "0.5.0"'

        async def fake_to_thread(fn, *a, **kw):
            return mock_resp  # only call is requests.get — returns same version

        with patch("indieclaw.handlers_commands._check_remote_version", return_value="0.5.0"):
            await on_update(update, ctx)

        # One placeholder sent, then edited with the final status
        placeholder = update.message.reply_text.return_value
        placeholder.edit_text.assert_awaited()
        edits = [call[0][0] for call in placeholder.edit_text.await_args_list]
        assert any("already on latest" in e.lower() for e in edits)

    @pytest.mark.asyncio
    async def test_new_version_uses_clean_exit(self, monkeypatch):
        """When a real update happens, should do a clean process exit, not os.execv."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import on_update
        update = _make_update(text="/update")
        ctx = _make_context()

        monkeypatch.setattr("indieclaw.handlers_commands._local_version", lambda: "0.4.7")

        mock_install = MagicMock()
        mock_install.returncode = 0
        mock_install.stdout = "Installed"
        mock_install.stderr = ""

        call_count = 0
        async def fake_to_thread(fn, *a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "0.5.0"         # _check_remote_version
            elif call_count == 2:
                return mock_install    # subprocess.run (uv install)
            else:
                return "Updated: 0.4.7 -> 0.5.0"  # _get_update_summary

        import indieclaw.handlers as _h
        with patch.object(_h.asyncio, "to_thread", side_effect=fake_to_thread), \
             patch("os.kill") as mock_kill, \
             patch("os.getpid", return_value=12345), \
             patch("os.execv") as mock_execv:
            await on_update(update, ctx)

        mock_execv.assert_not_called()
        import signal
        mock_kill.assert_called_with(12345, signal.SIGTERM)


# ---------------------------------------------------------------------------
# Reaction handler — message=None safety
# ---------------------------------------------------------------------------

class TestRunAgentNullMessage:
    @pytest.mark.asyncio
    async def test_no_crash_when_message_is_none(self, monkeypatch):
        """_run_agent_and_reply should not crash when message=None (reaction path)."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import _run_agent_and_reply

        bot = MagicMock()
        bot.send_chat_action = AsyncMock()
        bot.send_message = AsyncMock()

        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, return_value="Noted the reaction."):
            # Should not raise AttributeError
            await _run_agent_and_reply(bot, None, "123", "reaction msg")

        # Reply should be sent via bot.send_message since message is None
        bot.send_message.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_crash_on_error_when_message_is_none(self, monkeypatch):
        """Error path should not crash when message=None."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import _run_agent_and_reply

        bot = MagicMock()
        bot.send_chat_action = AsyncMock()
        bot.send_message = AsyncMock()

        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            # Should not raise
            await _run_agent_and_reply(bot, None, "123", "reaction msg")


# ---------------------------------------------------------------------------
# on_photo — empty photo array safety
# ---------------------------------------------------------------------------

class TestOnPhotoEmptyArray:
    @pytest.mark.asyncio
    async def test_empty_photo_array_no_crash(self, monkeypatch):
        """on_photo should handle empty photo array gracefully."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import on_photo

        update = MagicMock()
        update.effective_chat.id = 123
        update.message.photo = []
        update.message.caption = "test"
        update.message.reply_text = AsyncMock()
        ctx = MagicMock()

        # Should not raise IndexError
        await on_photo(update, ctx)


# ---------------------------------------------------------------------------
# /model command — shorthand and picker
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Activity indicator formatting
# ---------------------------------------------------------------------------

class TestActivityIndicator:
    def test_format_activity_short(self):
        from indieclaw.handlers import _format_activity
        result = _format_activity("searching", 1.5)
        assert result == "\U0001f527 searching\u2026"

    def test_format_activity_long(self):
        from indieclaw.handlers import _format_activity
        result = _format_activity("browsing", 8.2)
        assert result == "\U0001f527 browsing\u2026 (8s)"


class TestOnModel:
    @pytest.mark.asyncio
    async def test_model_shorthand_switches(self, monkeypatch):
        """/model opus should switch directly without showing picker."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers_commands import on_model

        update = MagicMock()
        update.effective_chat.id = 123
        update.message.text = "/model opus"
        update.message.reply_text = AsyncMock()

        with patch("indieclaw.handlers_commands.set_model", new_callable=AsyncMock) as mock_set:
            await on_model(update, MagicMock())
            mock_set.assert_awaited_once_with("claude-opus-4-6")
            reply_text = update.message.reply_text.call_args[0][0]
            assert "Opus" in reply_text

    @pytest.mark.asyncio
    async def test_model_no_arg_shows_picker(self, monkeypatch):
        """/model with no arg should show inline keyboard."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers_commands import on_model

        update = MagicMock()
        update.effective_chat.id = 123
        update.message.text = "/model"
        update.message.reply_text = AsyncMock()

        with patch("indieclaw.handlers_commands.get_current_model", return_value="claude-sonnet-4-6"):
            await on_model(update, MagicMock())
            call_kwargs = update.message.reply_text.call_args[1]
            assert "reply_markup" in call_kwargs

    @pytest.mark.asyncio
    async def test_model_unknown_rejects(self, monkeypatch):
        """/model badname should reject with helpful message."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers_commands import on_model

        update = MagicMock()
        update.effective_chat.id = 123
        update.message.text = "/model gpt4"
        update.message.reply_text = AsyncMock()

        await on_model(update, MagicMock())
        reply_text = update.message.reply_text.call_args[0][0]
        assert "Unknown" in reply_text


# ---------------------------------------------------------------------------
# /btw command
# ---------------------------------------------------------------------------

class TestOnBtw:
    @pytest.mark.asyncio
    async def test_btw_no_args_shows_usage(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import on_btw
        update = MagicMock()
        update.effective_chat.id = 123
        update.message.text = "/btw"
        update.message.reply_text = AsyncMock()
        ctx = _make_context()
        await on_btw(update, ctx)
        reply = update.message.reply_text.call_args[0][0]
        assert "Usage" in reply

    @pytest.mark.asyncio
    async def test_btw_runs_subprocess(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import on_btw
        update = MagicMock()
        update.effective_chat.id = 123
        update.message.text = "/btw what is 2+2?"
        update.message.reply_text = AsyncMock()
        ctx = _make_context()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "It is 4."

        async def fake_to_thread(fn, *a, **kw):
            return mock_result

        import indieclaw.handlers as _h
        with patch.object(_h.asyncio, "to_thread", side_effect=fake_to_thread), \
             patch.object(_h, "_TypingLoop") as mock_typing:
            mock_typing.return_value.__aenter__ = AsyncMock()
            mock_typing.return_value.__aexit__ = AsyncMock()
            await on_btw(update, ctx)
        update.message.reply_text.assert_awaited()
        calls = update.message.reply_text.await_args_list
        assert any("It is 4." in str(c) for c in calls)


# ---------------------------------------------------------------------------
# /cc command
# ---------------------------------------------------------------------------

class TestOnCC:
    @pytest.mark.asyncio
    async def test_cc_no_args_shows_help(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import on_cc
        update = MagicMock()
        update.effective_chat.id = 123
        update.message.text = "/cc"
        update.message.reply_text = AsyncMock()
        ctx = _make_context()

        with patch("indieclaw.claude_code.has_active_session", return_value=False), \
             patch("indieclaw.claude_code.get_session_info", return_value=None):
            await on_cc(update, ctx)
        reply = update.message.reply_text.call_args[0][0]
        assert "/cc" in reply
        assert "stop" in reply.lower()

    @pytest.mark.asyncio
    async def test_cc_stop_without_session(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import on_cc
        update = MagicMock()
        update.effective_chat.id = 123
        update.message.text = "/cc stop"
        update.message.reply_text = AsyncMock()
        ctx = _make_context()

        with patch("indieclaw.claude_code.has_active_session", return_value=False), \
             patch("indieclaw.claude_code.stop_session", new_callable=AsyncMock, return_value=False), \
             patch("indieclaw.claude_code.get_stop_summary", return_value=""):
            await on_cc(update, ctx)
        reply = update.message.reply_text.call_args[0][0]
        assert "No active" in reply


# ---------------------------------------------------------------------------
# Debounce — multiple messages combined
# ---------------------------------------------------------------------------

class TestDebounceMultipleMessages:
    @pytest.mark.asyncio
    async def test_multiple_messages_combined(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import flush_debounce, on_message

        captured_msgs = []

        async def mock_agent_run(chat_id, user_message, **kw):
            captured_msgs.append(user_message)
            return "ok"

        update1 = _make_update(text="first")
        update2 = _make_update(text="second")
        update3 = _make_update(text="third")
        ctx = _make_context()

        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, side_effect=mock_agent_run), \
             patch("indieclaw.handlers.get_streaming", return_value=False), \
             patch("indieclaw.claude_code.has_active_session", return_value=False):
            await on_message(update1, ctx)
            await on_message(update2, ctx)
            await on_message(update3, ctx)
            await flush_debounce("123")

        assert len(captured_msgs) == 1
        combined = captured_msgs[0]
        assert "first" in combined
        assert "second" in combined
        assert "third" in combined


# ---------------------------------------------------------------------------
# on_video
# ---------------------------------------------------------------------------

class TestOnVideo:
    @pytest.mark.asyncio
    async def test_video_downloads_and_passes_to_agent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        import indieclaw.workspace as ws
        monkeypatch.setattr(ws, "UPLOADS_DIR", tmp_path)

        from indieclaw.handlers import on_video
        update = MagicMock()
        update.effective_chat.id = 123
        update.message.caption = "Check this out"
        video = MagicMock()
        video.file_id = "vid1"
        video.file_unique_id = "viduniq1"
        video.duration = 10
        update.message.video = video
        update.message.animation = None
        update.message.message_id = 42
        update.message.reply_text = AsyncMock()

        ctx = MagicMock()
        ctx.bot.send_chat_action = AsyncMock()
        file_mock = AsyncMock()
        ctx.bot.get_file = AsyncMock(return_value=file_mock)

        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, return_value="Saw the video"), \
             patch("indieclaw.handlers.get_streaming", return_value=False):
            await on_video(update, ctx)
        file_mock.download_to_drive.assert_awaited_once()
        update.message.reply_text.assert_awaited()
        calls = update.message.reply_text.await_args_list
        assert any("Saw the video" in str(c) for c in calls)


# ---------------------------------------------------------------------------
# /start command
# ---------------------------------------------------------------------------

class TestOnStart:
    @pytest.mark.asyncio
    async def test_start_shows_user_id_for_allowed_user(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import on_start
        update = MagicMock()
        update.effective_chat.id = 123
        update.effective_user.id = 123
        update.message.reply_text = AsyncMock()
        ctx = _make_context()

        with patch("indieclaw.auth.is_allowed", return_value=True), \
             patch("indieclaw.version.local_version", return_value="0.1.0"):
            await on_start(update, ctx)
        reply = update.message.reply_text.call_args[0][0]
        assert "123" in reply

    @pytest.mark.asyncio
    async def test_start_shows_user_id_for_unknown_user(self, monkeypatch):
        from indieclaw.handlers import on_start
        update = MagicMock()
        update.effective_chat.id = 456
        update.effective_user.id = 456
        update.message.reply_text = AsyncMock()
        ctx = _make_context()

        with patch("indieclaw.auth.is_allowed", return_value=False), \
             patch("indieclaw.version.local_version", return_value="0.1.0"):
            await on_start(update, ctx)
        reply = update.message.reply_text.call_args[0][0]
        assert "456" in reply
        assert "indieclaw setup" in reply


# ---------------------------------------------------------------------------
# _drain_followups — should combine all queued messages, not drop earlier ones
# ---------------------------------------------------------------------------

class TestDrainFollowups:
    @pytest.mark.asyncio
    async def test_all_queued_messages_are_combined(self, monkeypatch):
        """All queued messages should be sent to the agent, not just the last."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import _drain_followups, _pending_followups

        captured_msgs = []

        async def mock_agent_run(chat_id, user_message, **kw):
            captured_msgs.append(user_message)
            return "ok"

        _pending_followups["123"] = ["first msg", "second msg", "third msg"]

        bot = MagicMock()
        bot.send_chat_action = AsyncMock()
        bot.send_message = AsyncMock()

        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, side_effect=mock_agent_run), \
             patch("indieclaw.handlers.get_streaming", return_value=False):
            await _drain_followups(bot, "123")

        assert len(captured_msgs) == 1
        combined = captured_msgs[0]
        assert "first msg" in combined
        assert "second msg" in combined
        assert "third msg" in combined


# ---------------------------------------------------------------------------
# Tool-noise reply — should send acknowledgment, not silence
# ---------------------------------------------------------------------------

class TestToolNoiseReply:
    @pytest.mark.asyncio
    async def test_tool_noise_sends_acknowledgment(self, monkeypatch):
        """When agent produces tool-only output, user should get an acknowledgment."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import _run_agent_and_reply

        bot = MagicMock()
        bot.send_chat_action = AsyncMock()
        bot.send_message = AsyncMock()

        msg = MagicMock()
        msg.reply_text = AsyncMock()

        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, return_value="(no response)"), \
             patch("indieclaw.handlers.get_streaming", return_value=False):
            await _run_agent_and_reply(bot, msg, "123", "do something")

        # Should have sent SOME reply to the user
        assert msg.reply_text.await_count > 0 or bot.send_message.await_count > 0

    @pytest.mark.asyncio
    async def test_standing_by_suppressed(self, monkeypatch):
        """'(No message — standing by.)' should be suppressed, not forwarded."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import _run_agent_and_reply

        bot = MagicMock()
        bot.send_chat_action = AsyncMock()
        bot.send_message = AsyncMock()

        msg = MagicMock()
        msg.reply_text = AsyncMock()

        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, return_value="(No message \u2014 standing by.)"), \
             patch("indieclaw.handlers.get_streaming", return_value=False), \
             patch("indieclaw.handlers.get_tools_used", return_value=set()):
            await _run_agent_and_reply(bot, msg, "123", "ping")

        # Should NOT forward the standing-by phrase verbatim
        for call in msg.reply_text.await_args_list:
            assert "standing by" not in call.args[0].lower()

    @pytest.mark.asyncio
    async def test_telegram_send_suppresses_done(self, monkeypatch):
        """When agent used telegram_send, don't send an extra 'Done.' message."""
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers import _run_agent_and_reply

        bot = MagicMock()
        bot.send_chat_action = AsyncMock()
        bot.send_message = AsyncMock()

        msg = MagicMock()
        msg.reply_text = AsyncMock()

        # Simulate agent using telegram_send (MCP-namespaced) and returning noise
        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, return_value="(no response)"), \
             patch("indieclaw.handlers.get_streaming", return_value=False), \
             patch("indieclaw.handlers.get_tools_used", return_value={"mcp__indieclaw__telegram_send"}):
            await _run_agent_and_reply(bot, msg, "123", "send me a summary")

        # Should NOT have sent "Done." — telegram_send already delivered the reply
        for call in msg.reply_text.await_args_list:
            assert "Done" not in call.args[0], f"Unexpected 'Done.' reply: {call.args[0]}"


# ---------------------------------------------------------------------------
# Promise follow-up
# ---------------------------------------------------------------------------

class TestDetectPromise:
    def test_detects_ill_phrase(self):
        from indieclaw.handlers import _detect_promise
        snippet = _detect_promise("Sure, I'll look into that for you.")
        assert snippet is not None

    def test_detects_i_will(self):
        from indieclaw.handlers import _detect_promise
        snippet = _detect_promise("I will get back to you on this.")
        assert snippet is not None

    def test_detects_let_me(self):
        from indieclaw.handlers import _detect_promise
        snippet = _detect_promise("Let me check the logs and report back.")
        assert snippet is not None

    def test_detects_will_do(self):
        from indieclaw.handlers import _detect_promise
        snippet = _detect_promise("Will do! Give me a moment.")
        assert snippet is not None

    def test_no_promise_returns_none(self):
        from indieclaw.handlers import _detect_promise
        assert _detect_promise("The answer is 42.") is None
        assert _detect_promise("Here are the results you asked for.") is None

    def test_snippet_is_non_empty_string(self):
        from indieclaw.handlers import _detect_promise
        snippet = _detect_promise("I'll set that up shortly.")
        assert isinstance(snippet, str)
        assert len(snippet) > 0

    def test_snippet_max_length(self):
        from indieclaw.handlers import _detect_promise
        long_reply = "I'll do it. " + "x" * 200
        snippet = _detect_promise(long_reply)
        assert snippet is not None
        assert len(snippet) <= 120


class TestMaybeScheduleFollowup:
    @pytest.mark.asyncio
    async def test_schedules_task_when_promise_detected(self, monkeypatch):
        import indieclaw.handlers as _h
        monkeypatch.setattr(_h, "_followup_timers", {})
        bot = MagicMock()

        with patch("indieclaw.handlers._detect_promise", return_value="I'll do it"):
            _h._maybe_schedule_followup(bot, "123", "I'll do it soon.")

        assert "123" in _h._followup_timers
        _h._followup_timers["123"].cancel()

    @pytest.mark.asyncio
    async def test_no_task_when_no_promise(self, monkeypatch):
        import indieclaw.handlers as _h
        monkeypatch.setattr(_h, "_followup_timers", {})
        bot = MagicMock()

        with patch("indieclaw.handlers._detect_promise", return_value=None):
            _h._maybe_schedule_followup(bot, "123", "The answer is 42.")

        assert "123" not in _h._followup_timers

    @pytest.mark.asyncio
    async def test_replaces_existing_timer(self, monkeypatch):
        import indieclaw.handlers as _h
        monkeypatch.setattr(_h, "_followup_timers", {})
        bot = MagicMock()

        old_task = MagicMock()
        old_task.done = MagicMock(return_value=False)
        old_task.cancel = MagicMock()
        _h._followup_timers["123"] = old_task

        with patch("indieclaw.handlers._detect_promise", return_value="I'll do it"):
            _h._maybe_schedule_followup(bot, "123", "I'll do it soon.")

        old_task.cancel.assert_called_once()
        assert _h._followup_timers["123"] is not old_task
        _h._followup_timers["123"].cancel()


class TestFollowupCancelledOnNewMessage:
    @pytest.mark.asyncio
    async def test_new_message_cancels_followup_timer(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        import indieclaw.handlers as _h
        monkeypatch.setattr(_h, "_followup_timers", {})

        pending_task = MagicMock()
        pending_task.done = MagicMock(return_value=False)
        pending_task.cancel = MagicMock()
        _h._followup_timers["123"] = pending_task

        update = _make_update(chat_id="123", text="are you done?")
        ctx = _make_context()

        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, return_value="Done!"), \
             patch("indieclaw.handlers.get_streaming", return_value=False), \
             patch("indieclaw.claude_code.has_active_session", return_value=False):
            await _h.on_message(update, ctx)
            from indieclaw.handlers import flush_debounce
            await flush_debounce("123")

        pending_task.cancel.assert_called_once()
        assert "123" not in _h._followup_timers


class TestFollowupFiresAgentRun:
    @pytest.mark.asyncio
    async def test_followup_sends_promise_context_to_agent(self, monkeypatch):
        import indieclaw.handlers as _h
        monkeypatch.setattr(_h, "_followup_timers", {})
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")

        captured = []

        async def mock_run(chat_id, user_message, **kw):
            captured.append(user_message)
            return "Done!"

        bot = MagicMock()
        bot.send_chat_action = AsyncMock()
        bot.send_message = AsyncMock()

        with patch("indieclaw.handlers.agent_run", new_callable=AsyncMock, side_effect=mock_run), \
             patch("indieclaw.handlers.get_streaming", return_value=False), \
             patch("indieclaw.handlers._FOLLOWUP_DELAY", 0):
            _h._maybe_schedule_followup(bot, "123", "I'll set that up for you.")
            await asyncio.sleep(0.05)

        assert len(captured) == 1


# ---------------------------------------------------------------------------
# TestStatusDashboard
# ---------------------------------------------------------------------------

class TestStatusDashboard:
    @pytest.mark.asyncio
    async def test_status_shows_usage_sections(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "123")
        from indieclaw.handlers_commands import on_status

        update = MagicMock()
        update.effective_chat = MagicMock()
        update.effective_chat.id = 123
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        with patch("indieclaw.handlers_commands.load_custom_tools", return_value=[]), \
             patch("indieclaw.handlers_commands.get_current_model", return_value="claude-sonnet-4-6"), \
             patch("indieclaw.handlers_commands.get_current_effort", return_value="low"), \
             patch("indieclaw.handlers_commands.get_last_usage", return_value={
                 "input_tokens": 1204, "output_tokens": 813,
                 "cache_read_input_tokens": 892, "cache_creation_input_tokens": 0,
                 "_model": "claude-sonnet-4-6",
             }), \
             patch("indieclaw.handlers_commands.get_tool_timings", return_value=[
                 ("Bash", 2.3), ("WebSearch", 1.1), ("Read", 0.2),
             ]), \
             patch("indieclaw.handlers_commands.SessionState") as mock_ss, \
             patch("indieclaw.handlers_commands.local_version", return_value="0.1.23"), \
             patch("indieclaw.handlers_commands.BrowserManager") as mock_bm:
            mock_bm.get.return_value.backend = "lightpanda"
            mock_state = MagicMock()
            mock_state.get_usage_today.return_value = {
                "date": "2026-04-04", "input_tokens": 45200, "output_tokens": 12800,
                "cache_read_tokens": 31100, "cache_write_tokens": 0,
                "turns": 12, "cost_usd": 0.14, "models": {"claude-sonnet-4-6": 10, "claude-haiku-4-5-20251001": 2},
            }
            mock_state.get_usage_history.return_value = []
            mock_ss.load.return_value = mock_state

            import indieclaw.workspace as ws
            monkeypatch.setattr(ws, "SKILLS_DIR", MagicMock(exists=lambda: False))
            monkeypatch.setattr(ws, "MEMORY", MagicMock(read_text=MagicMock(side_effect=FileNotFoundError)))

            await on_status(update, MagicMock())

        text = update.message.reply_text.await_args[0][0]
        assert "Last turn" in text
        assert "Bash" in text
        assert "Today" in text


class TestVoiceTranscription:
    @pytest.mark.asyncio
    async def test_transcribe_voice_success(self):
        from indieclaw.handlers import _transcribe_voice
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Hello, this is a test message.\n"
        with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_result):
            result = await _transcribe_voice("/tmp/test.ogg")
        assert result == "Hello, this is a test message."

    @pytest.mark.asyncio
    async def test_transcribe_voice_failure(self):
        from indieclaw.handlers import _transcribe_voice
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_result):
            result = await _transcribe_voice("/tmp/test.ogg")
        assert result is None

    @pytest.mark.asyncio
    async def test_transcribe_voice_timeout(self):
        import subprocess
        from indieclaw.handlers import _transcribe_voice
        with patch("asyncio.to_thread", new_callable=AsyncMock, side_effect=subprocess.TimeoutExpired("claude", 30)):
            result = await _transcribe_voice("/tmp/test.ogg")
        assert result is None

    @pytest.mark.asyncio
    async def test_transcribe_voice_empty_output(self):
        from indieclaw.handlers import _transcribe_voice
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "   \n"
        with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_result):
            result = await _transcribe_voice("/tmp/test.ogg")
        assert result is None
