#!/usr/bin/env python3
"""Write or verify a skill's runtime manifest.

The manifest is an explicit per-skill file list with SHA-256 digests. Verification
rejects both a listed file that is missing and a runtime file that is not listed,
so a release can never ship an unlisted script by accident.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

MANIFEST_NAME = "runtime-manifest.sha256"
IGNORED_PARTS = {"__pycache__"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


def runtime_files(skill_dir: str | Path) -> list[Path]:
    root = Path(skill_dir)
    if not root.is_dir():
        raise ValueError(f"Skill directory not found: {root}")
    files = [
        path for path in root.rglob("*")
        if path.is_file()
        and not (set(path.relative_to(root).parts) & IGNORED_PARTS)
        and path.suffix not in IGNORED_SUFFIXES
        and path.name != MANIFEST_NAME
    ]
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(skill_dir: str | Path) -> Path:
    root = Path(skill_dir)
    rows = [f"{_digest(path)}  {path.relative_to(root).as_posix()}" for path in runtime_files(root)]
    target = root / MANIFEST_NAME
    target.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return target


def verify_manifest(skill_dir: str | Path) -> list[dict[str, str]]:
    """Return the list of problems; an empty list means the manifest is exact."""

    root = Path(skill_dir)
    manifest = root / MANIFEST_NAME
    problems: list[dict[str, str]] = []
    if not manifest.is_file():
        return [{"code": "MANIFEST_MISSING", "message": f"{MANIFEST_NAME} is required", "path": MANIFEST_NAME}]
    listed: dict[str, str] = {}
    for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            problems.append({"code": "MANIFEST_LINE_INVALID", "message": f"Invalid manifest line {number}", "path": MANIFEST_NAME})
            continue
        listed[parts[1]] = parts[0].lower()
    actual = {path.relative_to(root).as_posix(): _digest(path) for path in runtime_files(root)}
    for relative in sorted(set(actual) - set(listed)):
        problems.append({"code": "MANIFEST_UNLISTED_FILE", "message": "Runtime file is not listed in the manifest", "path": relative})
    for relative in sorted(set(listed) - set(actual)):
        problems.append({"code": "MANIFEST_FILE_MISSING", "message": "Listed runtime file is missing", "path": relative})
    for relative in sorted(set(listed) & set(actual)):
        if listed[relative] != actual[relative]:
            problems.append({"code": "MANIFEST_HASH_MISMATCH", "message": "Runtime file differs from its manifest digest", "path": relative})
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("write", "verify"))
    parser.add_argument("skill_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.mode == "write":
            path = write_manifest(args.skill_dir)
            print(f"wrote {path}")
            return 0
        problems = verify_manifest(args.skill_dir)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    if problems:
        for row in problems:
            print(f"{row['code']}: {row['message']}: {row['path']}", file=sys.stderr)
        return 3
    print("manifest ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["MANIFEST_NAME", "runtime_files", "verify_manifest", "write_manifest"]
