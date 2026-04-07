#!/usr/bin/env python3
"""
Bootstrap MemPalace from Donna's existing session logs.

Usage: uv run python scripts/bootstrap_mempalace.py [--dry-run] [--limit N] [--reset]

1. Converts ~/.indieclaw/sessions/*.jsonl to transcript format
2. Mines transcripts into wing "donna" with general extract mode
3. Idempotent — skips already-filed source files
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

import chromadb
from mempalace.config import MempalaceConfig
from mempalace.convo_miner import mine_convos


def convert_session_to_transcript(jsonl_path: Path) -> str:
    """Convert indieclaw session JSONL to MemPalace transcript format.

    Indieclaw format: {"ts": "...", "chat_id": "...", "role": "user|assistant|result", "content": "..."}
    MemPalace format: "> user message\\nassistant response\\n"
    """
    lines = []
    for raw_line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue

        role = entry.get("role", "")
        content = entry.get("content", "")

        # Skip result entries (token counts, not conversation)
        if role == "result":
            continue
        # Skip non-string content
        if not isinstance(content, str) or not content.strip():
            continue
        # Strip telegram metadata prefixes like "[chat_id=123 message_id=456]\n"
        text = content.strip()
        if text.startswith("[chat_id="):
            newline_idx = text.find("\n")
            if newline_idx != -1:
                text = text[newline_idx + 1:].strip()

        if not text:
            continue

        if role == "user":
            lines.append(f"> {text}")
            lines.append("")
        elif role == "assistant":
            lines.append(text)
            lines.append("")

    return "\n".join(lines)


def reset_palace(config: MempalaceConfig):
    """Delete all drawers in wing 'donna' to allow re-mining."""
    try:
        client = chromadb.PersistentClient(path=config.palace_path)
        col = client.get_collection(config.collection_name)
    except Exception:
        return 0

    results = col.get(where={"wing": "donna"}, include=[])
    ids = results["ids"]
    if ids:
        col.delete(ids=ids)
    return len(ids)


def main():
    parser = argparse.ArgumentParser(description="Bootstrap MemPalace from Donna's session logs")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be mined without filing")
    parser.add_argument("--limit", type=int, default=0, help="Max files to process (0 = all)")
    parser.add_argument("--reset", action="store_true", help="Delete existing donna wing before re-mining")
    args = parser.parse_args()

    sessions_dir = Path.home() / ".indieclaw" / "sessions"
    if not sessions_dir.exists():
        print(f"Error: sessions directory not found: {sessions_dir}")
        sys.exit(1)

    jsonl_files = sorted(sessions_dir.glob("*.jsonl"))
    if not jsonl_files:
        print(f"Error: no .jsonl files in {sessions_dir}")
        sys.exit(1)

    print(f"Found {len(jsonl_files)} session files in {sessions_dir}")

    # Initialize palace
    config = MempalaceConfig()
    config.init()

    # Reset if requested
    if args.reset:
        deleted = reset_palace(config)
        print(f"Reset: deleted {deleted} existing drawers in wing 'donna'")

    # Convert JSONL to transcript format in a temp directory
    with tempfile.TemporaryDirectory(prefix="mempalace_bootstrap_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        converted = 0
        for jsonl_file in jsonl_files:
            transcript = convert_session_to_transcript(jsonl_file)
            if not transcript.strip():
                continue
            # Write as .txt so the miner picks it up
            out_file = tmp_path / f"{jsonl_file.stem}.txt"
            out_file.write_text(transcript, encoding="utf-8")
            converted += 1

        print(f"Converted {converted}/{len(jsonl_files)} sessions to transcript format")

        if args.limit:
            print(f"Limiting to {args.limit} files")

        # Mine the converted transcripts
        mine_convos(
            convo_dir=str(tmp_path),
            palace_path=config.palace_path,
            wing="donna",
            agent="bootstrap",
            limit=args.limit,
            dry_run=args.dry_run,
            extract_mode="general",
        )


if __name__ == "__main__":
    main()
