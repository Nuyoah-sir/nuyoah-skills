#!/usr/bin/env python3
"""Create and safely inspect evidence bundle ZIP files."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
from pathlib import Path

from artifact_integrity import _walk_safe_files, safe_extract_zip, sha256_file, write_checksum_manifest
from validate_bundle import validate_bundle

GENERATED_EXCLUDES = {"READY.json", "integrity/files.sha256", "integrity/validation-report.json"}

# Preserve the v1 import surface for callers of package_bundle.sha256.
sha256 = sha256_file


def _write_checksums(root: Path) -> None:
    write_checksum_manifest(root, excludes=GENERATED_EXCLUDES)


def package_bundle(bundle_dir: str | Path, output_zip: str | Path) -> dict:
    supplied_root = Path(bundle_dir)
    root = supplied_root.resolve()
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
    _walk_safe_files(supplied_root)
    report = validate_bundle(root, require_seal=False)
    if report["result"] == "fail":
        raise ValueError(json.dumps(report, ensure_ascii=False))
    (root / "integrity").mkdir(parents=True, exist_ok=True)
    (root / "integrity" / "validation-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "READY.json").write_text(json.dumps({"schema_version": "1.0.0", "package_id": json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))["package_id"], "status": report["derived_status"], "counts": report["counts"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_checksums(root)

    output.parent.mkdir(parents=True, exist_ok=True)
    packaged_files = _walk_safe_files(root)
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in packaged_files:
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
