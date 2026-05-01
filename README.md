# IndieClaw

[![PyPI](https://img.shields.io/pypi/v/indieclaw?color=blue)](https://pypi.org/project/indieclaw/)
[![Python](https://img.shields.io/pypi/pyversions/indieclaw)](https://pypi.org/project/indieclaw/)
[![CI](https://github.com/saikatkumardey/indieclaw/actions/workflows/ci.yml/badge.svg)](https://github.com/saikatkumardey/indieclaw/actions/workflows/ci.yml)
[![python-doctor](https://img.shields.io/badge/python--doctor-93%2F100-brightgreen)](https://github.com/saikatkumardey/python-doctor)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Personal AI agent on Telegram. Self-hosted, powered by Claude.

**Requires:** An [Anthropic API key](https://console.anthropic.com/) or [Claude Pro/Max subscription](https://claude.ai/). IndieClaw uses Claude as its reasoning engine — you need one or the other to run it.

## Install

```bash
pip install indieclaw
```

Or with uv:

```bash
uv tool install indieclaw
```

Then set up:

```bash
indieclaw setup
```

Setup asks for your Telegram bot token, user ID, and Claude auth. Get a bot token from [@BotFather](https://t.me/BotFather), then send `/start` to your bot — it will show your Telegram user ID.

## Run

```bash
indieclaw start       # background daemon
indieclaw start -f    # foreground
indieclaw chat        # interactive TUI (no Telegram needed)
indieclaw logs -f     # stream logs
indieclaw stop
indieclaw restart
```

`indieclaw chat` launches a terminal UI for chatting directly with your agent — useful for testing, debugging, or when you don't want to go through Telegram.

On Linux with systemd, `indieclaw setup` installs and starts the service automatically.

## Commands

| Command | |
|---------|--|
| `/status` | Model, tools, token usage |
| `/model` | Switch Claude model |
| `/effort` | Switch thinking effort |
| `/reset` | Clear conversation history |
| `/restart` | Restart the bot |
| `/update` | Pull latest and restart |
| `/cc <prompt>` | Live Claude Code session (streaming) |

## Workspace

Everything lives in `~/.indieclaw/`:

| File | Purpose |
|------|---------|
| `SOUL.md` | Agent identity and instructions |
| `USER.md` | Your profile (name, timezone, preferences) |
| `MEMORY.md` | Persistent memory across sessions |
| `crons.yaml` | Scheduled jobs |
| `skills/*/SKILL.md` | Skill docs injected into system prompt |
| `tools/*.py` | Custom tools — hot-loaded, no restart needed |
| `sessions/*.jsonl` | Conversation logs |
| `handover.md` | State snapshot across restarts |
| `subconscious.yaml` | Background reflection threads |

Override the workspace path: `INDIECLAW_HOME=/path/to/dir`

## Subconscious

A background reflection loop that runs every 2 hours. The agent reviews open threads, recent conversations, and memory — then decides whether to act (send a message, spawn a task) or stay quiet.

Threads are tracked in `~/.indieclaw/subconscious.yaml`. The agent can add, resolve, or keep threads across cycles. Disable it or change the interval in `~/.indieclaw/config.yaml`:

```yaml
subconscious_enabled: false
subconscious_interval_hours: 4
```

## Custom tools

Drop a `.py` file in `~/.indieclaw/tools/` with a `SCHEMA` dict and `execute()` function. Available on the next message, no restart needed.

## Update

```bash
indieclaw update       # from terminal
/update               # from Telegram
```

Saves a handover note before restarting and picks up where it left off.

## License

MIT
