#!/usr/bin/env python3
"""Validate the repo/cog info.json files against what Red-Index reads.

Mirrors Cog-Creators/Red-Index `indexer.py`: the repo needs a parseable
info.json, and every cog directory containing an info.json must also contain
an __init__.py or the indexer flags it as an invalid cog package.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_KEYS = ("author", "description", "short", "install_msg")
COG_KEYS = (
    "author",
    "description",
    "short",
    "end_user_data_statement",
    "min_bot_version",
    "tags",
    "type",
)

errors = []


def load(path):
    try:
        return json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{path}: unreadable JSON ({exc})")
        return None


repo_info = load(ROOT / "info.json")
if repo_info is not None:
    for key in REPO_KEYS:
        if not repo_info.get(key):
            errors.append(f"info.json: missing/empty '{key}'")
    if not isinstance(repo_info.get("author"), list):
        errors.append("info.json: 'author' must be a list")

cogs = 0
for d in sorted(p for p in ROOT.iterdir() if p.is_dir() and not p.name.startswith(".")):
    info = d / "info.json"
    if not info.is_file():
        continue
    cogs += 1
    if not (d / "__init__.py").is_file():
        errors.append(f"{d.name}: info.json present but no __init__.py (invalid cog package)")
    data = load(info)
    if data is None:
        continue
    for key in COG_KEYS:
        if data.get(key) in (None, "", [], {}):
            errors.append(f"{d.name}/info.json: missing/empty '{key}'")
    if data.get("type") != "COG":
        errors.append(f"{d.name}/info.json: 'type' should be 'COG'")
    if not isinstance(data.get("tags"), list) or not data["tags"]:
        errors.append(f"{d.name}/info.json: 'tags' must be a non-empty list")
    if not isinstance(data.get("min_python_version"), list):
        errors.append(f"{d.name}/info.json: 'min_python_version' must be a list")

if not cogs:
    errors.append("no cog directories with an info.json were found")

for e in errors:
    print(f"ERROR: {e}")
print(f"checked {cogs} cog(s); {len(errors)} error(s)")
sys.exit(1 if errors else 0)
