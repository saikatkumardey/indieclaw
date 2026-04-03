from __future__ import annotations

import os
from typing import Any

from . import workspace

_cache: tuple[str, float, Config] | None = None


class Config:

    DEFAULTS: dict[str, Any] = {
        "model": "claude-sonnet-4-6",
        "effort": "low",
        "max_turns": 10,
"btw_model": "claude-haiku-4-5-20251001",
        "cron_model": "claude-sonnet-4-6",
        "subconscious_enabled": True,
        "subconscious_interval_hours": 2,
        "subconscious_model": "claude-sonnet-4-6",
        "streaming": True,
        "debounce_seconds": 0.5,
        "interrupt_on_message": True,
        "max_budget_usd": 0.0,
        "agent_stall_timeout": 120,
        "agent_initial_timeout": 300,
    }

    def __init__(self, data: dict | None = None) -> None:
        self._data = dict(self.DEFAULTS)
        if data:
            self._data.update(data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        global _cache
        if key not in self.DEFAULTS:
            raise KeyError(f"Unknown config key: {key!r}")
        expected = type(self.DEFAULTS[key])
        if not isinstance(value, expected):
            raise TypeError(f"{key!r} must be {expected.__name__}, got {type(value).__name__}")
        self._data[key] = value
        self._save()
        _cache = None  # invalidate on write

    def to_dict(self) -> dict:
        return dict(self._data)

    def _save(self) -> None:
        workspace.write_json(workspace.CONFIG, self._data)

    @classmethod
    def load(cls) -> Config:
        global _cache
        path = workspace.CONFIG
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0

        if _cache is not None and _cache[0] == str(path) and _cache[1] == mtime:
            return _cache[2]

        data = workspace.read_json(path)

        if "model" not in data and (env_model := os.getenv("INDIECLAW_MODEL")):
            data["model"] = env_model
        instance = cls(data)
        _cache = (str(path), mtime, instance)
        return instance
