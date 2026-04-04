from __future__ import annotations

from datetime import datetime, timezone

from claude_agent_sdk import ResultMessage

from . import workspace

_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-6":           {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-sonnet-4-6":         {"input": 3.0,  "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-haiku-4-5-20251001": {"input": 0.8,  "output": 4.0,  "cache_read": 0.08, "cache_write": 1.0},
}

_FALLBACK_MODEL = "claude-sonnet-4-6"


def estimate_cost(model: str, input_tokens: int, output_tokens: int,
                  cache_read: int = 0, cache_write: int = 0) -> float:
    rates = _PRICING.get(model, _PRICING[_FALLBACK_MODEL])
    return (
        input_tokens * rates["input"]
        + output_tokens * rates["output"]
        + cache_read * rates["cache_read"]
        + cache_write * rates["cache_write"]
    ) / 1_000_000


_ZERO_TOKENS = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "turns": 0}

_HISTORY_MAX = 7


class SessionState:
    def __init__(self, data: dict | None = None) -> None:
        self._data = data or {
            "version": 1,
            "updated_at": None,
            "sessions": {},
            "usage_today": {"date": None, **_ZERO_TOKENS, "cost_usd": 0.0, "models": {}},
            "usage_history": [],
        }
        self._data.setdefault("version", 1)
        self._data.setdefault("sessions", {})
        self._data.setdefault("usage_today", {"date": None, **_ZERO_TOKENS, "cost_usd": 0.0, "models": {}})
        self._data.setdefault("usage_history", [])
        self._data["usage_today"].setdefault("cost_usd", 0.0)
        self._data["usage_today"].setdefault("models", {})

    def record_turn(self, chat_id: str, result: ResultMessage) -> None:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")

        usage = result.usage or {}
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_write = usage.get("cache_creation_input_tokens", 0)
        _raw_model = getattr(result, "model", "")
        model = _raw_model if isinstance(_raw_model, str) else ""

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
            self._roll_history(usage_today)
            usage_today.update(date=today, **_ZERO_TOKENS, cost_usd=0.0, models={})

        usage_today["input_tokens"] += input_tokens
        usage_today["output_tokens"] += output_tokens
        usage_today["cache_read_tokens"] += cache_read
        usage_today["cache_write_tokens"] += cache_write
        usage_today["turns"] += turns

        cost = estimate_cost(model, input_tokens, output_tokens, cache_read, cache_write)
        usage_today["cost_usd"] = round(usage_today.get("cost_usd", 0.0) + cost, 6)
        usage_today.setdefault("models", {})[model] = usage_today["models"].get(model, 0) + 1

        self._data["updated_at"] = now.isoformat()
        self._save()

    def _roll_history(self, old_today: dict) -> None:
        if not old_today.get("date"):
            return
        snapshot = {
            "date": old_today["date"],
            "input_tokens": old_today.get("input_tokens", 0),
            "output_tokens": old_today.get("output_tokens", 0),
            "cache_read_tokens": old_today.get("cache_read_tokens", 0),
            "cache_write_tokens": old_today.get("cache_write_tokens", 0),
            "turns": old_today.get("turns", 0),
            "cost_usd": old_today.get("cost_usd", 0.0),
            "models": dict(old_today.get("models", {})),
        }
        history = self._data["usage_history"]
        history.append(snapshot)
        if len(history) > _HISTORY_MAX:
            del history[:-_HISTORY_MAX]

    def get_usage_history(self) -> list[dict]:
        return list(self._data.get("usage_history", []))

    def get_session(self, chat_id: str) -> dict:
        return dict(self._data["sessions"].get(chat_id, {}))

    def get_usage_today(self) -> dict:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        usage = self._data["usage_today"]
        if usage.get("date") != today:
            return {"date": today, **_ZERO_TOKENS, "cost_usd": 0.0, "models": {}}
        return dict(usage)

    def to_dict(self) -> dict:
        return dict(self._data)

    def _save(self) -> None:
        workspace.write_json(workspace.SESSION_STATE, self._data)

    @classmethod
    def load(cls) -> SessionState:
        data = workspace.read_json(workspace.SESSION_STATE)
        return cls(data if data else None)
