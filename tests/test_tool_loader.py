"""Dynamic tool loader tests."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


VALID_TOOL_SRC = '''
SCHEMA = {
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Return pong.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

def execute() -> str:
    return "pong"
'''

TOOL_WITH_PARAM_SRC = '''
SCHEMA = {
    "type": "function",
    "function": {
        "name": "greet",
        "description": "Greet someone.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name to greet"},
            },
            "required": ["name"],
        },
    },
}

def execute(name: str) -> str:
    return f"Hello, {name}!"
'''

BROKEN_TOOL_SRC = "SCHEMA = {}"  # missing execute


def test_load_custom_tools_returns_tool(tmp_path):
    """A valid tool file should be loaded as an SDK tool with .name attribute."""
    from indieclaw.tool_loader import load_custom_tools

    (tmp_path / "ping.py").write_text(VALID_TOOL_SRC)
    tools = load_custom_tools(tmp_path)

    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "ping"


def test_load_custom_tools_forward_works(tmp_path):
    """Loaded tool's handler should call execute() and return content dict."""
    from indieclaw.tool_loader import load_custom_tools

    (tmp_path / "ping.py").write_text(VALID_TOOL_SRC)
    tools = load_custom_tools(tmp_path)
    # SdkMcpTool stores the async function in .handler
    result = asyncio.run(tools[0].handler({}))
    assert result["content"][0]["text"] == "pong"


def test_load_custom_tools_with_param(tmp_path):
    """Tool with parameters should pass them through correctly."""
    from indieclaw.tool_loader import load_custom_tools

    (tmp_path / "greet.py").write_text(TOOL_WITH_PARAM_SRC)
    tools = load_custom_tools(tmp_path)
    result = asyncio.run(tools[0].handler({"name": "Alice"}))
    assert result["content"][0]["text"] == "Hello, Alice!"


def test_load_custom_tools_skips_broken(tmp_path):
    """Files missing execute() should be skipped gracefully."""
    from indieclaw.tool_loader import load_custom_tools

    (tmp_path / "broken.py").write_text(BROKEN_TOOL_SRC)
    (tmp_path / "ping.py").write_text(VALID_TOOL_SRC)

    tools = load_custom_tools(tmp_path)
    assert len(tools) == 1
    assert tools[0].name == "ping"


def test_load_custom_tools_empty_dir(tmp_path):
    """Empty directory should return an empty list."""
    from indieclaw.tool_loader import load_custom_tools

    tools = load_custom_tools(tmp_path)
    assert tools == []


def test_load_custom_tools_nonexistent_dir(tmp_path):
    """Non-existent directory should return an empty list."""
    from indieclaw.tool_loader import load_custom_tools

    tools = load_custom_tools(tmp_path / "no_such_dir")
    assert tools == []


# --- validate_tool_module tests ---

def test_validate_valid_tool(tmp_path):
    """A valid tool module should pass validation."""
    from indieclaw.tool_loader import validate_tool_module

    p = tmp_path / "good.py"
    p.write_text(VALID_TOOL_SRC)
    ok, errors, mod = validate_tool_module(p)
    assert ok is True
    assert errors == []
    assert mod is not None
    assert callable(mod.execute)


def test_validate_missing_execute(tmp_path):
    """Module without execute() should fail validation."""
    from indieclaw.tool_loader import validate_tool_module

    p = tmp_path / "no_exec.py"
    p.write_text(BROKEN_TOOL_SRC)
    ok, errors, _mod = validate_tool_module(p)
    assert ok is False
    assert any("Missing SCHEMA or execute" in e for e in errors)


def test_validate_bad_schema(tmp_path):
    """Module with SCHEMA missing 'function' key should fail."""
    from indieclaw.tool_loader import validate_tool_module

    p = tmp_path / "bad_schema.py"
    p.write_text('SCHEMA = {"type": "function"}\ndef execute(): return "x"\n')
    ok, errors, _mod = validate_tool_module(p)
    assert ok is False
    assert any("function" in e for e in errors)


def test_loader_ignores_staging_dir(tmp_path):
    """Staging subdirectory should not be picked up by load_custom_tools."""
    from indieclaw.tool_loader import load_custom_tools

    staging = tmp_path / ".staging"
    staging.mkdir()
    (staging / "hidden.py").write_text(VALID_TOOL_SRC)
    # Only .py files directly in tools_dir are loaded
    tools = load_custom_tools(tmp_path)
    assert len(tools) == 0


def test_deleted_tool_evicted_from_cache(tmp_path):
    """Deleting a tool file should remove it from _tool_cache on next load."""
    import indieclaw.tool_loader as tl

    # Save and restore global cache state
    old_dir_cache = tl._dir_cache
    old_tool_cache = dict(tl._tool_cache)
    try:
        tl._dir_cache = None
        tl._tool_cache.clear()

        (tmp_path / "ping.py").write_text(VALID_TOOL_SRC)
        tools = tl.load_custom_tools(tmp_path)
        assert len(tools) == 1
        assert str(tmp_path / "ping.py") in tl._tool_cache

        # Delete the tool file and invalidate dir cache
        (tmp_path / "ping.py").unlink()
        tl._dir_cache = None  # force rescan

        tools = tl.load_custom_tools(tmp_path)
        assert len(tools) == 0
        assert str(tmp_path / "ping.py") not in tl._tool_cache
    finally:
        tl._dir_cache = old_dir_cache
        tl._tool_cache.clear()
        tl._tool_cache.update(old_tool_cache)
