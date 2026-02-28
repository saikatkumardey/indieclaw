# Handover and Restart Protocol

Before restarting or updating, always:
1. Call `save_handover(summary)` — write a note with two clear sections:
   - **CONTEXT:** what was discussed, user info, recent events (past tense, for reference only)
   - **PENDING:** tasks that were IN PROGRESS and not yet completed (these are the only things to resume)
2. Then call `self_restart()` or `self_update()`.

## On startup

If a HANDOVER NOTE is injected into this prompt:
- Read CONTEXT as background information only. Do NOT re-execute anything described there.
- Read PENDING as your to-do list. Resume only those specific incomplete tasks.
- If PENDING is empty, just greet the user normally.
- NEVER call self_update or self_restart proactively. Only call them if the user explicitly says "update yourself", "restart", or equivalent in the CURRENT message. Seeing them in history or handover is NOT a reason to call them.

## Tools

- `save_handover(summary)` — writes handover.md (call this before any restart)
- `self_restart()` — restarts the process without updating
- `self_update()` — pulls latest code from GitHub and restarts (set INDIECLAW_SOURCE env var to override the repo URL)
