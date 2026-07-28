"""Global blocklist management CLI contracts."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "tools" / "reddit_policy.py"
SPEC = importlib.util.spec_from_file_location("reddit_policy_cli", MODULE_PATH)
assert SPEC and SPEC.loader
reddit_policy_cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reddit_policy_cli
SPEC.loader.exec_module(reddit_policy_cli)


def test_add_and_remove_use_the_single_global_blocklist(monkeypatch):
    written = []
    monkeypatch.setattr(reddit_policy_cli, "_read", lambda: (1, ["existingtestforum"]))
    monkeypatch.setattr(reddit_policy_cli, "_write", lambda version, blocks: written.append((version, blocks)))
    assert reddit_policy_cli.main(["add-block", "r/NewTestForum"]) == 0
    assert written == [(1, ["existingtestforum", "newtestforum"])]
    written.clear()
    monkeypatch.setattr(reddit_policy_cli, "_read", lambda: (1, ["newtestforum"]))
    assert reddit_policy_cli.main(["remove-block", "R/NEWTESTFORUM"]) == 0
    assert written == [(1, [])]


def test_add_rejects_duplicate_normalized_names(monkeypatch):
    monkeypatch.setattr(reddit_policy_cli, "_read", lambda: (1, ["duplicatetestforum"]))
    with pytest.raises(SystemExit) as exc:
        reddit_policy_cli.main(["add-block", "r/DuplicateTestForum"])
    assert exc.value.code == 2
