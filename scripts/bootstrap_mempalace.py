#!/usr/bin/env python3
"""
Bootstrap MemPalace from Donna's existing session logs.

Usage: uv run python scripts/bootstrap_mempalace.py [--dry-run] [--limit N]

1. Initializes palace at ~/.mempalace
2. Mines ~/.indieclaw/sessions/*.jsonl into wing "donna"
3. Uses "general" extract mode (decisions, preferences, milestones, problems)
4. Idempotent — skips already-filed source files
"""

import argparse
import sys
from pathlib import Path

from mempalace.config import MempalaceConfig
from mempalace.convo_miner import mine_convos


def main():
    parser = argparse.ArgumentParser(description="Bootstrap MemPalace from Donna's session logs")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be mined without filing")
    parser.add_argument("--limit", type=int, default=0, help="Max files to process (0 = all)")
    args = parser.parse_args()

    sessions_dir = Path.home() / ".indieclaw" / "sessions"
    if not sessions_dir.exists():
        print(f"Error: sessions directory not found: {sessions_dir}")
        sys.exit(1)

    jsonl_files = list(sessions_dir.glob("*.jsonl"))
    if not jsonl_files:
        print(f"Error: no .jsonl files in {sessions_dir}")
        sys.exit(1)

    print(f"Found {len(jsonl_files)} session files in {sessions_dir}")

    # Initialize palace
    config = MempalaceConfig()
    config_path = config.init()
    print(f"Palace initialized: {config_path}")

    # Mine sessions
    mine_convos(
        convo_dir=str(sessions_dir),
        palace_path=config.palace_path,
        wing="donna",
        agent="bootstrap",
        limit=args.limit,
        dry_run=args.dry_run,
        extract_mode="general",
    )


if __name__ == "__main__":
    main()
