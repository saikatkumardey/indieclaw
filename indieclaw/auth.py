from __future__ import annotations

import functools
import os
from collections.abc import Callable

from loguru import logger


def allowed_ids() -> set[str]:
    raw = os.getenv("ALLOWED_USER_IDS", "")
    return {cid.strip() for cid in raw.split(",") if cid.strip()}


def is_allowed(chat_id: str | int) -> bool:
    return str(chat_id) in allowed_ids()


def default_chat_id() -> str:
    ids = allowed_ids()
    return next(iter(ids), "") if ids else ""


def require_allowed(fn: Callable) -> Callable:
    @functools.wraps(fn)
    async def wrapper(update, context):
        if update.effective_chat and is_allowed(update.effective_chat.id):
            return await fn(update, context)
        # Give feedback on explicit commands only (not silent messages/reactions)
        msg = getattr(update, "message", None)
        if msg and hasattr(msg, "reply_text") and getattr(msg, "text", ""):
            try:
                await msg.reply_text("Not authorised for this bot.")
            except Exception:
                logger.debug("failed to send auth rejection to %s", update.effective_chat.id)
    return wrapper
