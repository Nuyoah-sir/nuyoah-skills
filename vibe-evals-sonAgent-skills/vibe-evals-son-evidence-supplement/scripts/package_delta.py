#!/usr/bin/env python3
"""Seal and package a Vibe Evals evidence delta."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
from pathlib import Path

from artifact_integrity import safe_extract_zip, sha256_file, write_checksum_manifest
from validate_delta import validate_delta

EXCLUDES = {"DELTA-READY.json", "integrity/files.sha256", "integrity/validation-report.json"}

sha256 = sha256_file


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
    write_checksum_manifest(delta, delta / "integrity/files.sha256", excludes=EXCLUDES)
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
