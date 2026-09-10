#!/usr/bin/env python3
"""Create and safely inspect evidence bundle ZIP files."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from validate_bundle import validate_bundle

GENERATED_EXCLUDES = {"READY.json", "integrity/files.sha256", "integrity/validation-report.json"}
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
            normalized = posix.as_posix()
            collision_key = normalized.casefold()
            if collision_key in seen:
                raise ValueError(f"Duplicate ZIP member: {info.filename}")
            seen.add(collision_key)
            if info.file_size > 128 * 1024 * 1024:
                raise ValueError(f"ZIP member exceeds 128 MiB: {info.filename}")
            if info.file_size > 1024 * 1024 and info.compress_size > 0 and info.file_size / info.compress_size > 200:
                raise ValueError(f"ZIP member compression ratio is suspicious: {info.filename}")
            total_size += info.file_size
            if total_size > 512 * 1024 * 1024:
                raise ValueError("ZIP expands beyond the 512 MiB safety limit")
        zf.extractall(destination)
    return destination


def _write_checksums(root: Path) -> None:
    target = root / "integrity" / "files.sha256"
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if relative in GENERATED_EXCLUDES:
            continue
        lines.append(f"{sha256(path)}  {relative}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def package_bundle(bundle_dir: str | Path, output_zip: str | Path) -> dict:
    root = Path(bundle_dir).resolve()
    output = Path(output_zip).resolve()
    partial = output.with_suffix(output.suffix + ".partial")
    try:
        output.relative_to(root)
        raise ValueError("Output ZIP must be outside the bundle directory")
    except ValueError as exc:
        if str(exc) == "Output ZIP must be outside the bundle directory":
            raise
    if output.exists() or output.with_suffix(output.suffix + ".sha256").exists() or partial.exists():
        raise FileExistsError(f"Output already exists: {output}")
    symlinks = [path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_symlink()]
    if symlinks:
        raise ValueError(f"Bundle contains symbolic links: {symlinks}")
    report = validate_bundle(root, require_seal=False)
    if report["result"] == "fail":
        raise ValueError(json.dumps(report, ensure_ascii=False))
    (root / "integrity").mkdir(parents=True, exist_ok=True)
    (root / "integrity" / "validation-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "READY.json").write_text(json.dumps({"schema_version": "1.0.0", "package_id": json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))["package_id"], "status": report["derived_status"], "counts": report["counts"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_checksums(root)

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix()):
            zf.write(path, path.relative_to(root).as_posix())
    with tempfile.TemporaryDirectory() as tmp:
        extracted = safe_extract_zip(partial, Path(tmp) / "bundle")
        unpacked_report = validate_bundle(extracted, require_seal=True)
        if unpacked_report["result"] == "fail":
            partial.unlink(missing_ok=True)
            raise ValueError(json.dumps(unpacked_report, ensure_ascii=False))
    partial.replace(output)
    digest = sha256(output)
    output.with_suffix(output.suffix + ".sha256").write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    return {"zip": str(output), "sha256": digest, "status": report["derived_status"], "counts": report["counts"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_dir", type=Path)
    parser.add_argument("output_zip", type=Path)
    args = parser.parse_args(argv)
    try:
        result = package_bundle(args.bundle_dir, args.output_zip)
    except (ValueError, FileExistsError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ready_for_form" else 2


if __name__ == "__main__":
    sys.exit(main())
