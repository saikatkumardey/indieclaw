# Changelog

All notable changes to IndieClaw will be documented in this file.

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
