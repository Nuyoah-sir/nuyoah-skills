#!/usr/bin/env python3
"""Re-run discovery and compare the complete source inventory digest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from artifact_integrity import _walk_safe_files, sha256_file
from discover_task_package import discover_task_package


def verify_frozen_source(task_root: str | Path, freeze: dict) -> dict:
    """Compare the live task root against the frozen inventory, byte for byte.

    The freeze is the inventory the exporting machine recorded; a form-ready
    package may only be sealed while the live source still matches it exactly,
    including files that appeared or disappeared afterwards.
    """

    root = Path(task_root)
    rows = freeze.get("source_inventory") if isinstance(freeze, dict) else None
    if not isinstance(rows, list):
        raise ValueError("SOURCE_FREEZE_INVALID: source_inventory must be a list")
    before = {row["path"]: row["sha256"] for row in rows if isinstance(row, dict) and isinstance(row.get("path"), str)}
    if len(before) != len(rows):
        raise ValueError("SOURCE_FREEZE_INVALID: every inventory row needs a path and digest")
    try:
        after = {file.relative_to(root).as_posix(): sha256_file(file) for file in _walk_safe_files(root)}
    except (OSError, ValueError) as exc:
        return {"status": "source_unreadable", "detail": str(exc)}
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(path for path in set(before) & set(after) if before[path] != after[path])
    if added or removed or changed:
        return {"status": "source_changed", "added": added, "removed": removed, "changed": changed}
    return {"status": "unchanged", "input_digest": freeze.get("input_digest"), "files": len(before)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_root", type=Path)
    parser.add_argument("source_freeze", type=Path)
    args = parser.parse_args(argv)
    frozen = json.loads(args.source_freeze.read_text(encoding="utf-8"))
    current = discover_task_package(args.task_root)
    if not current.get("ok"):
        print(json.dumps(current, ensure_ascii=False, indent=2))
        return 3
    if current["input_digest"] != frozen.get("input_digest"):
        before = {item["path"]: item["sha256"] for item in frozen.get("source_inventory", [])}
        after = {item["path"]: item["sha256"] for item in current.get("source_inventory", [])}
        result = {"status": "source_changed", "added": sorted(set(after)-set(before)), "removed": sorted(set(before)-set(after)), "changed": sorted(path for path in set(before)&set(after) if before[path] != after[path])}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 4
    print(json.dumps({"status": "unchanged", "input_digest": current["input_digest"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
