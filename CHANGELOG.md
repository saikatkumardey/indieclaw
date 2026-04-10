# Changelog

All notable changes to IndieClaw will be documented in this file.

## [0.1.39] - 2026-04-10

- Remove dead code, redundant copies, and duplicate logic across codebase


## [0.1.38] - 2026-04-10




## [0.1.37] - 2026-04-08

- Show branch name in startup message when running from non-main branch
- Revert "release: v0.1.37"


## [0.1.36] - 2026-04-07

- clarify Anthropic subscription/API key requirement in README
- bump: 0.1.35
- fix bootstrap: convert indieclaw JSONL to transcript before mining
- bump: 0.1.34
- add mempalace bootstrap script for session log mining
- add mempalace_knowledge tool and tests
- add mempalace_diary tool and tests
- add mempalace dependency for persistent memory
- fix: add tasks/task to _BOT_COMMANDS so sync_commands registers them
- bump: 0.1.33
- fix: suppress duplicate reply when telegram_send already confirmed


## [0.1.32] - 2026-04-05

- feat: add acknowledge-before-working rule to shipped AGENT.md template


## [0.1.31] - 2026-04-05

- feat: unified /task add/done/drop + /tasks list, replacing /threads
- release: v0.1.30
- feat: /threads command for subconscious transparency
- ci: update python-doctor badge to 90/100
- release: v0.1.29
- feat: auto-inject recent context on new session start
- ci: update python-doctor badge to 91/100


## [0.1.30] - 2026-04-05

- feat: /threads command for subconscious transparency
- release: v0.1.29
- feat: auto-inject recent context on new session start
- ci: update python-doctor badge to 91/100


## [0.1.29] - 2026-04-04

- feat: auto-inject recent context on new session start


## [0.1.28] - 2026-04-04

- feat: richer error classification with actionable messages
- release: v0.1.27
- feat: skill auto-indexing with descriptions in system prompt
- ci: update python-doctor badge to 90/100


## [0.1.27] - 2026-04-04

- feat: skill auto-indexing with descriptions in system prompt


## [0.1.26] - 2026-04-04

- feat: transcribe voice messages via claude CLI before passing to agent


## [0.1.25] - 2026-04-04

- style: reformat import line
- feat: extended /status dashboard with tool timing, cost, and history
- feat: store model name in _last_usage for status display
- feat: per-tool timing via PostToolUse hook
- feat: add model/cost tracking and 7-day usage history to SessionState
- feat: add estimate_cost helper for token-to-dollar pricing
- ci: update python-doctor badge to 91/100


## [0.1.23] - 2026-04-04

- fix: 12 silent bugs — daemon safety, telegram_send suppression, test hangs
- Suppress '(No message — standing by.)' idle message from Telegram replies
- feat: sync_commands tool to re-register Telegram / menu on demand
- fix: add /stop to /help text
- feat: auto follow-up when Donna makes a promise
- Add RTK rewrite hook to Donna's Bash tool calls


## [0.1.17] - 2026-04-04

- fix: patch rtk hook script to include ~/.local/bin in PATH


## [0.1.16] - 2026-04-04

- feat: rtk token optimizer + strip noise suffix from replies


## [0.1.15] - 2026-04-04




## [0.1.14] - 2026-04-04




## [0.1.13] - 2026-04-04




## [0.1.12] - 2026-04-04

- fix: strip hallucinated Human: turn continuations from responses


## [0.1.11] - 2026-04-04

- fix: add Python version classifiers for PyPI badge


## [0.1.10] - 2026-04-04

- fix: replace send_message_draft with edit-based preview — no replay animation


## [0.1.9] - 2026-04-04

- fix: suppress duplicate reply when telegram_send already used in turn


## [0.1.8] - 2026-04-03

- fix: filter 'No response requested.' as tool noise
- fix: make release auto-bumps patch and pushes, no V= required
- fix: add interrupt_on_message to test_to_dict expected keys


## [0.1.7] - 2026-04-03

- fix: don't pop active_runs or drain followups if superseded by interrupt
- add interrupt_on_message: cancel active run on new message instead of queuing
- simplify self_update: check remote version, pip install from git
- fix self_update to git pull + pip install for local repo checkouts


## [0.1.5] - 2026-03-31

- fix: queue reactions when agent is busy, notify on reaction errors


## [0.1.4] - 2026-03-29

- feat: detect and retry lazy ignorance claims


## [0.1.3] - 2026-03-29

- fix: combine all queued messages instead of dropping earlier ones
- test: audit — delete 9 bullshit tests, add 12 for uncovered flows
- ci: update python-doctor badge to 92/100


## [0.1.2] - 2026-03-28

- perf: dead code removal, regex precompilation, lazy file reads


## [0.1.1] - 2026-03-28

- feat: /start shows user ID to all users for easy setup
- docs: update README with pip install, PyPI/CI/Python badges
- Initial release of IndieClaw v0.1.0


## [0.1.0] - 2026-03-28

- Initial release as IndieClaw
- Self-hosted personal AI agent with Telegram integration
- Cron scheduler with configurable delivery
- Skills system with progressive disclosure
- Subconscious reflection engine
- Dynamic tool loading
- Browser automation
- Streaming responses
- fix: respect deliver_to: '' in cron jobs to prevent double delivery
