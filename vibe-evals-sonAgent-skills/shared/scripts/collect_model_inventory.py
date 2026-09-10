#!/usr/bin/env python3
"""Collect deterministic final/round file inventories without executing model code."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _files(root: Path) -> list[dict]:
    if not root.is_dir():
        raise ValueError(f"Source directory does not exist: {root}")
    links = [path for path in root.rglob("*") if path.is_symlink()]
    if links:
        raise ValueError(f"Source contains symbolic links: {[str(path) for path in links]}")
    return [
        {"path": path.relative_to(root).as_posix(), "size": path.stat().st_size, "sha256": _hash(path)}
        for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.relative_to(root).as_posix())
    ]


def collect_model_inventory(final_dir: str | Path, rounds: list[tuple], output_path: str | Path, task_root: str | Path | None = None, model_metadata: str | Path | None = None) -> dict:
    final = Path(final_dir).resolve()
    output = Path(output_path).resolve()
    try:
        output.relative_to(final)
        raise ValueError("Output must not be inside the source directory")
    except ValueError as exc:
        if str(exc) == "Output must not be inside the source directory":
            raise
    seen: set[int] = set()
    root = Path(task_root).resolve() if task_root else None
    metadata = json.loads(Path(model_metadata).read_text(encoding="utf-8")) if model_metadata else None
    expected_rounds = metadata.get("round_source_labels", {}) if isinstance(metadata, dict) else {}
    groups = []
    for entry in rounds:
        if len(entry) not in (2, 3):
            raise ValueError("Round entries must be (number, path) or (number, path, source_label)")
        number, raw_path = entry[0], entry[1]
        if not isinstance(number, int) or number < 1 or number in seen:
            raise ValueError(f"Invalid or duplicate round: {number!r}")
        seen.add(number)
        source = Path(raw_path).resolve()
        if len(entry) == 3:
            label = str(entry[2]).replace("\\", "/").strip("/")
        elif root:
            try:
                label = source.relative_to(root).as_posix()
            except ValueError as exc:
                raise ValueError(f"Round source is outside task_root: {source}") from exc
        else:
            label = source.name
        if metadata is not None and expected_rounds.get(str(number)) != label:
            raise ValueError(f"Round {number} source {label!r} does not match frozen model binding {expected_rounds.get(str(number))!r}")
        groups.append({"round": number, "source_root_label": label, "files": _files(source)})
    result = {"final": {"files": _files(final)}, "rounds": sorted(groups, key=lambda item: item["round"])}
    if output.exists():
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise FileExistsError(f"Output already exists and is not an initialized inventory skeleton: {output}") from exc
        if set(existing) != {"final", "rounds"} or existing.get("rounds") != [] or existing.get("final") != result["final"]:
            raise FileExistsError(f"Output already exists and is not the matching initialized inventory skeleton: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("final_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--task-root", type=Path)
    parser.add_argument("--model-metadata", type=Path, help="Frozen per-model source binding in model.json")
    parser.add_argument("--round", action="append", default=[], metavar="N=PATH")
    args = parser.parse_args(argv)
    parsed = []
    try:
        for value in args.round:
            number, path = value.split("=", 1)
            parsed.append((int(number), Path(path)))
        collect_model_inventory(args.final_dir, parsed, args.output, args.task_root, args.model_metadata)
    except (ValueError, FileExistsError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
