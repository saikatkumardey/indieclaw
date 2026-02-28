"""Tests for indieclaw doctor."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from indieclaw.doctor import (
    Status,
    _check_process,
    _check_runtime,
    _check_state,
    _check_workspace,
    _human_size,
    run,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _patch_workspace(tmp_path: Path, monkeypatch) -> None:
    """Point workspace constants at tmp_path so tests are isolated."""
    import indieclaw.workspace as ws

    monkeypatch.setattr(ws, "HOME", tmp_path)
    monkeypatch.setattr(ws, "SOUL", tmp_path / "SOUL.md")
    monkeypatch.setattr(ws, "AGENT", tmp_path / "AGENT.md")
    monkeypatch.setattr(ws, "USER", tmp_path / "USER.md")
    monkeypatch.setattr(ws, "MEMORY", tmp_path / "MEMORY.md")
    monkeypatch.setattr(ws, "CRONS", tmp_path / "crons.yaml")
    monkeypatch.setattr(ws, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(ws, "TOOLS_DIR", tmp_path / "tools")
    monkeypatch.setattr(ws, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(ws, "HANDOVER", tmp_path / "handover.md")
    monkeypatch.setattr(ws, "CONFIG", tmp_path / "indieclaw.json")
    monkeypatch.setattr(ws, "SESSION_STATE", tmp_path / "session_state.json")


def _make_healthy_workspace(tmp_path: Path) -> None:
    """Create a fully healthy workspace in tmp_path."""
    for d in ("skills", "tools", "uploads", "sessions"):
        (tmp_path / d).mkdir(exist_ok=True)
    for name in ("SOUL.md", "AGENT.md", "USER.md", "MEMORY.md"):
        (tmp_path / name).write_text(f"# {name}\nContent here.\n")
    (tmp_path / "crons.yaml").write_text(yaml.dump({"jobs": []}))
    (tmp_path / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=123:ABC\nALLOWED_USER_IDS=12345\n"
    )


# ---------------------------------------------------------------------------
# _human_size
# ---------------------------------------------------------------------------


def test_human_size_bytes():
    assert _human_size(512) == "512 B"


def test_human_size_kb():
    assert _human_size(2048) == "2.0 KB"


def test_human_size_mb():
    assert _human_size(5 * 1024 * 1024) == "5.0 MB"


def test_human_size_gb():
    assert _human_size(3 * 1024 * 1024 * 1024) == "3.0 GB"


# ---------------------------------------------------------------------------
# Workspace checks
# ---------------------------------------------------------------------------


def test_workspace_missing_home(tmp_path, monkeypatch):
    missing = tmp_path / "nonexistent"
    import indieclaw.workspace as ws

    monkeypatch.setattr(ws, "HOME", missing)
    results = _check_workspace()
    assert len(results) == 1
    assert results[0].status == Status.FAIL
    assert "missing" in results[0].message.lower()


def test_workspace_complete(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    results = _check_workspace()
    assert all(c.status == Status.OK for c in results), [
        (c.status, c.message) for c in results if c.status != Status.OK
    ]


def test_workspace_missing_subdir(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    # Remove one subdir
    (tmp_path / "tools").rmdir()
    results = _check_workspace()
    tool_checks = [c for c in results if "tools/" in c.message]
    assert len(tool_checks) == 1
    assert tool_checks[0].status == Status.FAIL


def test_workspace_empty_core_file(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    (tmp_path / "SOUL.md").write_text("")
    results = _check_workspace()
    soul_checks = [c for c in results if "SOUL.md" in c.message]
    assert any(c.status == Status.WARN for c in soul_checks)


def test_workspace_invalid_crons_yaml(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    (tmp_path / "crons.yaml").write_text("{{invalid: yaml: [")
    results = _check_workspace()
    yaml_checks = [c for c in results if "invalid YAML" in c.message]
    assert len(yaml_checks) == 1
    assert yaml_checks[0].status == Status.FAIL


def test_workspace_missing_env(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    (tmp_path / ".env").unlink()
    results = _check_workspace()
    env_checks = [c for c in results if ".env" in c.message]
    assert any(c.status == Status.FAIL for c in env_checks)


def test_workspace_env_missing_var(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=abc\n")
    results = _check_workspace()
    uid_checks = [c for c in results if "ALLOWED_USER_IDS" in c.message]
    assert len(uid_checks) == 1
    assert uid_checks[0].status == Status.FAIL


def test_workspace_invalid_config_json(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    (tmp_path / "indieclaw.json").write_text("{broken json")
    results = _check_workspace()
    cfg_checks = [c for c in results if "indieclaw.json" in c.message]
    assert any(c.status == Status.FAIL for c in cfg_checks)


def test_workspace_low_max_turns(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    (tmp_path / "indieclaw.json").write_text(json.dumps({"max_turns": 1}))
    results = _check_workspace()
    cfg_checks = [c for c in results if "max_turns" in c.message and "indieclaw.json" in c.message]
    assert len(cfg_checks) == 1
    assert cfg_checks[0].status == Status.WARN


def test_workspace_valid_config_json(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    (tmp_path / "indieclaw.json").write_text(json.dumps({"max_turns": 10}))
    results = _check_workspace()
    cfg_checks = [c for c in results if "indieclaw.json" in c.message]
    assert all(c.status == Status.OK for c in cfg_checks)


def test_workspace_invalid_session_state(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    (tmp_path / "session_state.json").write_text("not json")
    results = _check_workspace()
    ss_checks = [c for c in results if "session_state.json" in c.message]
    assert any(c.status == Status.FAIL for c in ss_checks)


# ---------------------------------------------------------------------------
# Runtime checks
# ---------------------------------------------------------------------------


def test_runtime_valid_telegram_token(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": {"username": "TestBot"}}

    with patch("indieclaw.doctor.requests.get", return_value=mock_resp) as mock_get:
        results = _check_runtime()

    mock_get.assert_called_once()
    tg_checks = [c for c in results if "Telegram" in c.message or "telegram" in c.message]
    assert any(c.status == Status.OK and "@TestBot" in c.message for c in tg_checks)


def test_runtime_invalid_telegram_token(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.json.return_value = {}

    with patch("indieclaw.doctor.requests.get", return_value=mock_resp):
        results = _check_runtime()

    tg_checks = [c for c in results if "Telegram" in c.message or "telegram" in c.message]
    assert any(c.status == Status.FAIL for c in tg_checks)


def test_runtime_telegram_network_error(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch("indieclaw.doctor.requests.get", side_effect=ConnectionError("timeout")):
        results = _check_runtime()

    tg_checks = [c for c in results if "Telegram" in c.message or "telegram" in c.message or "verify" in c.message]
    assert any(c.status == Status.WARN for c in tg_checks)


def test_runtime_no_token_skipped(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    (tmp_path / ".env").write_text("ALLOWED_USER_IDS=123\n")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch("indieclaw.doctor.requests.get") as mock_get:
        results = _check_runtime()

    mock_get.assert_not_called()
    tg_checks = [c for c in results if "token" in c.message.lower()]
    assert any(c.status == Status.WARN for c in tg_checks)


def test_runtime_api_key_present(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    (tmp_path / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=123:ABC\nALLOWED_USER_IDS=123\nANTHROPIC_API_KEY=sk-test-key\n"
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": {"username": "Bot"}}

    with patch("indieclaw.doctor.requests.get", return_value=mock_resp):
        results = _check_runtime()

    auth_checks = [c for c in results if "ANTHROPIC_API_KEY" in c.message]
    assert any(c.status == Status.OK for c in auth_checks)


def test_runtime_no_auth_at_all(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": {"username": "Bot"}}

    with patch("indieclaw.doctor.requests.get", return_value=mock_resp), patch("shutil.which", return_value=None):
        results = _check_runtime()

    auth_checks = [c for c in results if "auth" in c.message.lower() or "API key" in c.message]
    assert any(c.status == Status.FAIL for c in auth_checks)


def test_runtime_known_model(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    monkeypatch.setenv("INDIECLAW_MODEL", "claude-sonnet-4-6")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch("indieclaw.doctor.requests.get", side_effect=ConnectionError):
        results = _check_runtime()

    model_checks = [c for c in results if "Model" in c.message or "model" in c.message]
    assert any(c.status == Status.OK for c in model_checks)


def test_runtime_unknown_model(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    monkeypatch.setenv("INDIECLAW_MODEL", "claude-future-99")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch("indieclaw.doctor.requests.get", side_effect=ConnectionError):
        results = _check_runtime()

    model_checks = [c for c in results if "model" in c.message.lower()]
    assert any(c.status == Status.WARN for c in model_checks)


def test_runtime_valid_custom_tool(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    tool_src = '''
SCHEMA = {
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Return pong.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

def execute() -> str:
    return "pong"
'''
    (tmp_path / "tools" / "ping.py").write_text(tool_src)

    with patch("indieclaw.doctor.requests.get", side_effect=ConnectionError):
        results = _check_runtime()

    tool_checks = [c for c in results if "ping.py" in c.message]
    assert len(tool_checks) == 1
    assert tool_checks[0].status == Status.OK


def test_runtime_broken_custom_tool(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "tools" / "broken.py").write_text("raise RuntimeError('boom')")

    with patch("indieclaw.doctor.requests.get", side_effect=ConnectionError):
        results = _check_runtime()

    tool_checks = [c for c in results if "broken.py" in c.message]
    assert len(tool_checks) == 1
    assert tool_checks[0].status == Status.FAIL


def test_runtime_tool_missing_schema(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "tools" / "nope.py").write_text("def execute(): return 'hi'\n")

    with patch("indieclaw.doctor.requests.get", side_effect=ConnectionError):
        results = _check_runtime()

    tool_checks = [c for c in results if "nope.py" in c.message]
    assert len(tool_checks) == 1
    assert tool_checks[0].status == Status.FAIL
    assert "SCHEMA" in tool_checks[0].message


def test_runtime_valid_cron_expression(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "crons.yaml").write_text(yaml.dump({
        "jobs": [{"id": "morning", "cron": "0 9 * * *", "prompt": "hello"}]
    }))

    with patch("indieclaw.doctor.requests.get", side_effect=ConnectionError):
        results = _check_runtime()

    cron_checks = [c for c in results if "morning" in c.message]
    assert len(cron_checks) == 1
    assert cron_checks[0].status == Status.OK


def test_runtime_invalid_cron_expression(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "crons.yaml").write_text(yaml.dump({
        "jobs": [{"id": "bad", "cron": "not a cron", "prompt": "hello"}]
    }))

    with patch("indieclaw.doctor.requests.get", side_effect=ConnectionError):
        results = _check_runtime()

    cron_checks = [c for c in results if "bad" in c.message]
    assert len(cron_checks) == 1
    assert cron_checks[0].status == Status.FAIL


def test_runtime_edge_tts_available(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch("indieclaw.doctor.requests.get", side_effect=ConnectionError), \
         patch("indieclaw.doctor.shutil.which", side_effect=lambda x: f"/usr/bin/{x}"):
        results = _check_runtime()

    tts_checks = [c for c in results if "edge-tts" in c.message.lower()]
    assert len(tts_checks) == 1
    assert tts_checks[0].status == Status.OK


def test_runtime_edge_tts_missing(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def _selective_which(name):
        if name in ("edge-tts", "ffmpeg"):
            return None
        return f"/usr/bin/{name}"

    with patch("indieclaw.doctor.requests.get", side_effect=ConnectionError), \
         patch("indieclaw.doctor.shutil.which", side_effect=_selective_which):
        results = _check_runtime()

    tts_checks = [c for c in results if "edge-tts" in c.message.lower() or "ffmpeg" in c.message.lower()]
    assert len(tts_checks) >= 1
    assert any(c.status == Status.WARN for c in tts_checks)


# ---------------------------------------------------------------------------
# Process checks
# ---------------------------------------------------------------------------


def test_process_running(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    with patch("indieclaw.daemon.is_running", return_value=(True, 12345)):
        results = _check_process()
    assert len(results) == 1
    assert results[0].status == Status.OK
    assert "12345" in results[0].message


def test_process_not_running(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    with patch("indieclaw.daemon.is_running", return_value=(False, None)):
        results = _check_process()
    assert len(results) == 1
    assert results[0].status == Status.WARN


# ---------------------------------------------------------------------------
# State checks
# ---------------------------------------------------------------------------


def test_state_small_sessions(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    (tmp_path / "sessions" / "2025-01-01.jsonl").write_text('{"msg":"hi"}\n')
    results = _check_state()
    log_checks = [c for c in results if "Session logs" in c.message]
    assert len(log_checks) == 1
    assert log_checks[0].status == Status.OK


def test_state_large_sessions(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    # Create a file that's bigger than the threshold
    big_file = tmp_path / "sessions" / "big.jsonl"
    big_file.write_bytes(b"x" * (101 * 1024 * 1024))
    results = _check_state()
    log_checks = [c for c in results if "Session logs" in c.message]
    assert len(log_checks) == 1
    assert log_checks[0].status == Status.WARN


def test_state_stale_handover_present(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    (tmp_path / "handover.md").write_text("leftover state")
    results = _check_state()
    hv_checks = [c for c in results if "handover" in c.message.lower()]
    assert any(c.status == Status.WARN for c in hv_checks)


def test_state_no_handover(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    results = _check_state()
    hv_checks = [c for c in results if "handover" in c.message.lower()]
    assert all(c.status == Status.OK for c in hv_checks)


def test_state_many_session_entries(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    sessions = {f"chat{i}": {"turns": 1} for i in range(60)}
    (tmp_path / "session_state.json").write_text(
        json.dumps({"sessions": sessions})
    )
    results = _check_state()
    ss_checks = [c for c in results if "Session state" in c.message]
    assert len(ss_checks) == 1
    assert ss_checks[0].status == Status.WARN


# ---------------------------------------------------------------------------
# Integration: run()
# ---------------------------------------------------------------------------


def test_run_healthy_workspace(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    _make_healthy_workspace(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": {"username": "TestBot"}}

    with patch("indieclaw.doctor.requests.get", return_value=mock_resp):
        exit_code = run()

    assert exit_code == 0


def test_run_empty_workspace(tmp_path, monkeypatch):
    """run() on a missing workspace doesn't crash."""
    missing = tmp_path / "nonexistent"
    import indieclaw.workspace as ws

    monkeypatch.setattr(ws, "HOME", missing)
    monkeypatch.setattr(ws, "CRONS", missing / "crons.yaml")
    monkeypatch.setattr(ws, "TOOLS_DIR", missing / "tools")
    monkeypatch.setattr(ws, "CONFIG", missing / "indieclaw.json")
    monkeypatch.setattr(ws, "SESSION_STATE", missing / "session_state.json")
    monkeypatch.setattr(ws, "HANDOVER", missing / "handover.md")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("INDIECLAW_MODEL", raising=False)

    with patch("shutil.which", return_value=None):
        exit_code = run()

    assert exit_code == 1  # failures expected
