#!/usr/bin/env python3
"""Re-run discovery and compare the complete source inventory digest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from discover_task_package import discover_task_package


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
