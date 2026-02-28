from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool
from loguru import logger

_known_tool_files: set[str] = set()
_tool_cache: dict[str, tuple[float, SdkMcpTool]] = {}
_dir_cache: tuple[float, list[SdkMcpTool]] | None = None


def _validate_schema(schema) -> list[str]:
    if not isinstance(schema, dict) or "function" not in schema:
        return ["SCHEMA must be a dict with a 'function' key"]
    if not schema["function"].get("name"):
        return ["SCHEMA.function.name is required"]
    return []


def validate_tool_module(path: Path) -> tuple[bool, list[str], ModuleType | None]:
    try:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        return False, [f"Import failed: {e}"], None

    errors: list[str] = []
    if not (hasattr(mod, "SCHEMA") and hasattr(mod, "execute")):
        errors.append("Missing SCHEMA or execute()")
    if hasattr(mod, "execute") and not callable(mod.execute):
        errors.append("execute is not callable")
    if hasattr(mod, "SCHEMA"):
        errors.extend(_validate_schema(mod.SCHEMA))

    if errors:
        return False, errors, None
    return True, [], mod


def _make_sdk_tool(name: str, desc: str, properties: dict, _required: list, execute_fn) -> SdkMcpTool:
    input_schema: dict[str, Any] = dict.fromkeys(properties, str)

    @tool(name, desc, input_schema)
    async def _dyn_tool(args: dict) -> dict:
        kwargs = {k: args.get(k) for k in properties}
        result = await asyncio.to_thread(execute_fn, **kwargs)
        return {"content": [{"type": "text", "text": str(result)}]}

    return _dyn_tool


def _load_single_tool(path: Path, mtime: float) -> SdkMcpTool | None:
    if path.name not in _known_tool_files:
        logger.warning("New tool file detected: {} — loaded without integrity check", path.name)
        _known_tool_files.add(path.name)

    ok, errors, mod = validate_tool_module(path)
    if not ok:
        for err in errors:
            logger.warning("Skipping {} — {}", path.name, err)
        return None

    try:
        fn_def = mod.SCHEMA["function"]
        params = fn_def.get("parameters", {})
        sdk_tool = _make_sdk_tool(
            fn_def["name"], fn_def.get("description", ""),
            params.get("properties", {}), params.get("required", []),
            mod.execute,
        )
        _tool_cache[str(path)] = (mtime, sdk_tool)
        logger.info("Loaded custom tool: {}", fn_def["name"])
        return sdk_tool
    except Exception as e:
        logger.error("Failed to load tool {}: {}", path.name, e)
        return None


def _evict_deleted_cache_entries(tools_dir: Path) -> None:
    current_files = {str(p) for p in tools_dir.glob("*.py")}
    for cached_path in list(_tool_cache):
        if cached_path not in current_files:
            del _tool_cache[cached_path]


def _load_or_cache_tool(path: Path) -> SdkMcpTool | None:
    str_path = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None

    if str_path in _tool_cache:
        cached_mtime, cached_tool = _tool_cache[str_path]
        if mtime == cached_mtime:
            return cached_tool

    return _load_single_tool(path, mtime)


def load_custom_tools(tools_dir: Path | None = None) -> list[SdkMcpTool]:
    if tools_dir is None:
        from . import workspace
        tools_dir = workspace.TOOLS_DIR

    global _dir_cache

    if not tools_dir.exists():
        return []

    try:
        dir_mtime = tools_dir.stat().st_mtime
    except OSError:
        return []

    if _dir_cache is not None and _dir_cache[0] == dir_mtime:
        return list(_dir_cache[1])

    _evict_deleted_cache_entries(tools_dir)

    tool_list: list[SdkMcpTool] = []
    for path in sorted(tools_dir.glob("*.py")):
        sdk_tool = _load_or_cache_tool(path)
        if sdk_tool:
            tool_list.append(sdk_tool)

    _dir_cache = (dir_mtime, tool_list)
    return tool_list


