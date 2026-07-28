"""Versioned global Reddit filtering and preference policy.

The policy is intentionally separate from retrieval: it is applied only after
global discovery returns candidates, so it cannot narrow the discovery lane.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


BLOCKLIST_ENV_VAR = "LAST30DAYS_REDDIT_BLOCKLIST_PATH"
PREFERENCES_ENV_VAR = "LAST30DAYS_REDDIT_PREFERENCES_PATH"
SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]{1,21}$")


@dataclass(frozen=True)
class Policy:
    blocklist_version: int | None
    blocklist_path: str | None
    blocked_subreddits: frozenset[str]
    preferences_version: int | None
    preferences_path: str | None
    preferred_subreddits: dict[str, float]


def normalize_subreddit(value: object) -> str:
    name = str(value or "").strip()
    if name[:2].lower() == "r/":
        name = name[2:].strip()
    if not SUBREDDIT_RE.fullmatch(name):
        raise ValueError(f"invalid subreddit name: {value!r}")
    return name.lower()


def _read_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"missing Reddit policy file: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Reddit policy JSON in {path}: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Reddit policy {path} must be an object")
    return data


def load_blocklist(path: Path) -> tuple[int, frozenset[str]]:
    data = _read_object(path)
    if set(data) != {"version", "blocked_subreddits"} or not isinstance(data["version"], int) or data["version"] < 1:
        raise ValueError(f"invalid blocklist schema: {path}")
    values = data["blocked_subreddits"]
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ValueError(f"{path}: blocked_subreddits must be a list of strings")
    normalized = [normalize_subreddit(item) for item in values]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{path}: blocked_subreddits contains duplicate names")
    return data["version"], frozenset(normalized)


def load_preferences(path: Path) -> tuple[int, dict[str, float]]:
    data = _read_object(path)
    if set(data) != {"version", "preferred_subreddits"} or not isinstance(data["version"], int) or data["version"] < 1:
        raise ValueError(f"invalid preferences schema: {path}")
    values = data["preferred_subreddits"]
    if not isinstance(values, dict):
        raise ValueError(f"{path}: preferred_subreddits must be an object")
    normalized: dict[str, float] = {}
    for name, weight in values.items():
        subreddit = normalize_subreddit(name)
        if subreddit in normalized:
            raise ValueError(f"{path}: preferred_subreddits contains duplicate names")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 < float(weight) <= 2:
            raise ValueError(f"{path}: preference weight for {name!r} must be a number in (0, 2]")
        normalized[subreddit] = float(weight)
    return data["version"], normalized


def load_policy(blocklist_path: Path | None = None, preferences_path: Path | None = None) -> Policy:
    block_version, blocked = (None, frozenset()) if blocklist_path is None else load_blocklist(blocklist_path)
    pref_version, preferred = (None, {}) if preferences_path is None else load_preferences(preferences_path)
    if blocked & set(preferred):
        raise ValueError("a subreddit cannot be both blocked and preferred")
    return Policy(block_version, str(blocklist_path) if blocklist_path else None, blocked, pref_version, str(preferences_path) if preferences_path else None, preferred)


def policy_from_environment() -> Policy:
    blocklist = os.environ.get(BLOCKLIST_ENV_VAR)
    preferences = os.environ.get(PREFERENCES_ENV_VAR)
    return load_policy(Path(blocklist) if blocklist else None, Path(preferences) if preferences else None)
