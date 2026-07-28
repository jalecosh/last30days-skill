#!/usr/bin/env python3
"""Manage the single versioned global Reddit blocklist."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_SCRIPTS = REPO_ROOT / "skills" / "last30days" / "scripts"
BLOCKLIST_PATH = REPO_ROOT / "config" / "reddit" / "blocklist.json"
sys.path.insert(0, str(ENGINE_SCRIPTS))
from lib import reddit_policy  # noqa: E402


def _read() -> tuple[int, list[str]]:
    version, blocks = reddit_policy.load_blocklist(BLOCKLIST_PATH)
    return version, sorted(blocks)


def _write(version: int, blocks: list[str]) -> None:
    BLOCKLIST_PATH.write_text(
        json.dumps({"version": version, "blocked_subreddits": blocks}, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the global Reddit blocklist.")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("add-block", "remove-block"):
        child = commands.add_parser(command)
        child.add_argument("subreddit")
    commands.add_parser("list-blocks")
    args = parser.parse_args(argv)
    try:
        version, blocks = _read()
        if args.command == "list-blocks":
            print("\n".join(blocks))
            return 0
        name = reddit_policy.normalize_subreddit(args.subreddit)
        if args.command == "add-block":
            if name in blocks:
                raise ValueError(f"already blocked: {name}")
            _write(version, sorted([*blocks, name]))
            return 0
        if name not in blocks:
            raise ValueError(f"not blocked: {name}")
        blocks.remove(name)
        _write(version, blocks)
        return 0
    except ValueError as exc:
        parser.exit(2, f"reddit_policy: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
