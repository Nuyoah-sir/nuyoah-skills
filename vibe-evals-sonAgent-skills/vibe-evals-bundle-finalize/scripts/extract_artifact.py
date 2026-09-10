#!/usr/bin/env python3
"""Verify a ZIP sidecar hash, safely extract it, and validate the sealed artifact."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from package_bundle import safe_extract_zip, sha256
from validate_bundle import validate_bundle
from validate_delta import validate_delta


def _verify_sidecar(archive: Path, sidecar: Path) -> None:
    lines = [line.strip() for line in sidecar.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("SHA-256 sidecar must contain exactly one non-empty line")
    match = re.fullmatch(r"([0-9a-f]{64})  (.+)", lines[0])
    if not match or match.group(2) != archive.name:
        raise ValueError("SHA-256 sidecar format or filename does not match the ZIP")
    actual = sha256(archive)
    if actual != match.group(1):
        raise ValueError(f"ZIP SHA-256 mismatch: expected {match.group(1)}, actual {actual}")


def extract_artifact(kind: str, archive_path: str | Path, sidecar_path: str | Path, destination: str | Path, base_dir: str | Path | None = None, requests_path: str | Path | None = None) -> dict:
    archive = Path(archive_path).resolve()
    sidecar = Path(sidecar_path).resolve()
    destination_path = Path(destination).resolve()
    if destination_path.exists():
        raise FileExistsError(f"Destination already exists: {destination_path}")
    _verify_sidecar(archive, sidecar)
    extracted = safe_extract_zip(archive, destination_path)
    if kind == "bundle":
        report = validate_bundle(extracted, require_seal=True)
    elif kind == "delta":
        if base_dir is None or requests_path is None:
            raise ValueError("--base and --requests are required when kind=delta")
        report = validate_delta(extracted, Path(base_dir).resolve(), Path(requests_path).resolve(), require_seal=True)
    else:
        raise ValueError(f"Unsupported artifact kind: {kind}")
    if report.get("result") != "pass":
        raise ValueError("Extracted artifact failed validation: " + json.dumps(report, ensure_ascii=False))
    return {"kind": kind, "extracted": str(extracted), "sha256": sha256(archive), "validation": report}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("bundle", "delta"))
    parser.add_argument("archive", type=Path)
    parser.add_argument("sidecar", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--base", type=Path)
    parser.add_argument("--requests", type=Path)
    args = parser.parse_args(argv)
    try:
        result = extract_artifact(args.kind, args.archive, args.sidecar, args.destination, args.base, args.requests)
    except (ValueError, FileExistsError, FileNotFoundError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
