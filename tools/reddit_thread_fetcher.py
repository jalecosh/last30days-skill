#!/usr/bin/env python3
"""Fetch a complete Reddit comment tree through ScrapeCreators.

This standalone utility deliberately does not import or alter last30days.  It
loads only ``SCRAPECREATORS_API_KEY`` from the existing last30days global .env
file and never echoes the key.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


API_URL = "https://api.scrapecreators.com/v1/reddit/post/comments"
CONFIG_PATH = Path(r"C:\Users\jalec\.config\last30days\.env")
MAX_RETRIES = 5
BASE_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 30
COMMENT_FIELDS = (
    "id", "parent_id", "author", "score", "body", "created_utc",
    "created_at_iso", "permalink", "depth", "source_cursor", "replies",
)


def parse_env_value(path: Path, name: str) -> str | None:
    """Read one .env setting without printing its value."""
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        if key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]
        return value or None
    return None


def post_id_from_url(url: str) -> str:
    match = re.search(r"/comments/([A-Za-z0-9_]+)/", urlparse(url).path + "/")
    if not match:
        raise ValueError("URL must be a Reddit post URL containing /comments/<post-id>/")
    return match.group(1)


def fetch_json(post_url: str, cursor: str | None, api_key: str) -> dict[str, Any]:
    """Fetch one page, retrying transient API and connection failures."""
    params = {"url": post_url}
    if cursor:
        params["cursor"] = cursor
    request = Request(
        f"{API_URL}?{urlencode(params)}",
        headers={"x-api-key": api_key, "Accept": "application/json", "User-Agent": "InvestmentBrain-reddit-fetcher/1.0"},
    )

    for attempt in range(MAX_RETRIES):
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                data = json.loads(response.read().decode("utf-8"))
                if not isinstance(data, dict):
                    raise RuntimeError("API returned JSON that was not an object")
                return data
        except HTTPError as exc:
            retryable = exc.code == 429 or exc.code == 403 or 500 <= exc.code <= 599
            if not retryable or attempt == MAX_RETRIES - 1:
                raise RuntimeError(f"HTTP {exc.code}") from exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            delay = float(retry_after) if retry_after and retry_after.isdigit() else BASE_DELAY_SECONDS * (2 ** attempt)
        except (URLError, TimeoutError, OSError) as exc:
            if attempt == MAX_RETRIES - 1:
                raise RuntimeError(f"connection failure: {type(exc).__name__}") from exc
            delay = BASE_DELAY_SECONDS * (2 ** attempt)
        except json.JSONDecodeError as exc:
            raise RuntimeError("API returned invalid JSON") from exc
        time.sleep(min(delay, 30.0))

    raise AssertionError("unreachable")


def comment_nodes(value: Any, nested_depth: int = 0) -> Iterator[tuple[dict[str, Any], int]]:
    """Yield all comments embedded in a ``comments`` list or replies.items tree."""
    if not isinstance(value, list):
        return
    for node in value:
        if not isinstance(node, dict):
            continue
        yield node, nested_depth
        replies = node.get("replies")
        if isinstance(replies, dict):
            yield from comment_nodes(replies.get("items"), nested_depth + 1)


def comment_record(node: dict[str, Any], source_cursor: str | None, fallback_depth: int) -> dict[str, Any]:
    """Keep the requested fields while retaining the vendor's raw replies object."""
    record = {field: node.get(field) for field in COMMENT_FIELDS if field not in {"depth", "source_cursor"}}
    record["source_cursor"] = source_cursor
    source_depth = node.get("depth")
    record["depth"] = source_depth if isinstance(source_depth, int) and source_depth >= 0 else fallback_depth
    return record


def normalized_parent_id(parent_id: Any) -> str:
    value = str(parent_id or "")
    return value[3:] if value.startswith(("t1_", "t3_")) else value


def resolve_depths(comments: dict[str, dict[str, Any]], post_id: str) -> tuple[int, bool]:
    """Set depth from parent IDs; return max depth and parent-resolution status."""
    memo: dict[str, int] = {}
    resolving: set[str] = set()
    all_resolved = True

    def depth(comment_id: str) -> int:
        nonlocal all_resolved
        if comment_id in memo:
            return memo[comment_id]
        if comment_id in resolving:
            all_resolved = False
            return int(comments[comment_id].get("depth") or 0)
        resolving.add(comment_id)
        parent = str(comments[comment_id].get("parent_id") or "")
        if parent == f"t3_{post_id}" or parent == post_id:
            result = 0
        elif parent.startswith("t1_"):
            parent_comment_id = parent[3:]
            if parent_comment_id in comments:
                result = depth(parent_comment_id) + 1
            else:
                all_resolved = False
                result = int(comments[comment_id].get("depth") or 0)
        else:
            all_resolved = False
            result = int(comments[comment_id].get("depth") or 0)
        resolving.discard(comment_id)
        memo[comment_id] = result
        return result

    for comment_id, comment in comments.items():
        comment["depth"] = depth(comment_id)
    return max(memo.values(), default=0), all_resolved


def markdown_tree(comments: dict[str, dict[str, Any]], post_id: str) -> tuple[str, dict[str, Any]]:
    """Render depth-first discussion chains without changing source ordering.

    Each valid top-level Reddit comment gets its own stable chain number.  A
    child is written immediately after its parent's body and before any later
    sibling, making the parent-child relation readable even for long threads.
    """
    def is_deleted(comment: dict[str, Any]) -> bool:
        return (
            str(comment.get("author") or "").strip().lower() == "[deleted]"
            or str(comment.get("body") or "").strip().lower() == "[deleted]"
        )

    def sort_ids(comment_ids: list[str]) -> None:
        comment_ids.sort(
            key=lambda cid: (
                comments[cid].get("created_utc") is None,
                comments[cid].get("created_utc") or 0,
                cid,
            )
        )

    deleted_ids = {cid for cid, comment in comments.items() if is_deleted(comment)}
    valid_ids = set(comments) - deleted_ids
    children: dict[str, list[str]] = {}
    for comment_id in valid_ids:
        parent = normalized_parent_id(comments[comment_id].get("parent_id"))
        children.setdefault(parent, []).append(comment_id)
    for child_ids in children.values():
        sort_ids(child_ids)

    lines: list[str] = []
    rendered: set[str] = set()
    render_order: list[str] = []
    maximum_rendered_depth = 0

    def stamp(comment: dict[str, Any]) -> str:
        value = comment.get("created_at_iso")
        return str(value) if value else str(comment.get("created_utc") or "unknown time")

    def render(comment_id: str, depth: int, ancestor_last: list[bool], is_last: bool, parent_author: str | None) -> None:
        nonlocal maximum_rendered_depth
        if comment_id in rendered:
            return
        rendered.add(comment_id)
        render_order.append(comment_id)
        maximum_rendered_depth = max(maximum_rendered_depth, depth)
        comment = comments[comment_id]
        author = str(comment.get("author") or "[deleted]")
        score = comment.get("score")
        permalink = str(comment.get("permalink") or "")
        link = f"[permalink](https://www.reddit.com{permalink})" if permalink.startswith("/") else "permalink unavailable"
        if depth == 0:
            line_prefix = ""
            content_prefix = ""
        else:
            branch_prefix = "".join("   " if last else "│  " for last in ancestor_last)
            line_prefix = branch_prefix + ("└─ " if is_last else "├─ ")
            content_prefix = branch_prefix + ("   " if is_last else "│  ")
        lines.append(f"{line_prefix}u/{author} · {score if score is not None else 0} upvotes · depth {depth} · {stamp(comment)} · {link}")
        lines.append(f"{content_prefix}<sub>comment_id: {comment_id} · parent_id: {comment.get('parent_id') or ''}</sub>")
        if parent_author is not None:
            lines.append(f"{content_prefix}↳ replying to u/{parent_author}")
        body = str(comment.get("body") or "")
        # Prefix every physical line, including blank ones, so Markdown never
        # resets the visual branch indentation for multiline comments.
        body_lines = body.splitlines() or [""]
        lines.extend(f"{content_prefix}{body_line}" for body_line in body_lines)
        child_ids = children.get(comment_id, [])
        # The root is not itself drawn as a branch, so it contributes no
        # indentation prefix to its immediate replies.
        next_ancestor_last = ancestor_last if depth == 0 else ancestor_last + [is_last]
        for index, child_id in enumerate(child_ids):
            render(child_id, depth + 1, next_ancestor_last, index == len(child_ids) - 1, author)

    root_ids = children.get(post_id, [])
    for chain_number, root_id in enumerate(root_ids, start=1):
        if lines:
            lines.append("---")
        lines.append(f"## Discussion Chain {chain_number}")
        render(root_id, 0, [], True, None)

    # Any non-deleted node not reached from t3_<post-id> has a missing/deleted
    # parent (or a broken parent chain); do not blend it into a valid chain.
    orphan_ids = sorted(valid_ids - rendered, key=lambda cid: (
        comments[cid].get("created_utc") is None,
        comments[cid].get("created_utc") or 0,
        cid,
    ))
    if deleted_ids:
        lines.extend(["", "## Deleted Comments"])
        for comment_id in sorted(deleted_ids):
            comment = comments[comment_id]
            lines.append(f"u/[deleted] · {comment.get('score') or 0} upvotes · depth {comment.get('depth') or 0} · {stamp(comment)}")
            lines.append(f"<sub>comment_id: {comment_id} · parent_id: {comment.get('parent_id') or ''}</sub>")
    if orphan_ids:
        lines.extend(["", "## Orphaned Comments"])
        for comment_id in orphan_ids:
            comment = comments[comment_id]
            permalink = str(comment.get("permalink") or "")
            link = f"[permalink](https://www.reddit.com{permalink})" if permalink.startswith("/") else "permalink unavailable"
            lines.append(f"u/{comment.get('author') or '[unknown]'} · {comment.get('score') or 0} upvotes · depth {comment.get('depth') or 0} · {stamp(comment)} · {link}")
            lines.append(f"<sub>comment_id: {comment_id} · parent_id: {comment.get('parent_id') or ''}</sub>")
            lines.extend(str(comment.get("body") or "").splitlines() or [""])

    positions = {cid: index for index, cid in enumerate(render_order)}
    parent_child_relations_preserved = all(
        normalized_parent_id(comments[cid].get("parent_id")) == post_id
        or (
            normalized_parent_id(comments[cid].get("parent_id")) in positions
            and positions[normalized_parent_id(comments[cid].get("parent_id"))] < positions[cid]
        )
        for cid in render_order
    )
    metadata = {
        "discussion_chains": len(root_ids),
        "maximum_rendered_depth": maximum_rendered_depth,
        "orphan_count": len(orphan_ids),
        "deleted_count": len(deleted_ids),
        "parent_child_relations_preserved": parent_child_relations_preserved,
    }
    return "\n".join(lines) + ("\n" if lines else ""), metadata


def fetch_thread(post_url: str, max_comments: int | None, max_depth: int | None, api_key: str) -> tuple[dict[str, Any], str]:
    post_id = post_id_from_url(post_url)
    queue: deque[str | None] = deque([None])
    visited_cursors: set[str] = set()
    queued_cursors: set[str] = set()
    comments: dict[str, dict[str, Any]] = {}
    duplicates_removed = 0
    failed_cursors: list[str | None] = []
    stop_for_limit = False

    while queue and not stop_for_limit:
        cursor = queue.popleft()
        if cursor is not None:
            if cursor in visited_cursors:
                continue
            visited_cursors.add(cursor)
        try:
            response = fetch_json(post_url, cursor, api_key)
        except RuntimeError as exc:
            failed_cursors.append(cursor)
            print(f"[reddit-thread-fetcher] request failed for cursor {'initial' if cursor is None else '<redacted>'}: {exc}", file=sys.stderr)
            continue

        page_comments = response.get("comments", response.get("data", []))
        for node, structural_depth in comment_nodes(page_comments):
            record = comment_record(node, cursor, structural_depth)
            comment_id = str(record.get("id") or "")
            if not comment_id:
                continue
            if max_depth is not None and int(record["depth"]) > max_depth:
                continue
            if comment_id in comments:
                duplicates_removed += 1
            elif max_comments is not None and len(comments) >= max_comments:
                stop_for_limit = True
                break
            else:
                comments[comment_id] = record

            replies = node.get("replies")
            more = replies.get("more") if isinstance(replies, dict) else None
            next_cursor = more.get("cursor") if isinstance(more, dict) and more.get("has_more") else None
            if next_cursor and next_cursor not in visited_cursors and next_cursor not in queued_cursors:
                queue.append(str(next_cursor))
                queued_cursors.add(str(next_cursor))

    maximum_depth, all_parent_ids_resolved = resolve_depths(comments, post_id)
    tree, tree_metadata = markdown_tree(comments, post_id)
    deleted_comments = sum(1 for item in comments.values() if str(item.get("author") or "").lower() == "[deleted]" or str(item.get("body") or "").lower() == "[deleted]")
    empty_comments = sum(1 for item in comments.values() if not str(item.get("body") or "").strip())
    remaining = len(queue)
    stats = {
        "total_unique_comments": len(comments),
        "top_level_comments": tree_metadata["discussion_chains"],
        "maximum_depth": maximum_depth,
        "cursors_requested": len(visited_cursors),
        "duplicate_comments_removed": duplicates_removed,
        "deleted_comments": deleted_comments,
        "empty_comments": empty_comments,
        "failed_cursors": len(failed_cursors),
        "has_more_cursors_remain": bool(remaining or stop_for_limit or failed_cursors),
        "all_parent_ids_resolved": all_parent_ids_resolved,
        **tree_metadata,
    }
    raw = {
        "post_url": post_url,
        "post_id": post_id,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "comments": list(comments.values()),
        "statistics": stats,
    }
    stats_block = "\n".join(["## Statistics", "", *[f"- {key}: {value}" for key, value in stats.items()], ""])
    return raw, tree + "\n" + stats_block


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch all available comments for one Reddit post through ScrapeCreators.")
    parser.add_argument("url", help="Reddit post URL")
    parser.add_argument("--output-dir", type=Path, default=Path.cwd() / "reddit-comments", help="Directory for JSON and Markdown outputs")
    parser.add_argument("--max-comments", type=int, help="Stop after this many unique comments")
    parser.add_argument("--max-depth", type=int, help="Ignore comments deeper than this reply depth")
    args = parser.parse_args()
    if args.max_comments is not None and args.max_comments < 1:
        parser.error("--max-comments must be at least 1")
    if args.max_depth is not None and args.max_depth < 0:
        parser.error("--max-depth must be zero or greater")

    api_key = os.environ.get("SCRAPECREATORS_API_KEY") or parse_env_value(CONFIG_PATH, "SCRAPECREATORS_API_KEY")
    if not api_key:
        print(f"SCRAPECREATORS_API_KEY was not found in {CONFIG_PATH}", file=sys.stderr)
        return 2
    try:
        raw, markdown = fetch_thread(args.url, args.max_comments, args.max_depth, api_key)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    post_id = raw["post_id"]
    json_path = args.output_dir / f"{post_id}-comments-raw.json"
    markdown_path = args.output_dir / f"{post_id}-comments-tree.md"
    json_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(markdown_path), **raw["statistics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
