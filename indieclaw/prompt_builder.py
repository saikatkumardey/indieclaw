from __future__ import annotations

from . import workspace
from .skills import list_skills


def _onboarding_block() -> str:
    return f"""
## Onboarding Protocol

If USER.md contains "Not set yet" for the user's name, you are meeting this person for the first time.

Introduce yourself warmly. Tell them you're a personal AI agent, that you don't have a name yet either, and that you'd like to learn about them so you can serve them better. Then ask:
1. What their name is and how they'd like to be addressed
2. Their timezone
3. What they'd like help with (goals, projects, recurring tasks)
4. Any preferences (communication style, things to avoid, etc.)
5. What name they'd like to give you

Once you have enough to go on, write what you've learned using the Write tool with these exact absolute paths:
- {workspace.USER} — their name, how to address them, timezone, preferences, goals
- {workspace.SOUL} — update the Identity section with your new name and emoji
- {workspace.MEMORY} — add a "First session" note with the date and key facts

You don't have to ask all questions at once. Have a natural conversation. But do write what you learn before the session ends — use the absolute paths above, not relative filenames.

After onboarding is complete, you are no longer a blank slate. You have an identity and a user. Act like it.
"""


def _workspace_context() -> str:
    return (
        f"## Workspace\n"
        f"Your workspace directory: {workspace.HOME}\n"
        f"Always use these absolute paths when writing agent data files:\n"
        f"- SOUL.md:    {workspace.SOUL}  (identity + personality)\n"
        f"- AGENT.md:   {workspace.AGENT}  (operational playbook)\n"
        f"- USER.md:    {workspace.USER}\n"
        f"- MEMORY.md:  {workspace.MEMORY}\n"
        f"- crons.yaml: {workspace.CRONS}\n"
        f"- skills/:    {workspace.SKILLS_DIR}/<name>/SKILL.md\n"
        f"- tools/:     {workspace.TOOLS_DIR}/<name>.py\n"
        f"- Config:     {workspace.CONFIG}  (indieclaw.json — runtime settings)\n"
        f"- Session:    {workspace.SESSION_STATE}  (session_state.json — usage tracking)\n"
        f"Never use bare filenames like 'SOUL.md' — always the full path above."
    )


def system_prompt_slim() -> str:
    parts = [_workspace_context()]
    if agent_content := workspace.read(workspace.AGENT):
        parts.append(f"=== AGENT.md ===\n{agent_content.strip()}")
    if memory := workspace.read(workspace.MEMORY):
        parts.append(f"=== MEMORY.md ===\n{memory.strip()}")
    return "\n\n".join(parts)


def system_prompt_full() -> str:
    parts = [_workspace_context()]

    user_content = ""
    for path, name in (
        (workspace.SOUL, "SOUL.md"),
        (workspace.AGENT, "AGENT.md"),
        (workspace.USER, "USER.md"),
    ):
        content = workspace.read(path)
        if path == workspace.USER:
            user_content = content
        if content:
            parts.append(f"=== {name} ===\n{content.strip()}")

    if skills := list_skills(workspace.SKILLS_DIR):
        skill_lines = "\n".join(
            f"• {name} — {desc}" if desc else f"• {name}"
            for name, desc in skills
        )
        parts.append(
            f"=== AVAILABLE SKILLS ===\n"
            f"Use the read_skill tool to load a skill's instructions on demand.\n\n"
            f"{skill_lines}"
        )

    if memory := workspace.read(workspace.MEMORY):
        parts.append(f"=== MEMORY.md ===\n{memory.strip()}")

    if "Not set yet" in user_content:
        parts.append(_onboarding_block())

    parts.append(
        "=== CRITICAL RULE ===\n"
        "NEVER say \"I don't have context\", \"I don't recall\", or \"what are you referring to\" "
        "without FIRST reading today's session log "
        f"({workspace.HOME / 'sessions/'}YYYY-MM-DD.jsonl) and yesterday's log. "
        "Also check MEMORY.md. "
        "If after checking all three you still lack context, say what you checked and ask for clarification. "
        "A bare \"I don't have context\" is NEVER acceptable."
    )

    return "\n\n".join(parts)


def build_system_prompt(slim: bool = False) -> str:
    return system_prompt_slim() if slim else system_prompt_full()
