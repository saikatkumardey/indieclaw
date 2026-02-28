# IndieClaw

[![python-doctor](https://img.shields.io/badge/python--doctor-90%2F100-brightgreen)](https://github.com/saikatkumardey/python-doctor)

Personal AI agent on Telegram. Self-hosted, powered by Claude.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/saikatkumardey/indieclaw/main/install.sh | bash
```

Or manually:

```bash
pip install uv
uv tool install git+https://github.com/saikatkumardey/indieclaw
indieclaw setup
```

Setup asks for your Telegram bot token, user ID, and Claude auth. Get a bot token from [@BotFather](https://t.me/BotFather) and your user ID from [@userinfobot](https://t.me/userinfobot).

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
