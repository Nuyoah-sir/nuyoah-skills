#!/usr/bin/env python3
"""Refresh every skill's runtime mirrors from shared/ and rebuild its manifest.

Only files that already exist in a skill's scripts directory are refreshed, so a
skill never silently gains a script it was not designed to ship.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from build_runtime_manifest import write_manifest

SKILLS = (
    "vibe-evals-son-evidence-export",
    "vibe-evals-son-evidence-supplement",
    "vibe-evals-son-complete-eval",
    "vibe-evals-bundle-finalize",
)
# Files that only the repository ships (release tooling, tests) never get mirrored.
NOT_MIRRORED = {"build_runtime_manifest.py", "sync_runtime_mirrors.py", "validate_skill_layout.py"}


def sync(package_root: str | Path) -> list[dict[str, str]]:
    root = Path(package_root)
    shared = root / "shared" / "scripts"
    results: list[dict[str, str]] = []
    for skill in SKILLS:
        scripts = root / skill / "scripts"
        if not scripts.is_dir():
            continue
        for target in sorted(scripts.glob("*.py")):
            if target.name in NOT_MIRRORED:
                continue
            source = shared / target.name
            if not source.is_file():
                results.append({"action": "unmatched", "skill": skill, "file": target.name})
                continue
            changed = not target.is_file() or target.read_bytes() != source.read_bytes()
            if changed:
                shutil.copyfile(source, target)
                results.append({"action": "refreshed", "skill": skill, "file": target.name})
        write_manifest(root / skill)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package_root", type=Path, nargs="?", default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    try:
        results = sync(args.package_root)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    for row in results:
        print(f"{row['action']}: {row['skill']}/scripts/{row['file']}")
    print(f"mirrors synced: {sum(1 for row in results if row['action'] == 'refreshed')} refreshed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["SKILLS", "sync"]
