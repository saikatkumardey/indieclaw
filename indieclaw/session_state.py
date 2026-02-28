from __future__ import annotations

from datetime import datetime, timezone

from claude_agent_sdk import ResultMessage

from . import workspace

_ZERO_TOKENS = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "turns": 0}


class SessionState:
    def __init__(self, data: dict | None = None) -> None:
        self._data = data or {
            "version": 1,
            "updated_at": None,
            "sessions": {},
            "usage_today": {"date": None, **_ZERO_TOKENS},
        }
        self._data.setdefault("version", 1)
        self._data.setdefault("sessions", {})
        self._data.setdefault("usage_today", {"date": None, **_ZERO_TOKENS})

    def record_turn(self, chat_id: str, result: ResultMessage) -> None:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")

        usage = result.usage or {}
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_write = usage.get("cache_creation_input_tokens", 0)

        turns = result.num_turns or 1

        sess = self._data["sessions"].setdefault(chat_id, {
            "last_active": None, **_ZERO_TOKENS,
        })
        sess["last_active"] = now.isoformat()
        sess["input_tokens"] += input_tokens
        sess["output_tokens"] += output_tokens
        sess["cache_read_tokens"] += cache_read
        sess["cache_write_tokens"] += cache_write
        sess["turns"] += turns

        usage_today = self._data["usage_today"]
        if usage_today.get("date") != today:
            usage_today.update(date=today, **_ZERO_TOKENS)

        usage_today["input_tokens"] += input_tokens
        usage_today["output_tokens"] += output_tokens
        usage_today["cache_read_tokens"] += cache_read
        usage_today["cache_write_tokens"] += cache_write
        usage_today["turns"] += turns

        self._data["updated_at"] = now.isoformat()
        self._save()

    def get_session(self, chat_id: str) -> dict:
        return dict(self._data["sessions"].get(chat_id, {}))

    def get_usage_today(self) -> dict:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        usage = self._data["usage_today"]
        if usage.get("date") != today:
            return {"date": today, **_ZERO_TOKENS}
        return dict(usage)

    def to_dict(self) -> dict:
        return dict(self._data)

    def _save(self) -> None:
        workspace.write_json(workspace.SESSION_STATE, self._data)

    @classmethod
    def load(cls) -> SessionState:
        data = workspace.read_json(workspace.SESSION_STATE)
        return cls(data if data else None)
