#!/usr/bin/env python3
"""Manage the one global Reddit blocklist."""
import argparse, json
from pathlib import Path
PATH = Path(__file__).resolve().parents[1] / "config" / "reddit_blocklist.json"
def clean(value):
    value=value.strip()
    if value[:2].lower()=="r/": value=value[2:]
    if not value or not all(c.isalnum() or c=="_" for c in value): raise ValueError("invalid subreddit")
    return value
def read():
    return json.loads(PATH.read_text(encoding="utf-8")).get("subreddits", []) if PATH.exists() else []
def write(values):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps({"subreddits": sorted({v.casefold():v for v in values}.values(), key=str.casefold)}, indent=2)+"\n",encoding="utf-8")
def main(argv=None):
    p=argparse.ArgumentParser(); s=p.add_subparsers(dest="cmd",required=True)
    for x in ("add","remove"): s.add_parser(x).add_argument("subreddit")
    s.add_parser("list"); a=p.parse_args(argv); values=read()
    if a.cmd=="list": print("\n".join(sorted(values,key=str.casefold))); return 0
    name=clean(a.subreddit)
    values = values + [name] if a.cmd=="add" and name.casefold() not in {x.casefold() for x in values} else [x for x in values if x.casefold()!=name.casefold()] if a.cmd=="remove" else values
    write(values); return 0
if __name__=="__main__": raise SystemExit(main())