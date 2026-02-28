from unittest.mock import AsyncMock, MagicMock

import pytest

from indieclaw.auth import allowed_ids, default_chat_id, is_allowed, require_allowed


def test_allowed_ids_parses_env(monkeypatch):
    monkeypatch.setenv("ALLOWED_USER_IDS", "111,222, 333")
    assert allowed_ids() == {"111", "222", "333"}


def test_allowed_ids_empty_when_unset(monkeypatch):
    monkeypatch.delenv("ALLOWED_USER_IDS", raising=False)
    assert allowed_ids() == set()


def test_is_allowed_true(monkeypatch):
    monkeypatch.setenv("ALLOWED_USER_IDS", "42")
    assert is_allowed("42") is True


def test_is_allowed_false(monkeypatch):
    monkeypatch.setenv("ALLOWED_USER_IDS", "42")
    assert is_allowed("99") is False


def test_default_chat_id_returns_first(monkeypatch):
    monkeypatch.setenv("ALLOWED_USER_IDS", "10")
    assert default_chat_id() == "10"


def test_default_chat_id_empty_when_unset(monkeypatch):
    monkeypatch.delenv("ALLOWED_USER_IDS", raising=False)
    assert default_chat_id() == ""


@pytest.mark.asyncio
async def test_require_allowed_passes_allowed_user(monkeypatch):
    monkeypatch.setenv("ALLOWED_USER_IDS", "7")
    update = MagicMock()
    update.effective_chat.id = 7
    context = MagicMock()
    called = []

    @require_allowed
    async def handler(update, context):
        called.append(True)

    await handler(update, context)
    assert called == [True]


@pytest.mark.asyncio
async def test_require_allowed_blocks_unknown_user(monkeypatch):
    monkeypatch.setenv("ALLOWED_USER_IDS", "7")
    update = MagicMock()
    update.effective_chat.id = 99
    context = MagicMock()
    called = []

    @require_allowed
    async def handler(update, context):
        called.append(True)

    await handler(update, context)
    assert called == []


@pytest.mark.asyncio
async def test_require_allowed_sends_rejection_message(monkeypatch):
    """Unauthorized users with text messages should get a rejection reply."""
    monkeypatch.setenv("ALLOWED_USER_IDS", "7")
    update = MagicMock()
    update.effective_chat.id = 99
    update.message.text = "/help"
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    @require_allowed
    async def handler(update, context):
        pass

    await handler(update, context)
    update.message.reply_text.assert_awaited_once()
    msg = update.message.reply_text.await_args[0][0]
    assert "authorised" in msg.lower() or "authorized" in msg.lower()
