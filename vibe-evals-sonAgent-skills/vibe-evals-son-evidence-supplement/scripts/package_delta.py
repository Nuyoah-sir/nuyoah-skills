#!/usr/bin/env python3
"""Seal and package a Vibe Evals evidence delta."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from validate_delta import validate_delta

EXCLUDES = {"DELTA-READY.json", "integrity/files.sha256", "integrity/validation-report.json"}
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_extract_zip(archive: str | Path, destination: str | Path) -> Path:
    archive = Path(archive)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        if len(infos) > 10000:
            raise ValueError("ZIP contains more than 10,000 members")
        seen: set[str] = set()
        total_size = 0
        for info in infos:
            posix = PurePosixPath(info.filename.replace("\\", "/"))
            is_symlink = ((info.external_attr >> 16) & 0o170000) == 0o120000
            unsafe_segment = any(":" in part or part.rstrip(" .") != part or part.split(".", 1)[0].upper() in WINDOWS_RESERVED for part in posix.parts)
            if posix.is_absolute() or ".." in posix.parts or not posix.parts or unsafe_segment or is_symlink:
                raise ValueError(f"Unsafe ZIP member: {info.filename}")
            normalized = posix.as_posix().casefold()
            if normalized in seen:
                raise ValueError(f"Duplicate ZIP member: {info.filename}")
            seen.add(normalized)
            if info.file_size > 128 * 1024 * 1024:
                raise ValueError(f"ZIP member exceeds 128 MiB: {info.filename}")
            if info.file_size > 1024 * 1024 and info.compress_size > 0 and info.file_size / info.compress_size > 200:
                raise ValueError(f"ZIP member compression ratio is suspicious: {info.filename}")
            total_size += info.file_size
            if total_size > 512 * 1024 * 1024:
                raise ValueError("ZIP expands beyond the 512 MiB safety limit")
        zf.extractall(destination)
    return destination


def package_delta(delta_dir: str | Path, base_dir: str | Path, requests_path: str | Path, output_zip: str | Path) -> dict:
    delta = Path(delta_dir).resolve()
    base = Path(base_dir).resolve()
    output = Path(output_zip).resolve()
    partial = output.with_suffix(output.suffix + ".partial")
    try:
        output.relative_to(delta)
        raise ValueError("Output ZIP must be outside the delta directory")
    except ValueError as exc:
        if str(exc) == "Output ZIP must be outside the delta directory":
            raise
    if output.exists() or output.with_suffix(output.suffix + ".sha256").exists() or partial.exists():
        raise FileExistsError(f"Output already exists: {output}")
    symlinks = [path.relative_to(delta).as_posix() for path in delta.rglob("*") if path.is_symlink()]
    if symlinks:
        raise ValueError(f"Delta contains symbolic links: {symlinks}")
    requests = Path(requests_path).resolve()
    report = validate_delta(delta, base, requests, require_seal=False)
    if report["result"] != "pass":
        raise ValueError(json.dumps(report, ensure_ascii=False))
    meta = json.loads((delta / "DELTA.json").read_text(encoding="utf-8"))
    (delta / "integrity").mkdir(parents=True, exist_ok=True)
    (delta / "integrity/validation-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (delta / "DELTA-READY.json").write_text(json.dumps({"schema_version": "1.0.0", "delta_id": meta["delta_id"], "base_package_id": meta["base_package_id"], "counts": report["counts"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = []
    for path in sorted((p for p in delta.rglob("*") if p.is_file()), key=lambda p: p.relative_to(delta).as_posix()):
        relative = path.relative_to(delta).as_posix()
        if relative not in EXCLUDES:
            lines.append(f"{sha256(path)}  {relative}")
    (delta / "integrity/files.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted((p for p in delta.rglob("*") if p.is_file()), key=lambda p: p.relative_to(delta).as_posix()):
            zf.write(path, path.relative_to(delta).as_posix())
    with tempfile.TemporaryDirectory() as tmp:
        extracted = safe_extract_zip(partial, Path(tmp) / "delta")
        checked = validate_delta(extracted, base, requests, require_seal=True)
        if checked["result"] != "pass":
            partial.unlink(missing_ok=True)
            raise ValueError(json.dumps(checked, ensure_ascii=False))
    partial.replace(output)
    digest = sha256(output)
    output.with_suffix(output.suffix + ".sha256").write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    return {"zip": str(output), "sha256": digest, "counts": report["counts"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("delta_dir", type=Path)
    parser.add_argument("base_dir", type=Path)
    parser.add_argument("requests", type=Path)
    parser.add_argument("output_zip", type=Path)
    args = parser.parse_args(argv)
    try:
        result = package_delta(args.delta_dir, args.base_dir, args.requests, args.output_zip)
    except (ValueError, FileExistsError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
