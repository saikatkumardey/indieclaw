# CLAUDE.md

## Commands

```bash
uv sync                    # install dev deps
uv run pytest              # run tests (always run full suite before pushing)
uv run pytest tests/test_agent.py::test_run_returns_string  # single test
```

## Architecture

Telegram bot wrapping `claude-agent-sdk`. State lives in `~/.indieclaw/` (not in this repo).

**Request flow:** Telegram message → `handlers.py` → `agent.run(chat_id, msg)` → short-lived `ClaudeSDKClient` (context manager) → tool loop → reply via Telegram.

**Session lifecycle:** Short-lived client per message. Session state persists via SDK's JSONL files; `resume=session_id` reconnects to the same conversation. `_session_ids[chat_id]` tracks the last session ID. Reset by `/reset` (clears session ID) or model/effort change. Context compaction handled natively by the SDK.

**Subagents:** Native SDK `AgentDefinition` (`task-runner`) for long autonomous tasks. Claude spawns them via the built-in `Agent` tool. No custom task registry.

**System prompt** (`prompt_builder.py`): Built per client creation. Order: workspace context → SOUL.md → USER.md → skills list → MEMORY.md. Current timestamp prepended to user message for cache-friendliness.

## Key files

| File | What it does |
|------|-------------|
| `agent.py` | Short-lived SDK client per message, `run()`, `run_streaming()`, native subagents, hooks |
| `handlers.py` | Telegram command/message handlers, typing loop, reactions, `/update` |
| `tools_sdk.py` | Built-in SDK tools (telegram, browser, search_sessions, etc.) |
| `tool_loader.py` | Hot-loads `~/.indieclaw/tools/*.py` as SDK tools |
| `browser.py` | Playwright browser manager (lazy singleton, per-chat contexts) |
| `main.py` | CLI entrypoint, bot wiring, startup/shutdown hooks |
| `version.py` | Shared version utilities: `local_version()`, `check_remote_version()`, `get_update_summary()` |

## Tools

**Built-in** (5): `Bash`, `Read`, `Write`, `WebSearch`, `WebFetch`

**SDK tools** (`tools_sdk.py`): `telegram_send`, `telegram_edit`, `telegram_send_file`, `telegram_send_voice`, `telegram_react`, `self_restart`, `self_update`, `update_config`, `read_skill`, `search_sessions`, `browse`, `browser_click`, `browser_type`, `browser_screenshot`, `browser_eval`, `test_tool`, `deploy_tool`, `disable_tool`, `update_subconscious`, `reflect`

**Dynamic tools** (`~/.indieclaw/tools/*.py`): Must export `SCHEMA` (OpenAI function schema dict) and `execute()`. All params arrive as strings — always coerce defensively.

## Things that bite you

- **`/update` handler is separate from `self_update` tool.** Both call `uv tool install --upgrade` then SIGTERM for systemd restart. Version logic is shared via `version.py` — don't duplicate.
- **Tool wrapper casts ALL params to strings.** Every tool (built-in and dynamic) must defensively coerce: `int()`, `json.loads()` with try/except. Never trust types.
- **System prompt is built once per client, not per message.** Don't add per-message dynamic content to `_system_prompt()` — it breaks prompt caching. Put ephemeral info in the user message instead.
- **Dynamic tools are loaded module-level** via `reload_dynamic_tools()`, called at the start of each `run()`. Adding/modifying tools no longer resets sessions.
- **Edited messages are reprocessed.** `on_message` handles both `update.message` and `update.edited_message`. Use `update.edited_message or update.message` to get the right one.
- **Test mocks need `edited_message = None`.** MagicMock is truthy — tests that create mock Updates must explicitly set `update.edited_message = None` or the handler picks up the mock as an edit.
- **`importlib.metadata` doesn't work in uv tool installs.** `importlib.metadata.version("indieclaw")` throws `PackageNotFoundError` when installed via `uv tool install`. Use `version.local_version()` which falls back to parsing `uv tool list` output, then `pyproject.toml`.
- **Always end-to-end test before pushing, not just unit tests.** Unit tests passing doesn't mean the feature works in the real environment. For version checks, runtime metadata, or anything environment-dependent — verify the actual function output in a quick `uv run python -c "..."` smoke test.

## What makes code maintainable

- **Reduce layers a reader has to trace.** Flat is better than deep. If understanding a change requires jumping through 3+ files or call frames, collapse the indirection. Prefer direct calls over abstract dispatch when there's only one use case.
- **Reduce state a reader has to hold in their head.** Short functions, small scopes, no hidden side effects. A reader should be able to understand a function from its signature and body alone — without needing to track what mutated elsewhere.

## Conventions

- **TDD: write tests first, then implement.** Write a failing test that captures the expected behavior, then write the code to make it pass. This catches environment mismatches and edge cases before they ship.
- **Release:** `make release V=x.y.z` — updates `pyproject.toml` version, generates `CHANGELOG.md` from commits since last tag, runs `uv lock`, commits, and creates annotated git tag. Then `git push && git push --tags`.
- Never add co-author lines or "Generated with Claude Code" to commits.
- Run full test suite before pushing — `uv run pytest`.
- Keep `CUSTOM_TOOLS` list at the bottom of `tools_sdk.py` in sync when adding tools.
- Browser tools use lazy imports (`from .browser import BrowserManager`) to avoid importing Playwright at module load.
- **Be lean with context.** Read files with `offset`/`limit` when you only need a section. Batch related shell commands (commit + push + upgrade + restart) into one chained call. Don't re-read files you just wrote. Use parallel tool calls. Skip exploratory reads when you already know the structure.
