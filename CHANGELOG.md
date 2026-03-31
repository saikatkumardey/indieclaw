# Changelog

All notable changes to IndieClaw will be documented in this file.

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
