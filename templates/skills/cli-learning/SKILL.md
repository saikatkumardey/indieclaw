# CLI Tool Learning Protocol

You can learn to use any CLI tool by pointing at its GitHub repo. No MCP servers. No connectors. Just CLIs.

When the user says "learn to use <repo>" or "install <url>":

## Step 1 — Clone and inspect

```
Bash("git clone <url> /tmp/<name> --depth 1")
Bash("cat /tmp/<name>/README.md")
Bash("cat /tmp/<name>/README* /tmp/<name>/docs/*.md 2>/dev/null | head -200")
```

## Step 2 — Figure out how to install

Check pyproject.toml / setup.py → use `uv tool install` or `uv pip install`
Check Cargo.toml → use `cargo install`
Check go.mod → use `go install`
Check package.json → use `npm install -g`
Binary releases → download from GitHub releases
When in doubt: `Bash("cd /tmp/<name> && cat pyproject.toml setup.py Makefile 2>/dev/null")`

## Step 3 — Install it

```
Bash("uv tool install /tmp/<name>")   # Python
Bash("cargo install --path /tmp/<name>")  # Rust
```

## Step 4 — Verify and explore

```
Bash("<tool> --help")
Bash("<tool> <subcommand> --help")
```

## Step 5 — Write a skill

Create `skills/<tool-name>/SKILL.md` with:
- What the tool does (1 sentence)
- How to install (exact command)
- Key commands with examples
- Common flags and options
- Any gotchas or prerequisites

## Step 6 — Confirm to the user

Tell them: tool installed, skill written, ready to use.

This works for ANY CLI — Python, Rust, Go, Node, shell scripts. The skill persists across sessions so you never have to re-learn it.
